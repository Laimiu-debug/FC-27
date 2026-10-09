"""依据已核对字段布局定点修改数值，验证候选与反向恢复；不生成游戏模组。"""

from __future__ import annotations

import hashlib
import math
import struct

from fc27_assets import checked_range, decompress_cas
from fc27_candidate import encode_uncompressed_blocks
from fc27_dbobject import FormatError
from fc27_riff import RiffDocument, verify_ebx
from fc27_schema import NUMERIC_TYPES, Schemas, named_values


FORMATS = {item[0] for item in NUMERIC_TYPES.values()}
MAX_EDITS = 8192


def hex_bytes(value: str, size: int) -> bytes:
    if not isinstance(value, str) or len(value) != size * 2:
        raise FormatError("字段原始位的长度不符")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise FormatError("字段原始位不是十六进制") from exc
    if raw.hex() != value:
        raise FormatError("原始位必须使用规范的小写十六进制")
    return raw


def validate_raw(raw: bytes, fmt: str):
    value = struct.unpack(fmt, raw)[0]
    if fmt in ("<f", "<d") and not math.isfinite(value):
        raise FormatError("浮点候选或基线不是有限数")
    if fmt == "<?" and raw not in (b"\0", b"\x01"):
        raise FormatError("布尔值必须编码为 00 或 01")
    return value


def replacement(edit: dict, fmt: str) -> bytes:
    if not isinstance(fmt, str) or fmt not in FORMATS:
        raise FormatError("候选数值编码尚未支持")
    if "new_hex" in edit:
        raw = hex_bytes(edit["new_hex"], struct.calcsize(fmt))
    else:
        value = edit["value"]
        if fmt == "<?":
            valid = type(value) is bool
        elif fmt in ("<f", "<d"):
            valid = type(value) in (int, float)
        else:
            valid = type(value) is int
        if not valid:
            raise FormatError("候选值的类型与字段数值类型不同")
        try:
            raw = struct.pack(fmt, value)
        except (OverflowError, struct.error) as exc:
            raise FormatError("候选值超出字段的数值范围") from exc
    validate_raw(raw, fmt)
    return raw


def revert_values(candidate: bytes, report: dict) -> bytes:
    """仅恢复已核对散列的候选副本，原始散列必须逐字节匹配。"""
    if report.get("kind") != "fc27-fixed-numeric-edit-v1":
        raise FormatError("不是受支持的定点编辑报告")
    if hashlib.sha256(candidate).hexdigest() != report.get("candidate_sha256"):
        raise FormatError("候选副本已改变，拒绝恢复")
    changes = report.get("changes")
    if not isinstance(changes, list) or not 0 < len(changes) <= MAX_EDITS:
        raise FormatError("恢复清单为空或过大")
    result, occupied = bytearray(candidate), set()
    info = verify_ebx(candidate)
    start, end = info["payload_file_offset"], info["payload_file_offset"] + info["payload_size"]
    for change in changes:
        fmt, offset = change.get("format"), change.get("offset")
        if fmt not in FORMATS or type(offset) is not int:
            raise FormatError("恢复清单的类型或偏移无效")
        width = struct.calcsize(fmt)
        if not start <= offset <= end - width:
            raise FormatError("恢复位置超出 EBXD 载荷")
        affected = set(range(offset, offset + width))
        if occupied & affected:
            raise FormatError("恢复清单的字节位置重叠")
        occupied.update(affected)
        before, after = hex_bytes(change.get("before_hex"), width), hex_bytes(change.get("after_hex"), width)
        validate_raw(before, fmt)
        validate_raw(after, fmt)
        if checked_range(candidate, offset, width) != after:
            raise FormatError("候选副本的待恢复字节不符")
        result[offset:offset + width] = before
    restored = bytes(result)
    if hashlib.sha256(restored).hexdigest() != report.get("original_sha256"):
        raise FormatError("反向恢复没有得到原始散列")
    if not verify_ebx(restored)["byte_identical"]:
        raise FormatError("恢复副本的 RIFF 封装不一致")
    return restored


