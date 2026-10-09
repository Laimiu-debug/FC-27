"""固定长度的离线字段移植实验；输出 EBX/压缩块，不输出可安装模组。"""

from __future__ import annotations

import hashlib
import math
import struct

from fc27_assets import decompress_cas
from fc27_dbobject import FormatError, MAX_FILE_BYTES
from fc27_riff import RiffDocument, verify_ebx
from fc27_schema import Schemas, named_values


def encode_uncompressed_blocks(data: bytes) -> bytes:
    if not 0 < len(data) <= MAX_FILE_BYTES:
        raise FormatError("压缩块输入长度超出上限")
    result = bytearray()
    for offset in range(0, len(data), 65536):
        block = data[offset:offset + 65536]
        packed = len(block) << 32 | 7 << 20 | len(block)
        result.extend(struct.pack(">Q", packed))
        result.extend(block)
    return bytes(result)


def transfer_arrays(current: bytes, reference: bytes, schemas: Schemas,
                    fields: list[str], expected_sha256: str,
                    reference_schemas: Schemas | None = None) -> tuple[bytes, dict]:
    if hashlib.sha256(current).hexdigest() != expected_sha256:
        raise FormatError("FC27 基线散列改变，拒绝套用字段修改")
    if not fields or len(set(fields)) != len(fields):
        raise FormatError("候选字段为空或重复")
    target, source = named_values(current, schemas), named_values(reference, reference_schemas or schemas)
    if target["root_identity"] != source["root_identity"]:
        raise FormatError("前代与 FC27 的根类型 GUID/签名不一致")
    result = bytearray(current)
    positions, changes, selected_shapes = set(), [], {}
    for name in fields:
        if name not in target["shapes"] or name not in source["shapes"]:
            raise FormatError("候选字段没有经过名称和位置映射")
        shape, old_shape = target["shapes"][name], source["shapes"][name]
        if shape != old_shape or shape["kind"] != "float_array":
            raise FormatError("当前实验只接受完全匹配的 Float32 数组结构和标识")
        selected_shapes[name] = shape
        for i in range(shape["count"]):
            path = f"{name}[{i}]"
            before, after = target["leaves"][path], source["leaves"][path]
            if not math.isfinite(after["value"]):
                raise FormatError("候选值不是有限数")
            position = before["offset"]
            affected = set(range(position, position + 4))
            if positions & affected:
                raise FormatError("多个候选字段引用了相同字节，拒绝覆盖")
            positions.update(affected)
            raw = bytes.fromhex(after["raw_hex"])
            result[position:position + 4] = raw
            if before["raw_hex"] != after["raw_hex"]:
                changes.append({"field": path, "offset": position, "before": before["value"],
                                "after": after["value"], "before_hex": before["raw_hex"],
                                "after_hex": after["raw_hex"]})
    candidate = bytes(result)
    actual_changes = {i for i, (a, b) in enumerate(zip(current, candidate)) if a != b}
    if len(candidate) != len(current) or not actual_changes <= positions:
        raise FormatError("候选修改超出允许字节范围")
    for old, new in zip(RiffDocument.parse(current).chunks, RiffDocument.parse(candidate).chunks):
        if old.name != b"EBXD" and old != new:
            raise FormatError("候选修改改变了引用表或其他不透明块")
    if not verify_ebx(candidate)["byte_identical"]:
        raise FormatError("候选 EBX 封装读写不一致")
    mapped_candidate = named_values(candidate, schemas)
    for change in changes:
        if mapped_candidate["leaves"][change["field"]]["raw_hex"] != change["after_hex"]:
            raise FormatError("候选重新读取后的字段值不符")
    blocks = encode_uncompressed_blocks(candidate)
    if decompress_cas(blocks, len(candidate)) != candidate:
        raise FormatError("候选压缩块读写不一致")
    report = {
        "kind": "offline-structural-transfer-experiment", "loadable_mod": False,
        "game_started_by_tool": False, "game_files_written": False, "gameplay_effect_verified": False,
        "column_semantics_verified": False, "sdk_version": target["sdk_version"],
        "shared_types_sha256": target["shared_types_sha256"],
        "root_identity": target["root_identity"], "sdk_sha256": schemas.sdk_sha256,
        "original_sha256": expected_sha256, "reference_sha256": hashlib.sha256(reference).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "cas_blocks_sha1": hashlib.sha1(blocks).hexdigest(), "cas_blocks_bytes": len(blocks),
        "selected_fields": selected_shapes, "changed_values": len(changes),
        "changed_bytes": len(actual_changes), "changes": changes,
        "reference_tables_unchanged": True, "ebx_roundtrip_verified": True,
        "cas_block_roundtrip_verified": True,
    }
    return candidate, report
