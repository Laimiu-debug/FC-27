"""从已验证定点构建生成 Frosty v6 容器及 FC27 索引候选；不安装、不启动。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from fc27_build import SOURCE_FILES, digest, local_input, strict_json
from fc27_verify_build import run as verify_build
from fc27_assets import (Catalog, Location, TOCS, field, input_path, read_bundle,
                         read_toc, safe_relative, small_file)
from fc27_dbobject import Document, MAX_FILE_BYTES
from fc27_export import output_path
from fc27_index import add_layout_cas, layout_cas_ids, patch_binary_bundle, patch_toc
from fc27_modcontainer import EbxResource, read_mod, write_mod


def allocate_cas(catalog: Catalog, layer: int, package_index: int, registered: list[int], referenced: set[int]) -> int:
    prefix = layer << 48 | package_index << 16
    used = {i & 0xFFFF for i in set(registered) | referenced if i >> 16 == prefix >> 16}
    relative = catalog.relative_cas(Location(prefix | 1, 0, 1))
    folder = input_path(catalog.root, str(safe_relative(relative).parent))
    for path in folder.glob("cas_*.cas"):
        match = re.fullmatch(r"cas_([0-9]+)\.cas", path.name, re.IGNORECASE)
        if not match:
            raise ValueError("CAS 文件名不规范，拒绝猜测新编号")
        used.add(int(match.group(1)))
    number = max(used, default=0) + 1
    if not 0 < number <= 0xFFFF:
        raise ValueError("没有可用的 UInt16 CAS 编号")
    return prefix | number


def prepare(game_root: Path, bundle: Path, export_root: Path, profile: str) -> tuple[dict[str, bytes], dict]:
    """所有读取、基线核对和结构校验完成后，调用者才创建输出。"""
    verified = verify_build(bundle)
    report_data = small_file(input_path(bundle, "build-report.json"))
    report = strict_json(report_data)
    manifest_data = small_file(input_path(export_root, "manifest.json"))
    manifest = strict_json(manifest_data)
    if digest(manifest_data) != report["summary"]["export_manifest_sha256"]:
        raise ValueError("打包输入的原始导出清单不同")
    if not manifest.get("all_cas_sha1_verified") or not manifest.get("all_ebx_roundtrips_verified"):
        raise ValueError("资产基线未完成导出校验")
    sources = report["summary"]["source_hashes"]
    if set(sources) != SOURCE_FILES or manifest["source_hashes"] != sources:
        raise ValueError("构建和导出清单的索引来源不同")
    originals = {relative: small_file(input_path(game_root, relative)) for relative in sorted(SOURCE_FILES)}
    if any(digest(raw) != sources[relative] for relative, raw in originals.items()):
        raise ValueError("游戏索引已改变，拒绝旧版本打包")
    records = {}
    for record in manifest["assets"]:
        if record["name"] in records:
            raise ValueError("导出清单有重复资产")
        records[record["name"]] = record
    selected, resources = {}, []
    for asset in report["assets"]:
        name = asset["resource"]
        record = records[name]
        if record["decoded_sha256"] != asset["original_sha256"]:
            raise ValueError("原始导出清单与构建资产不同")
        candidate = small_file(input_path(bundle, "candidate-assets/" + name + ".ebx"))
        payload = small_file(input_path(bundle, "casblocks/" + name + ".casblocks"))
        if len(candidate) != record["original_size"]:
            raise ValueError("受限打包不支持原始尺寸变化")
        selected[name] = {"expected_sha1": record["sha1"], "original_size": len(candidate),
                          "sha1": hashlib.sha1(payload).hexdigest(), "payload": payload}
        resources.append(EbxResource(name, len(candidate), payload))
    catalog = Catalog(game_root)
    head = field(Document.parse(originals["Patch/layout.toc"]).root, "head").number_value()
    metadata = {"title": "FC27 AI offline experiment", "author": "FC-27 project",
                "category": "Gameplay", "version": "0.1-experimental",
                "description": "Offline format candidate. Loader compatibility and gameplay effect unverified.",
                "mod_page_link": "https://github.com/Laimiu-debug/FC-27"}
    container = write_mod(profile, head, metadata, resources)
    parsed = read_mod(container)
    if parsed.resources != tuple(resources):
        raise ValueError("容器载荷回读不同")
    files = {"candidate.fbmod": container}
    findings, all_memberships, archive_ids = [], {name: [] for name in selected}, []
    for layer, relative in enumerate(TOCS):
        toc_data = originals[relative]
        bundles = read_toc(toc_data)
        referenced = {loc.cas_id for bundle_info in bundles for loc in bundle_info.locations}
        memberships = []
        for bundle_info in bundles:
            raw = catalog.read(bundle_info.locations[0])
            hits = [a for a in read_bundle(raw, bundle_info, relative) if a.name in selected]
            if not hits:
                continue
            # 所有引用逐一读取，不能只依赖导出清单中最后出现的一项。
            for asset in hits:
                item = selected[asset.name]
                if (asset.sha1 != item["expected_sha1"] or asset.original_size != item["original_size"] or
                        hashlib.sha1(catalog.read(asset.location)).hexdigest() != item["expected_sha1"]):
                    raise ValueError("目标在某个 Bundle 中存在不同版本或 CAS 散列不符，拒绝跨版本替换")
                all_memberships[asset.name].append({"toc": relative, "bundle": bundle_info.index})
            memberships.append((bundle_info, raw, hits))
        if not memberships:
            continue
        layout_relative = f"{('Data', 'Patch')[layer]}/layout.toc"
        layout_data = originals[layout_relative]
        package_index = memberships[0][2][0].location.cas_id >> 16 & 0xFFFFFFFF
        cas_id = allocate_cas(catalog, layer, package_index, layout_cas_ids(layout_data), referenced)
        archive_ids.append(cas_id)
        archive, offsets = bytearray(), {}

        def store(raw):
            # 不复制整个原始 CAS，不创建到安装目录的文件链接。
            archive.extend(bytes((-len(archive)) % 4))
            location = Location(cas_id, len(archive), len(raw))
            archive.extend(raw)
            if len(archive) > MAX_FILE_BYTES:
                raise ValueError("本次索引候选 CAS 超过 32 MiB")
            return location

        for name in sorted({a.name for _, _, hits in memberships for a in hits}):
            offsets[name] = store(selected[name]["payload"])
        toc_replacements, modified = {}, {}
        for bundle_info, raw, hits in memberships:
            updated, indexes = patch_binary_bundle(raw, bundle_info, relative, selected)
            locations = list(bundle_info.locations)
            locations[0] = store(updated)
            for name, index in indexes.items():
                locations[index] = offsets[name]
            toc_replacements[bundle_info.index] = tuple(locations)
            modified[bundle_info.index] = (raw, updated, indexes)
        candidate_toc = patch_toc(toc_data, toc_replacements)
        # 从新 TOC 重新解析全部位置，选定记录重新读元数据、CAS、SHA1。
        for bundle_info in read_toc(candidate_toc):
            if bundle_info.index not in modified:
                continue
            raw, updated, indexes = modified[bundle_info.index]
            location = bundle_info.locations[0]
            reread = bytes(archive[location.offset:location.offset + location.size])
            if reread != updated:
                raise ValueError("候选元数据位置回读不同")
            reread_assets = read_bundle(reread, bundle_info, relative)
            for asset in reread_assets:
                if asset.name in indexes:
                    location = asset.location
                    blocks = bytes(archive[location.offset:location.offset + location.size])
                    item = selected[asset.name]
                    if blocks != item["payload"] or asset.sha1 != item["sha1"]:
                        raise ValueError("候选索引载荷或 SHA1 不同")
            findings.append({"toc": relative, "bundle": bundle_info.index,
                             "resources": sorted(indexes), "locations": len(bundle_info.locations),
                             "original_metadata_sha256": digest(raw), "candidate_metadata_sha256": digest(updated),
                             "unselected_locations_unchanged": True})
        files["index-candidate/" + relative] = candidate_toc
        files["index-candidate/" + layout_relative] = add_layout_cas(layout_data, {cas_id})
        files["index-candidate/" + catalog.relative_cas(Location(cas_id, 0, 1))] = bytes(archive)
        files["index-original/" + relative] = toc_data
        files["index-original/" + layout_relative] = layout_data
    if any(not hits for hits in all_memberships.values()):
        raise ValueError("部分候选资产没有实际 Bundle 引用")
    # 防止读取期间源索引发生变化。
    if any(digest(small_file(input_path(game_root, rel))) != sha for rel, sha in sources.items()):
        raise ValueError("打包期间游戏索引发生变化")
    summary = {"tool": "fc27-offline-package", "build_report_sha256": digest(report_data),
               "export_manifest_sha256": digest(manifest_data), "source_hashes": sources,
               "assets_packaged": len(resources), "changed_values": verified["changed_values"],
               "changed_bytes": verified["changed_bytes"], "container_format": "Frosty-v6-existing-EBX",
               "container_sha256": digest(container), "container_roundtrip_verified": True,
               "target_profile_label": profile, "profile_mapping_verified": False, "patch_head": head,
               "bundles_compiled": len(findings), "cas_archives_created": len(archive_ids),
               "cas_ids": [f"{i:016x}" for i in archive_ids], "memberships": all_memberships,
               "indices_reparsed": True, "unselected_locations_unchanged": True,
               "loadable_mod": False, "loader_compatibility_verified": False,
               "gameplay_effect_verified": False, "game_started_by_tool": False, "game_files_written": False,
               "limitations": ["profile 是显式研究标签，尚无已验证的 FC27 加载器映射",
                               "FC27 0x84 位置标记未知位按原值保留，未验证引擎语义",
                               "封装头按原样保留，没有签名或游戏接受性验证",
                               "索引候选需要原版数据回退，不是独立完整 ModData",
                               "未处理新增资产、资源尺寸或引用结构变化"]}
    return files, {"summary": summary, "bundles": findings}


def run(game_root: Path, bundle: Path, export_root: Path, output: Path, profile: str) -> dict:
    game_root = game_root.resolve(strict=True)
    bundle, export_root = local_input(bundle), local_input(export_root)
    destination = output_path(output, game_root)
    files, report = prepare(game_root, bundle, export_root, profile)
    report["files"] = [{"path": name, "bytes": len(raw), "sha256": digest(raw)}
                       for name, raw in sorted(files.items())]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    for name, raw in files.items():
        path = destination / safe_relative(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
    with (destination / "package-report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return report["summary"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("game-root", "bundle", "fc27-export", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--profile", required=True, help="显式研究标签；不代表已验证的加载器 profile")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.game_root, args.bundle, args.fc27_export, args.output, args.profile),
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
