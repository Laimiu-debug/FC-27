"""严格对照前代参考，生成固定长度实验编辑；不推断优化方向。"""

from __future__ import annotations

import hashlib

from fc27_dbobject import FormatError
from fc27_edit import edit_values, validate_raw
from fc27_riff import verify_ebx
from fc27_schema import Schemas, named_values


CURVE_FIELDS = {"Points", "MinX", "MaxX"}
POINT_FIELDS = {"X", "Y", "InTangentOffsetX", "InTangentOffsetY",
                "OutTangentOffsetX", "OutTangentOffsetY", "CurveType"}
GEOMETRY = {"X", "InTangentOffsetX", "OutTangentOffsetX", "CurveType"}


def hashes_match(current: dict, reference: dict, names: set[str]):
    for name in names:
        a, b = current.get(name), reference.get(name)
        if not isinstance(a, str) or len(a) != 8 or a != b:
            raise FormatError("字段名称哈希缺失或不匹配：" + name)
        try:
            if f"{int(a, 16):08x}" != a:
                raise ValueError
        except ValueError as exc:
            raise FormatError("字段名称哈希不是规范编码") from exc


def type_proof(current: bytes, reference: bytes, schemas: Schemas, reference_schemas: Schemas,
               name: str, fields: set[str]) -> dict:
    def find(raw, mapping):
        matches = []
        for identity in verify_ebx(raw)["types"]:
            definition = mapping.types.get((identity["guid"], identity["signature"]))
            if definition and definition["namespace"] == "Frostbite.Core" and definition["name"] == name:
                matches.append((identity, definition))
        if len(matches) != 1:
            raise FormatError("曲线相关类型无法以完整 GUID/签名唯一核对")
        return matches[0]
    (a_id, a), (b_id, b) = find(current, schemas), find(reference, reference_schemas)
    if (a_id["guid"] != b_id["guid"] or a.get("name_hash") is None or
            a.get("name_hash") != b.get("name_hash") or a["size"] != b["size"] or
            a["alignment"] != b["alignment"]):
        raise FormatError("曲线相关类型的 GUID、名称哈希、大小或对齐不一致")
    if a_id != b_id and not hasattr(schemas, "shared_sha256"):
        raise FormatError("曲线类型签名变化没有当前共享描述依据")
    a_fields, b_fields = {p["name"]: p for p in a["properties"]}, {p["name"]: p for p in b["properties"]}
    if len(a_fields) != len(a["properties"]) or len(b_fields) != len(b["properties"]):
        raise FormatError("曲线相关类型存在重复字段名")
    if not fields <= a_fields.keys() or not fields <= b_fields.keys():
        raise FormatError("曲线相关类型缺少必须字段")
    hashes_match({n: p.get("name_hash") for n, p in a_fields.items()},
                 {n: p.get("name_hash") for n, p in b_fields.items()}, fields)
    if name == "FloatCurvePoint" and (set(a_fields) != fields or set(b_fields) != fields or a["size"] != 28):
        raise FormatError("仅支持完整七字段、28 字节的曲线点")
    return {"name": name, "current_identity": a_id, "reference_identity": b_id,
            "name_hash": a["name_hash"], "size": a["size"], "alignment": a["alignment"],
            "field_hashes": {n: a_fields[n]["name_hash"] for n in sorted(fields)}}


