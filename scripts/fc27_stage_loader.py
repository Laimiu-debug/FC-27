"""生成项目内 ModData 实验副本、原版 CAS 回退清单并离线核对；不部署。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from fc27_build import digest, local_input, strict_json
from fc27_assets import input_path, small_file
from fc27_export import output_path
from fc27_modcontainer import read_mod
from fc27_mount import OfflineMount, audit_mount, header_impact, mount_relative, plain_file
from fc27_verify_package import run as verify_package


MAX_INVENTORY = 10_000
MAX_STAGE_BYTES = 160 * 1024 * 1024


def inventory(game_root: Path) -> list[dict]:
    result, names = [], set()
    metadata_bytes = 0
    for layer in ("Data", "Patch"):
        folder = game_root / layer
        if folder.is_symlink() or folder.is_junction():
            raise ValueError("游戏数据根目录不接受链接")
        # 不递归进入目录链接；发现任何链接即停止本次准备。
        for path in sorted(folder.rglob("*")):
            if path.is_symlink() or path.is_junction():
                raise ValueError("游戏数据包含链接，拒绝隐式回退")
            if not path.is_file():
                continue
            name = mount_relative(path.relative_to(game_root).as_posix())
            if name.casefold() in names:
                raise ValueError("原版回退清单有大小写冲突路径")
            names.add(name.casefold())
            stat = plain_file(game_root, name).stat()
            item = {"path": name, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                    "kind": "cas" if path.suffix.casefold() == ".cas" else "metadata"}
            if item["kind"] == "metadata":
                metadata_bytes += stat.st_size
                if metadata_bytes > MAX_STAGE_BYTES:
                    raise ValueError("原版元数据清单超过 160 MiB 上限")
                item["sha256"] = digest(small_file(path))
            result.append(item)
            if len(result) > MAX_INVENTORY:
                raise ValueError("原版回退清单超过文件数上限")
    return sorted(result, key=lambda item: item["path"])


def prepare(game_root: Path, bundle: Path, export_root: Path, package: Path) -> tuple[dict[str, bytes], dict]:
    report_raw = small_file(input_path(package, "package-report.json"))
    verification = verify_package(game_root, bundle, export_root, package)
    if small_file(input_path(package, "package-report.json")) != report_raw:
        raise ValueError("打包报告在验证期间改变")
    package_report = strict_json(report_raw)
    source_inventory = inventory(game_root)
    sources = {item["path"]: item for item in source_inventory}
    staged, overrides, impacts = {}, [], []
    for item in package_report["files"]:
        if not item["path"].startswith("index-candidate/"):
            continue
        relative = mount_relative(item["path"][len("index-candidate/"):])
        raw = small_file(plain_file(package / "index-candidate", relative))
        if len(raw) != item["bytes"] or digest(raw) != item["sha256"]:
            raise ValueError("打包文件在加载准备期间改变")
        if relative.casefold() in {name.casefold() for name in staged}:
            raise ValueError("加载候选有冲突路径")
        staged[relative] = raw
        overrides.append(relative)
        if relative in sources:
            original = small_file(plain_file(game_root, relative))
            impacts.append({"path": relative, **header_impact(original, raw)})
        elif not relative.endswith(".cas"):
            raise ValueError("受限加载准备不允许新增元数据路径")
    # 所有原版非 CAS 元数据复制进项目内，其他 TOC 仅逐字节保留，不解释未知结构。
    for item in source_inventory:
        if item["kind"] == "metadata" and item["path"] not in staged:
            raw = small_file(plain_file(game_root, item["path"]))
            if digest(raw) != item["sha256"]:
                raise ValueError("原版元数据在准备期间改变")
            staged[item["path"]] = raw
    if sum(map(len, staged.values())) > MAX_STAGE_BYTES:
        raise ValueError("本次加载副本超过 160 MiB 上限")
    fallback = [item for item in source_inventory if item["path"] not in staged]
    if any(item["kind"] != "cas" for item in fallback):
        raise ValueError("原版回退中包含未复制元数据")
    container_raw = small_file(input_path(package, "candidate.fbmod"))
    if digest(container_raw) != package_report["summary"]["container_sha256"]:
        raise ValueError("实验容器在加载准备期间改变")
    container = read_mod(container_raw)
    selected = {resource.name: {"sha1": hashlib.sha1(resource.payload).hexdigest(),
                               "sha256": digest(resource.payload), "original_size": resource.original_size}
                for resource in container.resources}
    report = {"format": "fc27-offline-loader-stage-v1", "summary": {
        "tool": "fc27-offline-loader-stage", "package_report_sha256": digest(report_raw),
        "container_sha256": package_report["summary"]["container_sha256"],
        "source_hashes": package_report["summary"]["source_hashes"],
        "patch_head": package_report["summary"]["patch_head"],
        "staged_files": len(staged), "staged_bytes": sum(map(len, staged.values())),
        "override_files": len(overrides), "metadata_copies": len(staged) - len(overrides),
        "original_cas_fallback_files": len(fallback),
        "original_cas_full_hashes_verified": False, "fallback_engine_behavior_verified": False,
        "standalone_moddata": False, "signature_validity_verified": False,
        "profile_mapping_verified": False, "loader_compatibility_verified": False,
        "loadable_mod": False, "gameplay_effect_verified": False,
        "game_started_by_tool": False, "game_files_written": False, "filesystem_links_created": False},
        "package_verification": verification, "source_inventory": source_inventory,
        "fallback_files": fallback, "override_paths": sorted(overrides), "header_impacts": impacts,
        "compiled_memberships": {name: len(hits) for name, hits in
                                 package_report["summary"]["memberships"].items()},
        "selected": selected, "staged_files": [
            {"path": name, "bytes": len(raw), "sha256": digest(raw)} for name, raw in sorted(staged.items())],
        "unresolved": ["FC27 对改后封装头/签名的接受性", "原版 CAS 的实际引擎回退或受支持加载目录部署",
                       "实际 FC27 加载器配置映射", "未扫描 TOC 的引用与独立 Chunk 表", "游戏加载及比赛效果"]}
    if inventory(game_root) != source_inventory:
        raise ValueError("游戏数据在准备期间改变")
    if small_file(input_path(package, "package-report.json")) != report_raw:
        raise ValueError("打包报告在加载准备期间改变")
    return staged, report


def validate_disk(stage: Path, staged: dict[str, bytes]):
    expected = {"ModData/" + name for name in staged} | {"loader-stage-report.json"}
    actual = set()
    for path in stage.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("加载副本包含链接")
        if path.is_file():
            actual.add(path.relative_to(stage).as_posix())
    if actual != expected:
        raise ValueError("加载副本缺少文件或包含计划外文件")
    for relative, raw in staged.items():
        if small_file(plain_file(stage / "ModData", relative)) != raw:
            raise ValueError(f"加载副本与绑定来源不同：{relative}")


def audit(stage: Path, game_root: Path, staged: dict[str, bytes], report: dict) -> dict:
    mount = OfflineMount(game_root, stage / "ModData", set(staged),
                         {item["path"]: item for item in report["fallback_files"]})
    result = audit_mount(mount, report["selected"])
    # 与受绑定打包报告的全部目标引用数量独立对照。
    if result["selected_memberships"] != report["compiled_memberships"]:
        raise ValueError("加载副本的目标引用次数与编译记录不同")
    if inventory(game_root) != report["source_inventory"]:
        raise ValueError("原版数据在加载校验期间改变")
    return result


def run(game_root: Path, bundle: Path, export_root: Path, package: Path, output: Path) -> dict:
    game_root = game_root.resolve(strict=True)
    bundle, export_root, package = map(local_input, (bundle, export_root, package))
    destination = output_path(output, game_root)
    staged, report = prepare(game_root, bundle, export_root, package)
    destination.mkdir(parents=True)
    for relative, raw in staged.items():
        path = destination / "ModData" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
    try:
        report["mount_audit"] = audit(destination, game_root, staged, report)
        report["summary"]["offline_mount_verified"] = True
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        report["summary"]["offline_mount_verified"] = False
        report["failure"] = str(exc)
        write_report(destination, report)
        raise
    write_report(destination, report)
    validate_disk(destination, staged)
    return report["summary"]


def verify(game_root: Path, bundle: Path, export_root: Path, package: Path, stage: Path) -> dict:
    game_root = game_root.resolve(strict=True)
    bundle, export_root, package, stage = map(local_input, (bundle, export_root, package, stage))
    staged, expected = prepare(game_root, bundle, export_root, package)
    validate_disk(stage, staged)
    expected["mount_audit"] = audit(stage, game_root, staged, expected)
    expected["summary"]["offline_mount_verified"] = True
    actual = strict_json(small_file(input_path(stage, "loader-stage-report.json")))
    if actual != expected:
        raise ValueError("加载报告与从绑定游戏基线重建的结果不同")
    validate_disk(stage, staged)
    return {**expected["summary"], "staged_files_rebuilt": True, **expected["mount_audit"]}


def write_report(destination: Path, report: dict):
    with (destination / "loader-stage-report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("game-root", "bundle", "fc27-export", "package"):
        parser.add_argument("--" + name, required=True, type=Path)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path)
    output.add_argument("--verify", type=Path, help="只读重建并验证已有加载副本")
    args = parser.parse_args()
    try:
        operation = verify if args.verify else run
        print(json.dumps(operation(args.game_root, args.bundle, args.fc27_export,
                                   args.package, args.verify or args.output), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
