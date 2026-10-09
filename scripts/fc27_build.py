"""按显式计划批量构建离线 EBX 候选、CAS 块和恢复副本；不安装模组。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fc27_runtime import project_root
PROJECT_ROOT = project_root()

from fc27_assets import input_path, safe_relative, small_file
from fc27_candidate import encode_uncompressed_blocks
from fc27_edit import MAX_EDITS, edit_values
from fc27_export import MAX_EXPORT_BYTES, output_path
from fc27_riff import verify_ebx
from fc27_sharedtypes import SharedTypes


SOURCE_FILES = {"Data/layout.toc", "Patch/layout.toc",
                "Data/Win32/fc/fcgame/fcgame.toc", "Patch/Win32/fc/fcgame/fcgame.toc"}
PLAN_KEYS = {"format", "export_manifest_sha256", "sdk_sha256", "shared_types_sha256", "assets"}


def strict_json(data: bytes):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 存在重复键")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("JSON 不接受非有限常量")
    return json.loads(data, object_pairs_hook=object_pairs, parse_constant=constant)


def local_input(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to((PROJECT_ROOT / "local").resolve()):
        raise ValueError("研究输入必须位于项目 local 内")
    return resolved


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_shared_source(shared_path: Path, game_root: Path, shared_sha256: str):
    layer = shared_path.parent.name
    if layer not in ("Data", "Patch") or shared_path.name not in (
            "SharedTypeDescriptors.ebx", "SharedTypeDescriptors_patch.ebx"):
        raise ValueError("共享类型输入必须是 initfs 工具按层导出的文件")
    manifest = strict_json(small_file(shared_path.parent.parent / "manifest.json"))
    layers = [item for item in manifest.get("files", []) if item.get("layer") == layer]
    if len(layers) != 1:
        raise ValueError("共享类型清单缺少唯一来源层")
    source = layers[0]
    matches = [item for item in source.get("files", []) if item.get("name") == shared_path.name]
    if len(matches) != 1 or matches[0].get("sha256") != shared_sha256:
        raise ValueError("共享类型内容与导出清单不符")
    if not source.get("outer_roundtrip_verified") or not source.get("inner_roundtrip_verified"):
        raise ValueError("共享类型导出未完成逐字节校验")
    if digest(small_file(input_path(game_root, layer + "/initfs_Win32"))) != source.get("source_sha256"):
        raise ValueError("游戏 initfs 已改变，需要重新提取共享类型")


def run(game_root: Path, export_root: Path, sdk_path: Path, shared_path: Path,
        plan_path: Path, output: Path) -> dict:
    destination = output_path(output, game_root)
    game_root = game_root.resolve(strict=True)
    export_root, sdk_path, shared_path, plan_path = map(local_input, (export_root, sdk_path, shared_path, plan_path))
    plan_data, manifest_data = small_file(plan_path), small_file(export_root / "manifest.json")
    plan, manifest = strict_json(plan_data), strict_json(manifest_data)
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS or plan["format"] != "fc27-fixed-edit-plan-v1":
        raise ValueError("不是受支持的定点编辑计划")
    if digest(manifest_data) != plan["export_manifest_sha256"]:
        raise ValueError("编辑计划绑定的导出清单已改变")
    if not isinstance(manifest, dict):
        raise ValueError("基线清单必须是 JSON 对象")
    if not manifest.get("all_cas_sha1_verified") or not manifest.get("all_ebx_roundtrips_verified"):
        raise ValueError("FC27 基线导出未完成校验")
    sources = manifest.get("source_hashes", {})
    if not isinstance(sources, dict) or set(sources) != SOURCE_FILES:
        raise ValueError("基线清单的版本来源不完整")
    for relative, sha in sources.items():
        if digest(small_file(input_path(game_root, relative))) != sha:
            raise ValueError("游戏索引已改变，需要重新导出基线")
    sdk_data, shared_data = small_file(sdk_path), small_file(shared_path)
    if digest(sdk_data) != plan["sdk_sha256"] or digest(shared_data) != plan["shared_types_sha256"]:
        raise ValueError("计划绑定的名称 SDK 或共享类型表已改变")
    check_shared_source(shared_path, game_root, digest(shared_data))
    entries = plan["assets"]
    if not isinstance(entries, list) or not 0 < len(entries) <= 64:
        raise ValueError("单次计划必须包含 1 至 64 个资产")
    records = {}
    for record in manifest["assets"]:
        if record["name"] in records:
            raise ValueError("基线清单的资产名称不唯一")
        records[record["name"]] = record
    prepared, names, guids, total, total_edits = [], set(), set(), 0, 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "expected_sha256", "root_identity", "edits"}:
            raise ValueError("资产计划字段不完整或存在未知字段")
        name = entry["name"]
        if not isinstance(name, str):
            raise ValueError("资产名称必须是字符串")
        safe_relative(name)
        if name in names or name not in records or not name.startswith("fifa/attribulator/"):
            raise ValueError("计划资产重复或不在已校验玩法基线中")
        names.add(name)
        record = records[name]
        source_path = input_path(export_root, record["output"])
        original = small_file(source_path)
        if digest(original) != record["decoded_sha256"] or digest(original) != entry["expected_sha256"]:
            raise ValueError("计划资产基线或清单散列不同")
        total += len(original)
        if total > MAX_EXPORT_BYTES:
            raise ValueError("计划的原始资产超过 64 MiB")
        info = verify_ebx(original)
        guids.update(item["guid"] for item in info["types"])
        identity = entry["root_identity"]
        if not isinstance(identity, dict) or set(identity) != {"guid", "signature"}:
            raise ValueError("资产计划缺少完整类型 GUID/签名")
        if not isinstance(entry["edits"], list):
            raise ValueError("资产编辑项必须是列表")
        total_edits += len(entry["edits"])
        if total_edits > MAX_EDITS:
            raise ValueError("单次计划的编辑项超过上限")
        prepared.append((entry, original))
    schemas = SharedTypes(shared_data).adapt(sdk_data, guids)
    built = []
    for entry, original in prepared:
        candidate, report = edit_values(original, schemas, entry["expected_sha256"], entry["edits"], entry["root_identity"])
        report["resource"] = entry["name"]
        built.append((entry["name"], original, candidate, report))
    summary = {"tool": "fc27-offline-fixed-build", "plan_sha256": digest(plan_data),
               "export_manifest_sha256": digest(manifest_data), "sdk_sha256": digest(sdk_data),
               "shared_types_sha256": digest(shared_data), "source_hashes": sources,
               "assets_built": len(built), "changed_values": sum(r[3]["changed_values"] for r in built),
               "changed_bytes": sum(r[3]["changed_bytes"] for r in built),
               "all_reverse_restores_verified": True, "loadable_mod": False,
               "game_started_by_tool": False, "game_files_written": False,
               "gameplay_effect_verified": False}
    # 所有版本、字段和反向恢复均通过后，才创建新输出目录。
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    for name, original, candidate, report in built:
        for folder, suffix, data in (("original-assets", ".ebx", original),
                                     ("candidate-assets", ".ebx", candidate),
                                     ("casblocks", ".casblocks", encode_uncompressed_blocks(candidate))):
            path = destination / folder / safe_relative(name + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
    with (destination / "build-report.json").open("x", encoding="utf-8") as stream:
        json.dump({"summary": summary, "assets": [item[3] for item in built]}, stream,
                  ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    with (destination / "plan.json").open("xb") as stream:
        stream.write(plan_data)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("game-root", "fc27-export", "sdk", "shared-types", "plan", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(args.game_root, args.fc27_export, args.sdk, args.shared_types, args.plan, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