def prepare_transfer(current: bytes, reference: bytes, schemas: Schemas, reference_schemas: Schemas,
                     fields: list[str]) -> tuple[dict, dict]:
    """选择完整根字段，保留当前布局；曲线只移植 Y 及其 Y 切线。

    比较的是 FC27 原版与 FC26 已修改样本，包含版本差异。只有布局证明，
    没有数组列、时间单位、难度分支或实际游戏执行语义的证明。
    """
    if (not isinstance(fields, list) or not fields or
            any(not isinstance(n, str) or not n or "." in n or "[" in n for n in fields) or
            len(set(fields)) != len(fields)):
        raise FormatError("研究选择须为唯一的完整根字段名")
    if getattr(schemas, "allow_legacy_curve_metadata", False) or getattr(reference_schemas, "allow_legacy_curve_metadata", False):
        raise FormatError("实验计划不允许异常元数据诊断策略")
    target, source = named_values(current, schemas), named_values(reference, reference_schemas)
    if (target["root_identity"]["guid"] != source["root_identity"]["guid"] or
            target["root_type"] != source["root_type"] or target["root_name_hash"] is None or
            target["root_name_hash"] != source["root_name_hash"]):
        raise FormatError("根类型 GUID 或名称哈希不能对应")
    if target["root_identity"] != source["root_identity"] and not target.get("shared_types_sha256"):
        raise FormatError("根类型签名变化没有当前共享描述依据")
    hashes_match(target["field_name_hashes"], source["field_name_hashes"], set(fields))
    if target["metadata_warnings"] or source["metadata_warnings"]:
        raise FormatError("实验计划不接受带有元数据警告的资产")
    if set(fields) & (set(target["unresolved_names"]) | set(source["unresolved_names"])):
        raise FormatError("选中字段含未核对名称")
    edits, field_reports, proofs = [], [], []
    for name in fields:
        shape, old_shape = target["shapes"].get(name), source["shapes"].get(name)
        if shape is None or shape != old_shape:
            raise FormatError("完整字段形状或数组标识不同：" + name)
        kind = shape["kind"]
        if kind in ("float32", "numeric") and shape.get("format") in ("<f", "<d"):
            paths = [name]
        elif kind == "float_array" and shape.get("format") == "<f" and shape["count"] > 0:
            paths = [f"{name}[{i}]" for i in range(shape["count"])]
        elif kind == "float_curve" and shape["count"] > 0 and shape["point_size"] == 28:
            if not proofs:
                proofs = [type_proof(current, reference, schemas, reference_schemas, "FloatCurve", CURVE_FIELDS),
                          type_proof(current, reference, schemas, reference_schemas, "FloatCurvePoint", POINT_FIELDS)]
            geometric = [name + ".MinX", name + ".MaxX"]
            geometric += [f"{name}.Points[{i}].{p}" for i in range(shape["count"]) for p in sorted(GEOMETRY)]
            for path in geometric:
                a, b = target["leaves"].get(path), source["leaves"].get(path)
                if a is None or b is None or a["format"] != b["format"] or a["raw_hex"] != b["raw_hex"]:
                    raise FormatError("曲线定义域、X、X 切线或 CurveType 不同：" + path)
                validate_raw(bytes.fromhex(a["raw_hex"]), a["format"])
            paths = [f"{name}.Points[{i}].{p}" for i in range(shape["count"])
                     for p in ("Y", "InTangentOffsetY", "OutTangentOffsetY")]
        else:
            raise FormatError("研究移植尚不支持该字段种类：" + name)
        field_changes = []
        for path in paths:
            a, b = target["leaves"].get(path), source["leaves"].get(path)
            if a is None or b is None or a["format"] != b["format"] or a["format"] not in ("<f", "<d"):
                raise FormatError("目标数值路径或编码不同：" + path)
            validate_raw(bytes.fromhex(a["raw_hex"]), a["format"])
            validate_raw(bytes.fromhex(b["raw_hex"]), b["format"])
            if a["raw_hex"] != b["raw_hex"]:
                item = {"field": path, "expected_hex": a["raw_hex"], "new_hex": b["raw_hex"]}
                edits.append(item)
                field_changes.append({**item, "current_value": a["value"], "reference_value": b["value"], "format": a["format"]})
        field_reports.append({"field": name, "name_hash": target["field_name_hashes"][name],
                              "shape": shape, "changed_values": len(field_changes), "changes": field_changes})
    identity = target["root_identity"]
    baseline = hashlib.sha256(current).hexdigest()
    plan = {"expected_sha256": baseline, "root_identity": identity, "edits": edits}
    report = {"current_root_identity": identity, "reference_root_identity": source["root_identity"],
              "root_name_hash": target["root_name_hash"], "curve_type_proofs": proofs,
              "fields": field_reports, "reference_sha256": hashlib.sha256(reference).hexdigest(),
              "comparison_includes_version_differences": True, "game_semantics_verified": False,
              "gameplay_effect_verified": False, "status": "unchanged" if not edits else "ready"}
    if edits:
        # 计划由原始字节和布局重新生成，且必须通过现有编译器的完整干跑/恢复。
        _, validation = edit_values(current, schemas, baseline, edits, identity)
        report.update(changed_values=validation["changed_values"], changed_bytes=validation["changed_bytes"],
                      reverse_restore_verified=validation["reverse_restore_verified"])
    return plan, report
