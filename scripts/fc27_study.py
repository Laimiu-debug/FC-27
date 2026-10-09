"""逐项审核整场比赛参考方案，生成六组实验计划与可复查差异；不安装。"""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import re
import sys

from fc27_build import PROJECT_ROOT, SOURCE_FILES, check_shared_source, digest, local_input, strict_json
from fc27_assets import OodleDecoder, input_path, safe_relative, small_file
from fc27_export import MAX_EXPORT_BYTES, output_path
from fc27_modsample import read_sample
from fc27_riff import verify_ebx
from fc27_schema import Schemas
from fc27_sharedtypes import SharedTypes
from fc27_reference import prepare_transfer
from fc27_progress import emit
from fc27_schema_cache import cached


def recipe_input(path: Path) -> Path:
    path = path.resolve(strict=True)
    if not path.is_relative_to((PROJECT_ROOT / "resources").resolve()):
        raise ValueError("研究方案须位于项目 resources 下")
    return path


def validate_recipe(recipe):
    if (not isinstance(recipe, dict) or set(recipe) != {"format", "id", "description", "modules"} or
            recipe["format"] != "fc27-whole-match-study-v1" or
            not isinstance(recipe["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", recipe["id"]) or
            not isinstance(recipe["description"], str) or not recipe["description"] or
            not isinstance(recipe["modules"], list) or not 0 < len(recipe["modules"]) <= 16):
        raise ValueError("研究方案格式或模块数量不支持")
    ids, names, total_fields = set(), set(), 0
    for module in recipe["modules"]:
        if not isinstance(module, dict) or set(module) != {"id", "title", "hypothesis", "assets"}:
            raise ValueError("研究模块存在未知或缺失字段")
        identifier = module["id"]
        reserved = {"all", "combined", "con", "prn", "aux", "nul"} | {f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10)}
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", identifier) or
                identifier in ids or identifier in reserved):
            raise ValueError("研究模块名重复或不规范")
        ids.add(identifier)
        if any(not isinstance(module[key], str) or not module[key] for key in ("title", "hypothesis")):
            raise ValueError("模块缺少名称或待验证假设")
        if not isinstance(module["assets"], list) or not module["assets"]:
            raise ValueError("模块没有明确资产")
        for asset in module["assets"]:
            if not isinstance(asset, dict) or set(asset) != {"name", "fields"}:
                raise ValueError("资产选择格式不同")
            name, fields = asset["name"], asset["fields"]
            if (not isinstance(name, str) or not name.startswith("fifa/attribulator/gameplay/") or
                    "\0" in name or name.casefold() in names or safe_relative(name).as_posix() != name):
                raise ValueError("资产路径不规范、重复或不是比赛玩法资产")
            names.add(name.casefold())
            if (not isinstance(fields, list) or not fields or len(fields) > 128 or
                    any(not isinstance(n, str) or not n or any(c in n for c in (".", "[", "\0")) for n in fields) or
                    len(set(fields)) != len(fields)):
                raise ValueError("研究字段须为唯一且明确的根字段名")
            total_fields += len(fields)
    if len(names) > 64 or total_fields > 1024:
        raise ValueError("研究方案超过 64 个资产或 1024 个根字段")


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def prepare(game_root: Path, export_root: Path, sample_path: Path, sdk_path: Path,
            shared_path: Path, codec_path: Path, recipe_path: Path, module: str = "all") -> tuple[dict[str, bytes], dict]:
    requested_module = module
    manifest_data = small_file(input_path(export_root, "manifest.json"))
    manifest = strict_json(manifest_data)
    sources = manifest.get("source_hashes", {})
    if (set(sources) != SOURCE_FILES or not manifest.get("all_cas_sha1_verified") or
            not manifest.get("all_ebx_roundtrips_verified")):
        raise ValueError("原始导出来源不完整或未完成校验")
    for relative, sha in sources.items():
        if digest(small_file(input_path(game_root, relative))) != sha:
            raise ValueError("原始索引改变，请重新导出")
    recipe_data = small_file(recipe_path)
    recipe = strict_json(recipe_data)
    validate_recipe(recipe)
    if module != "all":
        selected = [item for item in recipe["modules"] if item["id"] == module]
        if not selected:
            raise ValueError("所选模块不在明确研究方案中")
        recipe = {**recipe, "modules": selected}
    sdk_data, shared_data, sample_data = map(small_file, (sdk_path, shared_path, sample_path))
    check_shared_source(shared_path, game_root, digest(shared_data))
    profile, references = read_sample(sample_data)
    references = {a.name: a for a in references}
    records = {}
    for entry in manifest["assets"]:
        if entry["name"] in records:
            raise ValueError("原始导出清单含重复名称")
        records[entry["name"]] = entry
    wanted = {a["name"] for m in recipe["modules"] for a in m["assets"]}
    decoder = OodleDecoder(codec_path, game_root)
    originals, decoded, guids, total = {}, {}, set(), 0
    missing = {}
    for name in sorted(wanted):
        emit("study", "读取资产：" + name.rsplit("/", 1)[-1], assets_total=len(wanted), assets_read=len(originals))
        if name not in records or name not in references:
            missing[name] = "缺少同名当前资产或已校验参考"
            continue
        record = records[name]
        current = small_file(input_path(export_root, record["output"]))
        if digest(current) != record["decoded_sha256"]:
            raise ValueError("原始资产副本已改变")
        reference = references[name].decode(decoder)
        total += len(current) + len(reference)
        if total > MAX_EXPORT_BYTES:
            raise ValueError("本次研究资产超过 64 MiB")
        for raw in (current, reference):
            info = verify_ebx(raw)
            if not info["byte_identical"]:
                raise ValueError("资产不能逐字节往返")
            guids.update(t["guid"] for t in info["types"])
        originals[name], decoded[name] = current, reference
    identity = (digest(sdk_data), digest(manifest_data), tuple(sorted(guids)))
    reference_schemas = cached(("reference", *identity), lambda: Schemas.from_sdk(sdk_data, guids))
    # 与旧比较报告不同，这里不开放已知异常曲线的诊断读取策略。
    current_schemas = cached(("current", digest(shared_data), *identity),
                             lambda: SharedTypes(shared_data).adapt(sdk_data, guids))
    base = {"format": "fc27-fixed-edit-plan-v1", "export_manifest_sha256": digest(manifest_data),
            "sdk_sha256": digest(sdk_data), "shared_types_sha256": digest(shared_data)}
    files, modules, combined, total_edits, changed_bytes = {}, [], [], 0, 0
    review = io.StringIO(newline="")
    writer = csv.writer(review)
    writer.writerow(("module", "asset", "field", "format", "current_value", "reference_value", "expected_hex", "new_hex"))
    for module in recipe["modules"]:
        assets, plans = [], []
        for selection in module["assets"]:
            name = selection["name"]
            record = {"name": name, "requested_fields": selection["fields"]}
            try:
                if name in missing:
                    raise ValueError(missing[name])
                plan, details = prepare_transfer(originals[name], decoded[name], current_schemas,
                                                 reference_schemas, selection["fields"])
                record.update(details)
                if plan["edits"]:
                    plan["name"] = name
                    plans.append(plan)
                    total_edits += len(plan["edits"])
                    changed_bytes += details["changed_bytes"]
                    for field in details["fields"]:
                        for change in field["changes"]:
                            writer.writerow((module["id"], name, change["field"], change["format"],
                                             change["current_value"], change["reference_value"],
                                             change["expected_hex"], change["new_hex"]))
            except ValueError as exc:
                # 对一个资产采用原子筛选，不把部分不兼容字段偷偷漏掉后称为完整移植。
                record.update(status="rejected", reason=str(exc))
            assets.append(record)
        if plans:
            files["plans/" + module["id"] + ".json"] = json_bytes({**base, "assets": plans})
            combined.extend(plans)
        modules.append({"id": module["id"], "title": module["title"], "hypothesis": module["hypothesis"],
                        "assets": assets, "assets_ready": len(plans),
                        "assets_rejected": sum(a["status"] == "rejected" for a in assets),
                        "changed_values": sum(len(p["edits"]) for p in plans),
                        "gameplay_effect_verified": False})
    if total_edits > 8192:
        raise ValueError("研究计划修改超过定点编译上限")
    if combined:
        files["plans/combined.json"] = json_bytes({**base, "assets": combined})
    files["review.csv"] = review.getvalue().encode("utf-8-sig")
    files["recipe.json"] = recipe_data
    check_shared_source(shared_path, game_root, digest(shared_data))
    if any(digest(small_file(input_path(game_root, rel))) != sha for rel, sha in sources.items()):
        raise ValueError("研究期间游戏索引改变")
    summary = {"tool": "fc27-offline-whole-match-study", "recipe_id": recipe["id"],
               "selected_module": requested_module,
               "recipe_sha256": digest(recipe_data), "reference_package": profile.title,
               "reference_package_sha256": digest(sample_data), **base,
               "source_hashes": sources, "modules_reviewed": len(modules),
               "modules_with_candidates": sum(m["assets_ready"] > 0 for m in modules),
               "assets_requested": len(wanted), "assets_ready": len(combined),
               "assets_rejected": sum(m["assets_rejected"] for m in modules),
               "changed_values": total_edits, "changed_bytes": changed_bytes,
               "all_ready_edits_reverse_restore_verified": True,
               "comparison_includes_version_differences": True, "pure_mod_delta_known": False,
               "game_semantics_verified": False, "gameplay_effect_verified": False,
               "loadable_mod": False, "game_started_by_tool": False, "game_files_written": False}
    report = {"summary": summary, "modules": modules,
              "files": [{"path": name, "sha256": digest(raw), "bytes": len(raw)} for name, raw in sorted(files.items())]}
    return files, report


def run(game_root: Path, export_root: Path, sample_path: Path, sdk_path: Path,
        shared_path: Path, codec_path: Path, recipe_path: Path, output: Path, *, module: str = "all") -> dict:
    game_root = game_root.resolve(strict=True)
    export_root, sample_path, sdk_path, shared_path, codec_path = map(
        local_input, (export_root, sample_path, sdk_path, shared_path, codec_path))
    recipe_path = recipe_input(recipe_path)
    destination = output_path(output, game_root)
    files, report = prepare(game_root, export_root, sample_path, sdk_path, shared_path, codec_path, recipe_path, module)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    for name, raw in files.items():
        path = destination / safe_relative(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
    with (destination / "study-report.json").open("xb") as stream:
        stream.write(json_bytes(report))
    return report["summary"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("game-root", "fc27-export", "sample", "sdk", "shared-types", "codec", "recipe", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.game_root, args.fc27_export, args.sample, args.sdk, args.shared_types,
                             args.codec, args.recipe, args.output), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