def edit_values(current: bytes, schemas: Schemas, expected_sha256: str,
                edits: list[dict], root_identity: dict | None = None) -> tuple[bytes, dict]:
    if hashlib.sha256(current).hexdigest() != expected_sha256:
        raise FormatError("基线散列改变，拒绝执行编辑计划")
    if not isinstance(edits, list) or not 0 < len(edits) <= MAX_EDITS:
        raise FormatError("编辑计划为空或过大")
    if getattr(schemas, "allow_legacy_curve_metadata", False):
        raise FormatError("参考文件的异常诊断策略不能用于候选编辑")
    mapped = named_values(current, schemas)
    if root_identity is not None and mapped["root_identity"] != root_identity:
        raise FormatError("计划与当前完整根类型身份不同")
    if mapped["metadata_warnings"]:
        raise FormatError("含元数据异常的资产不能用于候选编辑")
    result, selected, changes = bytearray(current), set(), []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) not in (
                {"field", "expected_hex", "value"}, {"field", "expected_hex", "new_hex"}):
            raise FormatError("编辑项必须包含字段、预期原始位和唯一候选值")
        path = edit["field"]
        if not isinstance(path, str) or path not in mapped["leaves"] or path in selected:
            raise FormatError("编辑字段不存在、未支持或重复")
        owners = [name for name in mapped["shapes"]
                  if path == name or path.startswith(name + "[") or path.startswith(name + ".")]
        if len(owners) != 1 or owners[0] in mapped["unresolved_names"]:
            raise FormatError("编辑字段没有唯一且已核对的名称")
        selected.add(path)
        leaf = mapped["leaves"][path]
        fmt, offset = leaf["format"], leaf["offset"]
        if fmt not in FORMATS:
            raise FormatError("编辑字段的数值编码尚未支持")
        width = struct.calcsize(fmt)
        before = hex_bytes(edit["expected_hex"], width)
        if before.hex() != leaf["raw_hex"]:
            raise FormatError("字段的预期原始位已改变")
        validate_raw(before, fmt)
        after = replacement(edit, fmt)
        if before == after:
            raise FormatError("编辑项没有改变任何原始位")
        # 不允许通过一个字段改变另一字段的别名，包括未选中的别名。
        for other_path, other in mapped["leaves"].items():
            other_start = other["offset"]
            if other_path != path and offset < other_start + struct.calcsize(other["format"]) and other_start < offset + width:
                raise FormatError("字段与其他数值位置重叠，拒绝别名写入")
        result[offset:offset + width] = after
        changes.append({"field": path, "root_field": owners[0],
                        "field_name_hash": mapped["field_name_hashes"][owners[0]],
                        "offset": offset, "format": fmt, "before_hex": before.hex(), "after_hex": after.hex(),
                        "before": validate_raw(before, fmt), "after": validate_raw(after, fmt)})
    candidate = bytes(result)
    occupied = {p for item in changes for p in range(item["offset"], item["offset"] + struct.calcsize(item["format"]))}
    actual = {i for i, (a, b) in enumerate(zip(current, candidate)) if a != b}
    if len(candidate) != len(current) or not actual <= occupied:
        raise FormatError("编辑超出数值位置或改变文件长度")
    old_chunks, new_chunks = RiffDocument.parse(current).chunks, RiffDocument.parse(candidate).chunks
    if len(old_chunks) != len(new_chunks) or any(a != b for a, b in zip(old_chunks, new_chunks) if a.name != b"EBXD"):
        raise FormatError("编辑改变了引用表、不透明块或填充")
    rebuilt = named_values(candidate, schemas)
    if rebuilt["shapes"] != mapped["shapes"] or rebuilt["root_identity"] != mapped["root_identity"]:
        raise FormatError("候选的字段形状或身份已改变")
    expected = {item["field"]: item["after_hex"] for item in changes}
    for path, leaf in mapped["leaves"].items():
        if rebuilt["leaves"].get(path, {}).get("raw_hex") != expected.get(path, leaf["raw_hex"]):
            raise FormatError("候选修改影响了未选字段或重新读取结果不符")
    blocks = encode_uncompressed_blocks(candidate)
    if not verify_ebx(candidate)["byte_identical"] or decompress_cas(blocks, len(candidate)) != candidate:
        raise FormatError("候选 EBX 或 CAS 压缩块不能逐字节读写")
    report = {"kind": "fc27-fixed-numeric-edit-v1", "loadable_mod": False,
              "game_started_by_tool": False, "game_files_written": False, "gameplay_effect_verified": False,
              "game_semantics_verified": False, "root_type": mapped["root_type"],
              "root_identity": mapped["root_identity"], "sdk_sha256": mapped["sdk_sha256"],
              "shared_types_sha256": mapped["shared_types_sha256"],
              "original_sha256": expected_sha256, "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
              "cas_blocks_sha1": hashlib.sha1(blocks).hexdigest(), "cas_blocks_bytes": len(blocks),
              "changed_values": len(changes), "changed_bytes": len(actual), "changes": changes,
              "reference_tables_unchanged": True, "unselected_supported_values_unchanged": True,
              "ebx_and_cas_roundtrips_verified": True}
    if revert_values(candidate, report) != current:
        raise FormatError("候选不能逐字节恢复为原始资产")
    report["reverse_restore_verified"] = True
    return candidate, report
