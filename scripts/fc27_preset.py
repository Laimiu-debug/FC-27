"""创建自有版本绑定的数值预设，或自动生成离线候选；不部署、不开游戏。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fc27_runtime import project_root
from fc27_management import UNVERIFIED, decode_json, json_bytes, read_bytes, write_new
from fc27_presets import MAX_PRESET, digest, prepare, validate_preset


def create(bundle: Path, manifest: Path, recipe: Path, output: Path, shared_manifest: Path):
    import fc27_build as build
    from fc27_verify_build import run as verify_build
    from fc27_setup import no_links
    root = build.PROJECT_ROOT.resolve(strict=True)
    output = output if output.is_absolute() else root / output
    no_links(output)
    target = output.resolve()
    if not any(target.is_relative_to(root / folder) and target != root / folder for folder in ("local", "resources")):
        raise ValueError("预设描述只能新建在项目 resources 或 local 内")
    verify_build(bundle)
    report_raw, manifest_raw, recipe_raw = (read_bytes(path, 32 * 1024 * 1024) for path in (
        bundle / "build-report.json", manifest, recipe))
    report, exported, recipe = map(decode_json, (report_raw, manifest_raw, recipe_raw))
    summary = report["summary"]
    shared = decode_json(read_bytes(shared_manifest, 1024 * 1024))
    layers = shared.get("files", [])
    if len(layers) != 2 or {layer["layer"] for layer in layers} != {"Data", "Patch"}:
        raise ValueError("共享类型来源须同时绑定 Data/Patch")
    if any(layer.get("outer_roundtrip_verified") is not True or layer.get("inner_roundtrip_verified") is not True for layer in layers):
        raise ValueError("共享类型来源未完成封装往返校验")
    matches = [item for layer in layers for item in layer.get("files", [])
               if item.get("sha256") == summary["shared_types_sha256"]]
    if len(matches) != 1:
        raise ValueError("共享类型来源与编译的类型表不同")
    if summary["export_manifest_sha256"] != digest(manifest_raw) or summary["source_hashes"] != exported["source_hashes"]:
        raise ValueError("预设研究来源的导出与编译绑定不符")
    records = {item["name"]: item for item in exported["assets"]}
    assets = []
    for item in report["assets"]:
        source = records[item["resource"]]
        if source["decoded_sha256"] != item["original_sha256"]:
            raise ValueError("预设资产与导出基线不符")
        assets.append({"name": item["resource"], "original_size": source["original_size"],
                       "packed_sha1": source["sha1"], "original_sha256": item["original_sha256"],
                       "candidate_sha256": item["candidate_sha256"], "root_identity": item["root_identity"],
                       "changes": [{key: change[key] for key in ("field", "offset", "format", "before_hex", "after_hex")}
                                   for change in item["changes"]]})
    preset = {"format": "fc27-bound-preset-v1", "id": "whole-match-experiment-v1",
              "title": "整场比赛研究方案", "source_hashes": summary["source_hashes"],
              "provenance": {"build_report_sha256": digest(report_raw), "sdk_sha256": summary["sdk_sha256"],
                             "shared_types_sha256": summary["shared_types_sha256"], "recipe_sha256": digest(recipe_raw),
                             "schema_source_hashes": {layer["layer"] + "/initfs_Win32": layer["source_sha256"] for layer in layers},
                             "runtime_schema_adaptation": False, "reference_is_modified_fc26": True},
              "modules": [{"id": m["id"], "title": m["title"], "assets": [a["name"] for a in m["assets"]]}
                          for m in recipe["modules"]], "assets": assets, "safety": dict(UNVERIFIED)}
    validate_preset(preset)
    raw = json_bytes(preset)
    if len(raw) > MAX_PRESET:
        raise ValueError("预设超过公开描述大小上限")
    write_new(target, raw)
    return {"preset": str(output), "assets": len(assets), "changes": sum(len(a["changes"]) for a in assets),
            "bytes": len(raw), "sha256": digest(raw), "contains_game_payloads": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    creator = sub.add_parser("create")
    for name in ("bundle", "manifest", "recipe", "output", "shared-manifest"):
        creator.add_argument("--" + name, required=True, type=Path)
    builder = sub.add_parser("prepare")
    builder.add_argument("--game-root", type=Path, required=True)
    builder.add_argument("--module", default="all")
    args = parser.parse_args()
    try:
        value = create(args.bundle, args.manifest, args.recipe, args.output, args.shared_manifest) if args.command == "create" else prepare(
            project_root(), args.game_root, args.module)
        print(json_bytes(value).decode("utf-8"))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
