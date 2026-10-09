"""版本绑定预设：分发数值编辑描述，从用户原版生成副本，不分发游戏载荷。"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import struct
import re
import uuid
import urllib.request
import zipfile

from fc27_assets import (Catalog, OODLE_SHA256, OodleDecoder, export_asset, input_path,
                         safe_relative, small_file)
from fc27_candidate import encode_uncompressed_blocks
from fc27_edit import FORMATS, MAX_EDITS, hex_bytes, revert_values, validate_raw
from fc27_management import (UNVERIFIED, decode_json, json_bytes, plain_path, project_local,
                             read_bytes, write_new)
from fc27_progress import emit
from fc27_riff import RiffDocument, verify_ebx
from fc27_runtime import resource_root


PRESET_FILE = "resources/whole-match-preset.json"
MAX_PRESET = 256 * 1024
SOURCES = {"Data/layout.toc", "Patch/layout.toc", "Data/Win32/fc/fcgame/fcgame.toc",
           "Patch/Win32/fc/fcgame/fcgame.toc"}
CODEC_URL = ("https://github.com/FMTDev/FMT.Releases/releases/download/"
             "FMT-26.10.9654.14105/FMT-26.10.9654.14105.zip")
CODEC_ZIP_SHA = "715ee3c38bf0faa77069a6332e672e7b3bc2bd245f4fbf80dd6296cf9cf52051"
CODEC_ZIP_BYTES = 261062429


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load_preset() -> dict:
    # 只读取程序内公开资源，不接受用户上传的任意偏移表。
    raw = read_bytes(plain_path(resource_root(), PRESET_FILE), MAX_PRESET)
    value = decode_json(raw)
    validate_preset(value)
    return value


def hash_text(value, length=64):
    if not isinstance(value, str) or len(value) != length or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("预设散列格式无效")


def validate_preset(value):
    keys = {"format", "id", "title", "source_hashes", "provenance", "modules", "assets", "safety"}
    if (not isinstance(value, dict) or set(value) != keys or value["format"] != "fc27-bound-preset-v1"
            or value["safety"] != UNVERIFIED):
        raise ValueError("不是受支持的版本绑定预设")
    if set(value["source_hashes"]) != SOURCES:
        raise ValueError("预设必须绑定四份实际索引")
    for sha in value["source_hashes"].values():
        hash_text(sha)
    provenance = value["provenance"]
    if set(provenance) != {"build_report_sha256", "sdk_sha256", "shared_types_sha256", "recipe_sha256", "schema_source_hashes",
                           "runtime_schema_adaptation", "reference_is_modified_fc26"}:
        raise ValueError("预设研究来源不完整")
    for key in ("build_report_sha256", "sdk_sha256", "shared_types_sha256", "recipe_sha256"):
        hash_text(provenance[key])
    if set(provenance["schema_source_hashes"]) != {"Data/initfs_Win32", "Patch/initfs_Win32"}:
        raise ValueError("预设必须绑定两层共享类型来源")
    for sha in provenance["schema_source_hashes"].values():
        hash_text(sha)
    if provenance["runtime_schema_adaptation"] is not False or provenance["reference_is_modified_fc26"] is not True:
        raise ValueError("预设不能声称重新完成类型适配或前代原版对比")
    assets, names, edits = value["assets"], set(), 0
    if not isinstance(assets, list) or not 1 <= len(assets) <= 64:
        raise ValueError("预设资产数量无效")
    for item in assets:
        if set(item) != {"name", "original_size", "packed_sha1", "original_sha256", "candidate_sha256",
                         "root_identity", "changes"}:
            raise ValueError("预设资产字段无效")
        name = item["name"]
        safe_relative(name)
        if not name.startswith("fifa/attribulator/") or name in names:
            raise ValueError("预设只接受唯一玩法资产")
        names.add(name)
        if type(item["original_size"]) is not int or not 0 < item["original_size"] <= 32 * 1024 * 1024:
            raise ValueError("预设原始尺寸无效")
        hash_text(item["packed_sha1"], 40)
        hash_text(item["original_sha256"])
        hash_text(item["candidate_sha256"])
        if set(item["root_identity"]) != {"guid", "signature"}:
            raise ValueError("预设根类型身份不完整")
        if str(uuid.UUID(item["root_identity"]["guid"])) != item["root_identity"]["guid"]:
            raise ValueError("预设 GUID 无效")
        hash_text(item["root_identity"]["signature"], 8)
        fields, occupied = set(), set()
        if not isinstance(item["changes"], list) or not 1 <= len(item["changes"]) <= MAX_EDITS:
            raise ValueError("预设修改项数量无效")
        for change in item["changes"]:
            if set(change) != {"field", "offset", "format", "before_hex", "after_hex"}:
                raise ValueError("预设修改项字段无效")
            fmt, offset, field = change["format"], change["offset"], change["field"]
            if fmt not in FORMATS or type(offset) is not int or not 0 <= offset < item["original_size"]:
                raise ValueError("预设偏移或数值类型无效")
            if not isinstance(field, str) or not field or len(field) > 256 or field in fields:
                raise ValueError("预设字段名称为空、过长或重复")
            fields.add(field)
            width = struct.calcsize(fmt)
            before, after = hex_bytes(change["before_hex"], width), hex_bytes(change["after_hex"], width)
            validate_raw(before, fmt)
            validate_raw(after, fmt)
            affected = set(range(offset, offset + width))
            if before == after or offset + width > item["original_size"] or occupied & affected:
                raise ValueError("预设存在无效、重叠或无变化的修改")
            occupied.update(affected)
            edits += 1
    if edits > MAX_EDITS:
        raise ValueError("预设修改总量超过上限")
    ids, assigned = set(), set()
    if not isinstance(value["modules"], list) or not 1 <= len(value["modules"]) <= 16:
        raise ValueError("预设模块数量无效")
    for module in value["modules"]:
        if set(module) != {"id", "title", "assets"} or not module["assets"] or module["id"] == "all":
            raise ValueError("预设模块字段无效")
        if not isinstance(module["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", module["id"]):
            raise ValueError("预设模块标识无效")
        if module["id"] in ids or len(set(module["assets"])) != len(module["assets"]):
            raise ValueError("预设模块或模块资产重复")
        ids.add(module["id"])
        if not set(module["assets"]) <= names or assigned & set(module["assets"]):
            raise ValueError("预设模块必须划分已绑定资产，不能叠加偏移修改")
        assigned.update(module["assets"])
    if assigned != names:
        raise ValueError("预设有未分配模块的资产")


def select_assets(preset, module):
    if module == "all":
        return preset["assets"]
    modules = {item["id"]: item for item in preset["modules"]}
    if module not in modules:
        raise ValueError("未找到所选内置方案")
    names = set(modules[module]["assets"])
    return [item for item in preset["assets"] if item["name"] in names]


def version_check(game: Path, preset: dict) -> dict:
    from fc27_setup import no_links
    no_links(game)
    game = game.resolve(strict=True)
    mismatches = []
    for name, expected in (preset["source_hashes"] | preset["provenance"]["schema_source_hashes"]).items():
        path = plain_path(game, name)
        if digest(small_file(path)) != expected:
            mismatches.append(name)
    return {"matched": not mismatches, "mismatched_files": sorted(mismatches),
            "scope": "four-index-and-two-initfs-sha256", **UNVERIFIED}


def apply_bound_asset(original: bytes, item: dict) -> tuple[bytes, dict]:
    if len(original) != item["original_size"] or digest(original) != item["original_sha256"]:
        raise ValueError("原版资产与已审查的预设不一致；拒绝按旧偏移修改")
    info = verify_ebx(original)
    if not info["byte_identical"] or item["root_identity"] not in info["types"]:
        raise ValueError("原版 RIFF 或完整根 GUID/签名不符")
    start, end = info["payload_file_offset"], info["payload_file_offset"] + info["payload_size"]
    result = bytearray(original)
    for change in item["changes"]:
        width = struct.calcsize(change["format"])
        offset = change["offset"]
        if not start <= offset <= end - width or original[offset:offset+width].hex() != change["before_hex"]:
            raise ValueError("预设修改超出 EBXD 或原始数值位不符")
        result[offset:offset+width] = bytes.fromhex(change["after_hex"])
    candidate = bytes(result)
    if digest(candidate) != item["candidate_sha256"] or not verify_ebx(candidate)["byte_identical"]:
        raise ValueError("预设生成的完整资产散列或 RIFF 校验失败")
    for before, after in zip(RiffDocument.parse(original).chunks, RiffDocument.parse(candidate).chunks):
        if before.name != b"EBXD" and before != after:
            raise ValueError("预设改变了不允许修改的引用表")
    blocks = encode_uncompressed_blocks(candidate)
    report = {"kind": "fc27-fixed-numeric-edit-v1", "resource": item["name"],
              "root_identity": item["root_identity"], "original_sha256": item["original_sha256"],
              "candidate_sha256": item["candidate_sha256"], "changes": copy.deepcopy(item["changes"]),
              "changed_values": len(item["changes"]), "changed_bytes": sum(a != b for a, b in zip(original, candidate)),
              "cas_blocks_sha1": hashlib.sha1(blocks).hexdigest(), "cas_blocks_bytes": len(blocks),
              "runtime_schema_adaptation": False, "exact_reviewed_asset_sha256_matched": True, **UNVERIFIED}
    if revert_values(candidate, report) != original:
        raise ValueError("预设无法逐字节反向恢复")
    report["reverse_restore_verified"] = True
    return candidate, report


def codec_path(project: Path) -> Path:
    candidates = [project / "local/dependencies/oo2core_9_win64.dll",
                  project / "local/inputs/oo2core_9_win64.dll"]
    folder = project / "local/research"
    if folder.is_dir():
        candidates.extend(folder.glob("*/tools/codec/oo2core_9_win64.dll"))
    for candidate in candidates:
        if candidate.exists():
            path = project_local(candidate, project)
            if digest(read_bytes(path, 2 * 1024 * 1024)) == OODLE_SHA256:
                return path
    # 不加载游戏内 DLL；只获取过去已校验的公开工具发行包中的固定散列依赖。
    emit("dependency", "获取已校验的解压依赖（首次约 249 MiB）")
    archive = project_local(project / "local/dependencies" / (CODEC_ZIP_SHA + ".zip"), project, exists=False)
    if not archive.exists():
        temporary = project_local(archive.with_suffix("." + uuid.uuid4().hex + ".part"), project, exists=False)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(CODEC_URL, timeout=30) as response, temporary.open("xb") as stream:
            total, sha = 0, hashlib.sha256()
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > CODEC_ZIP_BYTES:
                    raise ValueError("依赖下载超出固定大小；保留未完成文件供诊断")
                stream.write(chunk)
                sha.update(chunk)
                emit("dependency", "下载已校验依赖", completed=total, total=CODEC_ZIP_BYTES, unit="bytes")
        if total != CODEC_ZIP_BYTES or sha.hexdigest() != CODEC_ZIP_SHA:
            raise ValueError("依赖发行包大小或 SHA256 不符；不会提取或执行")
        temporary.rename(archive)
    archive = project_local(archive, project)
    with archive.open("rb") as stream:
        if archive.stat().st_size != CODEC_ZIP_BYTES or hashlib.file_digest(stream, "sha256").hexdigest() != CODEC_ZIP_SHA:
            raise ValueError("已缓存依赖发行包被修改")
    with zipfile.ZipFile(archive) as container:
        matches = [info for info in container.infolist() if info.filename.split("/")[-1] == "oo2core_9_win64.dll"]
        if len(matches) != 1 or matches[0].file_size > 2 * 1024 * 1024:
            raise ValueError("发行包没有唯一的受支持解压依赖")
        raw = container.read(matches[0])
    if digest(raw) != OODLE_SHA256:
        raise ValueError("解压依赖来源散列不符")
    target = project_local(project / "local/dependencies/oo2core_9_win64.dll", project, exists=False)
    if target.exists():
        raise ValueError("已有解压依赖与允许散列不同，不覆盖")
    write_new(target, raw)
    return target


def prepare(project: Path, game: Path, module: str, preset: dict | None = None) -> dict:
    """无需 SDK/参考包配置，但只为完全匹配的已审查版本建立离线副本。"""
    from fc27_package import run as package
    from fc27_verify_build import run as verify_build
    from fc27_verify_package import run as verify_package
    from fc27_stage_loader import run as stage, verify as verify_stage
    preset = load_preset() if preset is None else preset
    validate_preset(preset)
    from fc27_setup import no_links
    no_links(game)
    project, game = project.resolve(strict=True), game.resolve(strict=True)
    destination = project_local(project / "local/preset-builds" / ("prepared-" + uuid.uuid4().hex), project, game, exists=False)
    wanted = select_assets(preset, module)
    emit("preflight", "核对原版版本指纹")
    if not version_check(game, preset)["matched"]:
        raise ValueError("游戏已更新或与内置预设版本不同，拒绝套用；无需用户准备 SDK")
    catalog = Catalog(game)
    records, _ = catalog.scan_attrib()
    by_name = {asset.name: asset for asset in records}
    if any(item["name"] not in by_name for item in wanted):
        raise ValueError("原版缺少预设绑定的玩法资产")
    decoder = None
    prepared, exported = [], []
    for index, item in enumerate(wanted):
        asset = by_name[item["name"]]
        if asset.sha1 != item["packed_sha1"] or asset.original_size != item["original_size"]:
            raise ValueError("原版资产压缩散列或尺寸与预设不同")
        try:
            original = export_asset(catalog, asset, decoder)
        except ValueError as exc:
            if decoder is not None or "缺少解压依赖" not in str(exc):
                raise
            decoder = OodleDecoder(codec_path(project), game)
            original = export_asset(catalog, asset, decoder)
        candidate, report = apply_bound_asset(original, item)
        prepared.append((item, original, candidate, report))
        exported.append({**asset.record(catalog), "decoded_sha256": digest(original),
                         "output": "assets/" + item["name"] + ".ebx"})
        emit("build", "生成版本绑定的玩法副本", completed=index+1, total=len(wanted), unit="assets")
    if not version_check(game, preset)["matched"]:
        raise ValueError("读取期间游戏版本改变，尚未创建输出")
    manifest = {"tool": "fc27-preset-selected-export", "source_hashes": preset["source_hashes"],
                "assets": exported, "all_cas_sha1_verified": True, "all_ebx_roundtrips_verified": True,
                "scope": "selected-preset-assets", **UNVERIFIED}
    manifest_raw = json_bytes(manifest)
    provenance = preset["provenance"]
    plan = {"format": "fc27-fixed-edit-plan-v1", "export_manifest_sha256": digest(manifest_raw),
            "sdk_sha256": provenance["sdk_sha256"], "shared_types_sha256": provenance["shared_types_sha256"],
            "assets": [{"name": item["name"], "expected_sha256": item["original_sha256"],
                        "root_identity": item["root_identity"], "edits": [
                            {"field": c["field"], "expected_hex": c["before_hex"], "new_hex": c["after_hex"]}
                            for c in item["changes"]]} for item in wanted]}
    plan_raw = json_bytes(plan)
    reports = [entry[3] for entry in prepared]
    summary = {"tool": "fc27-offline-fixed-build", "plan_sha256": digest(plan_raw),
               "export_manifest_sha256": digest(manifest_raw), "sdk_sha256": provenance["sdk_sha256"],
               "shared_types_sha256": provenance["shared_types_sha256"], "source_hashes": preset["source_hashes"],
               "assets_built": len(prepared), "changed_values": sum(r["changed_values"] for r in reports),
               "changed_bytes": sum(r["changed_bytes"] for r in reports), "all_reverse_restores_verified": True,
               "runtime_schema_adaptation": False, "preset_provenance": provenance, **UNVERIFIED}
    for item, original, candidate, report in prepared:
        name = item["name"]
        for folder, suffix, raw in (("export/assets", ".ebx", original), ("built/original-assets", ".ebx", original),
                                   ("built/candidate-assets", ".ebx", candidate),
                                   ("built/casblocks", ".casblocks", encode_uncompressed_blocks(candidate))):
            write_new(destination / folder / (name + suffix), raw)
    write_new(destination / "export/manifest.json", manifest_raw)
    write_new(destination / "built/plan.json", plan_raw)
    write_new(destination / "built/build-report.json", json_bytes({"summary": summary, "assets": reports}))
    verify_build(destination / "built")
    emit("package")
    package(game, destination / "built", destination / "export", destination / "packaged", "FC27")
    emit("verify")
    verify_package(game, destination / "built", destination / "export", destination / "packaged")
    emit("loader-stage")
    stage(game, destination / "built", destination / "export", destination / "packaged", destination / "loader-stage")
    emit("verify-loader-stage")
    checked = verify_stage(game, destination / "built", destination / "export", destination / "packaged", destination / "loader-stage")
    result = {"format": "fc27-preset-prepared-v1", "preset_id": preset["id"], "module": module,
              "output": destination.relative_to(project).as_posix(), "assets_built": summary["assets_built"],
              "changed_values": summary["changed_values"], "changed_bytes": summary["changed_bytes"],
              "preset_sha256": digest(json_bytes(preset)), "source_hashes": preset["source_hashes"],
              "schema_source_hashes": preset["provenance"]["schema_source_hashes"],
              "offline_mount_verified": checked["offline_mount_verified"], "prepared": True,
              "installed": False, "enabled": False, **UNVERIFIED}
    write_new(destination / "preset-result.json", json_bytes(result))
    return result
