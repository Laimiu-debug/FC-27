"""提取已校验前代样本、对照 FC27，并创建一个不可安装的字段移植实验。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fc27_assets import OodleDecoder, safe_relative, small_file
from fc27_candidate import encode_uncompressed_blocks, transfer_arrays
from fc27_modsample import read_sample
from fc27_riff import verify_ebx
from fc27_schema import Schemas, named_values
from fc27_sharedtypes import SharedTypes
from fc27_export import MAX_EXPORT_BYTES, output_path


CANDIDATE_NAME = "fifa/attribulator/gameplay/groups/gp_cpuai/gp_cpuai_cpuaipredictionpoints_runtime"
CANDIDATE_FIELDS = ["PredictionPointMinMovementDelta", "PredictionPointMaxMovementDistance",
                    "PredictionPointMaxSpaceDistance"]


def dump_json(path: Path, value: dict):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def compare_fields(current: dict, reference: dict) -> dict:
    if current["root_identity"] != reference["root_identity"]:
        if not (current.get("shared_types_sha256") and
                current["root_identity"]["guid"] == reference["root_identity"]["guid"] and
                current["root_type"] == reference["root_type"] and
                current.get("root_name_hash") is not None and
                current["root_name_hash"] == reference.get("root_name_hash")):
            raise ValueError("根类型差异没有 FC27 共享描述和名称哈希依据")
    changed, incompatible = [], []
    for name, shape in current["shapes"].items():
        old_shape = reference["shapes"].get(name)
        if old_shape is not None and current["field_name_hashes"].get(name) != reference["field_name_hashes"].get(name):
            raise ValueError("同名字段的名称哈希不同")
        # 数组标识另行记录，形状比较关注数据种类、数量和结构大小。
        shape_keys = {k: v for k, v in shape.items() if k != "array_identifier"}
        reference_keys = {k: v for k, v in (old_shape or {}).items() if k != "array_identifier"}
        if shape_keys != reference_keys:
            incompatible.append(name)
            continue
        paths = [p for p in current["leaves"] if p == name or p.startswith(name + "[") or p.startswith(name + ".")]
        differences = []
        for path in paths:
            before, after = current["leaves"][path], reference["leaves"].get(path)
            if after is None or before["format"] != after["format"]:
                incompatible.append(name)
                differences = []
                break
            if before["raw_hex"] != after["raw_hex"]:
                differences.append({"field": path, "fc27": before["value"], "fc26_mod": after["value"]})
        if differences:
            changed.append({"field": name, "changed_numeric_values": len(differences),
                            "examples": differences[:3]})
    return {"root_type": current["root_type"], "root_identity": current["root_identity"],
            "reference_root_identity": reference["root_identity"],
            "layout_source": current["mapping_basis"],
            "signature_changed": current["root_identity"] != reference["root_identity"],
            "mapped_fields": len(current["shapes"]),
            "mapped_named_fields": len(current["shapes"]) - sum(n in current["shapes"] for n in current["unresolved_names"]),
            "numeric_values": len(current["leaves"]),
            "changed_fields": changed, "incompatible_shapes": sorted(set(incompatible)),
            "unsupported_fields": current["unsupported_fields"],
            "unresolved_names": current["unresolved_names"],
            "reference_metadata_warnings": reference["metadata_warnings"]}


def run(game_root: Path, export_root: Path, sample_path: Path, sdk_path: Path,
        codec_path: Path, output: Path, shared_types: Path | None = None) -> dict:
    destination = output_path(output, game_root)
    export_root = export_root.resolve(strict=True)
    if not export_root.is_relative_to((PROJECT_ROOT / "local").resolve()):
        raise ValueError("FC27 基线导出目录必须位于本项目 local 内")
    manifest = json.loads(small_file(export_root / "manifest.json"))
    if not manifest.get("all_cas_sha1_verified") or not manifest.get("all_ebx_roundtrips_verified"):
        raise ValueError("基线清单没有完成原始数据校验")
    sample_data = small_file(sample_path)
    profile, records = read_sample(sample_data)
    decoder = OodleDecoder(codec_path, game_root)
    references, reference_info = {}, {}
    total = 0
    for record in records:
        total += record.original_size
        if total > MAX_EXPORT_BYTES:
            raise ValueError("前代载荷导出超过 64 MiB")
        data = record.decode(decoder)
        info = verify_ebx(data)
        if not info["byte_identical"]:
            raise ValueError("前代 EBX 封装读写不一致")
        references[record.name] = data
        reference_info[record.name] = info
    guids = {t["guid"] for a in manifest["assets"] for t in a["inspection"]["types"]}
    guids.update(t["guid"] for info in reference_info.values() for t in info["types"])
    sdk_data = small_file(sdk_path)
    schemas = Schemas.from_sdk(sdk_data, guids)
    # read_sample 已验证受支持样本的完整 SHA256；仅在读取参考数据时记录此已知异常。
    schemas.allow_legacy_curve_metadata = True
    current_schemas = SharedTypes(small_file(shared_types)).adapt(sdk_data, guids) if shared_types else schemas
    results, targets, baseline_hashes = [], {}, {}
    for asset in manifest["assets"]:
        name = asset["name"]
        if name not in references:
            continue
        path = (export_root / safe_relative(asset["output"])).resolve(strict=True)
        if not path.is_relative_to(export_root):
            raise ValueError("基线资产路径超出导出目录")
        current = small_file(path)
        if hashlib.sha256(current).hexdigest() != asset["decoded_sha256"]:
            raise ValueError("基线导出文件已改变，拒绝继续比较")
        targets[name] = current
        baseline_hashes[name] = asset["decoded_sha256"]
        record = {"name": name}
        try:
            current_values = named_values(current, current_schemas)
            reference_values = named_values(references[name], schemas)
            record.update(status="mapped", **compare_fields(current_values, reference_values))
            if record["numeric_values"] == 0:
                record.update(status="identity_only", reason="根类型已匹配，但尚无受支持的数值字段")
        except ValueError as exc:
            record.update(status="unmapped", reason=str(exc))
        results.append(record)
    if CANDIDATE_NAME not in targets or CANDIDATE_NAME not in references:
        raise ValueError("缺少预测点的基线或前代载荷")
    candidate, candidate_report = transfer_arrays(
        targets[CANDIDATE_NAME], references[CANDIDATE_NAME], current_schemas, CANDIDATE_FIELDS,
        baseline_hashes[CANDIDATE_NAME], schemas)
    candidate_report.update(resource=CANDIDATE_NAME, reference_package=profile.title,
                            reference_package_sha256=hashlib.sha256(sample_data).hexdigest())
    summary = {
        "reference_package": profile.title, "reference_package_sha256": hashlib.sha256(sample_data).hexdigest(),
        "comparison": "FC26 modified sample versus FC27 current; includes game-version differences",
        "reference_assets_exported": len(references), "reference_bytes": total,
        "all_reference_cas_sha1_verified": True, "all_reference_ebx_roundtrips_verified": True,
        "same_name_assets": len(results), "mapped_assets": sum(r["status"] == "mapped" for r in results),
        "identity_only_assets": sum(r["status"] == "identity_only" for r in results),
        "identity_matched_assets": sum(r["status"] != "unmapped" for r in results),
        "unmapped_assets": sum(r["status"] == "unmapped" for r in results),
        "supported_fields": sum(r.get("mapped_fields", 0) for r in results),
        "supported_named_fields": sum(r.get("mapped_named_fields", 0) for r in results),
        "numeric_values_read": sum(r.get("numeric_values", 0) for r in results),
        "sdk_sha256": schemas.sdk_sha256, "sdk_types_selected": len(schemas.types),
        "fc27_shared_types_sha256": getattr(current_schemas, "shared_sha256", None),
        "fc27_types_adapted": len(current_schemas.types) if shared_types else 0,
        "assets_with_changed_signatures": sum(r.get("signature_changed", False) for r in results),
        "unresolved_field_names": sum(len(r.get("unresolved_names", [])) for r in results),
        "reference_assets_with_metadata_warnings": sum(bool(r.get("reference_metadata_warnings")) for r in results),
        "candidate_changed_values": candidate_report["changed_values"],
        "candidate_changed_bytes": candidate_report["changed_bytes"],
        "loadable_mod": False, "gameplay_effect_verified": False,
        "game_started_by_tool": False, "game_files_written": False,
    }
    # 所有源文件校验和候选验证完成后，创建新目录；不覆盖之前的研究产物。
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    for name, data in references.items():
        path = destination / "reference-assets" / safe_relative(name + ".ebx")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    for filename, data in (("original.ebx", targets[CANDIDATE_NAME]), ("candidate.ebx", candidate),
                           ("candidate.casblocks", encode_uncompressed_blocks(candidate))):
        with (destination / filename).open("xb") as stream:
            stream.write(data)
    dump_json(destination / "summary.json", summary)
    dump_json(destination / "candidate-report.json", candidate_report)
    dump_json(destination / "field-comparison.json", {"summary": summary, "assets": results})
    dump_json(destination / "sdk-matched-schemas.json", {
        "sdk_version": "FC26", "sdk_sha256": schemas.sdk_sha256,
        "types": [{"guid": g, "signature": s, **d} for (g, s), d in schemas.types.items()],
    })
    if shared_types:
        dump_json(destination / "fc27-shared-schemas.json", {
            "shared_types_sha256": current_schemas.shared_sha256,
            "naming_sdk_sha256": schemas.sdk_sha256,
            "types": [{"guid": g, "signature": s, **d} for (g, s), d in current_schemas.types.items()],
        })
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("game-root", "fc27-export", "sample", "sdk", "codec", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--shared-types", type=Path, help="本机导出的 FC27 SharedTypeDescriptors.ebx")
    args = parser.parse_args()
    try:
        summary = run(args.game_root, args.fc27_export, args.sample, args.sdk, args.codec, args.output, args.shared_types)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
