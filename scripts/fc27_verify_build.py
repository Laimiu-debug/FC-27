"""只读校验已生成的离线候选目录，确认每个候选均可逐字节恢复。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from fc27_build import PLAN_KEYS, digest, local_input, strict_json
from fc27_assets import decompress_cas, input_path, small_file
from fc27_edit import replacement, revert_values


def run(bundle: Path) -> dict:
    bundle = local_input(bundle)
    report = strict_json(small_file(bundle / "build-report.json"))
    summary, assets = report["summary"], report["assets"]
    if summary["tool"] != "fc27-offline-fixed-build" or summary["loadable_mod"] is not False:
        raise ValueError("不是受支持的离线构建目录")
    plan_data = small_file(bundle / "plan.json")
    if digest(plan_data) != summary["plan_sha256"]:
        raise ValueError("构建目录的计划副本已改变")
    plan = strict_json(plan_data)
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS or plan["format"] != "fc27-fixed-edit-plan-v1":
        raise ValueError("构建目录的计划格式不同")
    for key in ("export_manifest_sha256", "sdk_sha256", "shared_types_sha256"):
        if plan[key] != summary[key]:
            raise ValueError("计划的来源散列与构建汇总不同")
    if not isinstance(assets, list) or not 0 < len(assets) <= 64:
        raise ValueError("构建目录的资产列表为空或过大")
    planned = {}
    for entry in plan["assets"]:
        if entry["name"] in planned:
            raise ValueError("构建计划包含重复资产")
        planned[entry["name"]] = entry
    names, values, changed_bytes = set(), 0, 0
    for asset in assets:
        name = asset["resource"]
        if name in names:
            raise ValueError("构建报告包含重复资产")
        names.add(name)
        if name not in planned:
            raise ValueError("构建报告包含计划外资产")
        entry = planned[name]
        if asset["original_sha256"] != entry["expected_sha256"] or asset["root_identity"] != entry["root_identity"]:
            raise ValueError("构建报告与计划的资产身份不同")
        changes = {item["field"]: item for item in asset["changes"]}
        edits = {item["field"]: item for item in entry["edits"]}
        if len(changes) != len(asset["changes"]) or len(edits) != len(entry["edits"]) or set(changes) != set(edits):
            raise ValueError("构建报告与计划的编辑字段不同")
        for field, change in changes.items():
            item = edits[field]
            if (change["before_hex"] != item["expected_hex"] or
                    change["after_hex"] != replacement(item, change["format"]).hex()):
                raise ValueError("构建报告与计划的候选原始位不同")
        original = small_file(input_path(bundle, "original-assets/" + name + ".ebx"))
        candidate = small_file(input_path(bundle, "candidate-assets/" + name + ".ebx"))
        blocks = small_file(input_path(bundle, "casblocks/" + name + ".casblocks"))
        if digest(original) != asset["original_sha256"] or len(original) != len(candidate):
            raise ValueError("原始副本散列或候选长度不同")
        if len(blocks) != asset["cas_blocks_bytes"] or hashlib.sha1(blocks).hexdigest() != asset["cas_blocks_sha1"]:
            raise ValueError("候选 CAS 压缩块已改变")
        if decompress_cas(blocks, len(candidate)) != candidate or revert_values(candidate, asset) != original:
            raise ValueError("候选不能解压或逐字节恢复")
        actual_changes = sum(a != b for a, b in zip(original, candidate))
        if actual_changes != asset["changed_bytes"] or len(asset["changes"]) != asset["changed_values"]:
            raise ValueError("候选修改数量与报告不同")
        values += len(asset["changes"])
        changed_bytes += actual_changes
    if names != set(planned) or (len(assets), values, changed_bytes) != (summary["assets_built"], summary["changed_values"], summary["changed_bytes"]):
        raise ValueError("汇总数量与实际资产不同")
    return {"assets_verified": len(assets), "changed_values": values, "changed_bytes": changed_bytes,
            "all_reverse_restores_verified": True, "game_files_written": False, "loadable_mod": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.bundle), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
