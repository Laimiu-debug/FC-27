"""在内存中编译 FC27 当前索引副本；原始封装头不解释、不重新签名。"""

from __future__ import annotations

import struct

from fc27_assets import (Bundle, Location, checked_range, field, read_bundle, read_toc)
from fc27_dbobject import Document, FormatError, HEADER_SIZE, MAX_FILE_BYTES


def patch_binary_bundle(data: bytes, bundle: Bundle, toc: str, replacements: dict) -> tuple[bytes, dict[str, int]]:
    """只改选定 EBX 的 SHA1 与 originalSize，保留 RES/Chunk/字符串等字节。"""
    records = read_bundle(data, bundle, toc)
    endian = next(e for e in ("<", ">") if struct.unpack_from(e + "I", data, 4)[0] ^ 0x7065636E == 0xED1CEDB8)
    count = struct.unpack_from(endian + "I", data, 8)[0]
    result, indexes, allowed = bytearray(data), {}, set()
    for index, record in enumerate(records):
        if record.name not in replacements:
            continue
        if record.name in indexes:
            raise FormatError("同一 Bundle 的目标 EBX 名重复")
        item = replacements[record.name]
        if record.sha1 != item["expected_sha1"] or record.original_size != item["original_size"]:
            raise FormatError("Bundle 中的目标基线 SHA1 或尺寸不同")
        sha1 = bytes.fromhex(item["sha1"])
        if len(sha1) != 20 or not 0 < item["original_size"] <= MAX_FILE_BYTES:
            raise FormatError("候选 SHA1 或尺寸无效")
        start = 36 + 20 * index
        result[start:start + 20] = sha1
        size_offset = 36 + 20 * count + 8 * index + 4
        struct.pack_into(endian + "I", result, size_offset, item["original_size"])
        allowed.update(range(start, start + 20))
        allowed.update(range(size_offset, size_offset + 4))
        indexes[record.name] = index + 1  # 位置 0 是元数据本身。
    if not indexes:
        raise FormatError("Bundle 没有选中的资产")
    if any(a != b and i not in allowed for i, (a, b) in enumerate(zip(data, result))):
        raise FormatError("Bundle 修改越出选定字段")
    updated = read_bundle(bytes(result), bundle, toc)
    for before, after in zip(records, updated):
        if before.name in indexes:
            if after.sha1 != replacements[before.name]["sha1"]:
                raise FormatError("Bundle 候选散列回读不同")
        elif before != after:
            raise FormatError("未选资产的 Bundle 元数据改变")
    return bytes(result), indexes


def patch_toc(data: bytes, replacements: dict[int, tuple[Location, ...]]) -> bytes:
    """追加修改后的位置块，原块保留；只改对应表项的尺寸和相对位置。"""
    before = read_toc(data)
    if not replacements or not set(replacements).issubset(range(len(before))):
        raise FormatError("TOC 的目标 Bundle 索引无效")
    header = struct.unpack_from(">15I", data, HEADER_SIZE)
    result, allowed = bytearray(data), set()
    for index, locations in sorted(replacements.items()):
        bundle = before[index]
        if len(locations) != len(bundle.locations):
            raise FormatError("定点索引不允许资源数量变化")
        table_offset = HEADER_SIZE + header[1] + index * 16
        _, size, relative = struct.unpack_from(">IIQ", data, table_offset)
        block = checked_range(data, HEADER_SIZE + relative, size & 0x3FFFFFFF)
        head = list(struct.unpack_from(">9I", block))
        if head[:2] != [0, 0] or head[4:8] != [36, 36, 36, 0]:
            raise FormatError("仅支持当前非内联的 36 字节位置块")
        flags = block[head[2]:]
        entries, new_flags, current = bytearray(), bytearray(), None
        for old_flag, location in zip(flags, locations):
            if (location.cas_id >> 48 not in (0, 1) or not location.cas_id & 0xFFFF or
                    not 0 <= location.offset <= 0xFFFFFFFF or not 0 < location.size <= MAX_FILE_BYTES):
                raise FormatError("候选 CAS 标识或范围无效")
            # 原有 0x84 标记保留，不推断其中未知位的语义。
            flag = old_flag or (0x80 if current != location.cas_id else 0)
            if flag:
                entries += struct.pack(">Q", location.cas_id)
            current = location.cas_id
            entries += struct.pack(">II", location.offset, location.size)
            new_flags.append(flag)
        head[2] = 36 + len(entries)
        new_block = struct.pack(">9I", *head) + entries + new_flags
        result += bytes((-len(result)) % 4)
        new_relative = len(result) - HEADER_SIZE
        result += new_block
        struct.pack_into(">IQ", result, table_offset + 4, 0x40000000 | len(new_block), new_relative)
        allowed.update(range(table_offset + 4, table_offset + 16))
        if len(result) > MAX_FILE_BYTES:
            raise FormatError("候选 TOC 超过大小上限")
    if any(a != b and i not in allowed for i, (a, b) in enumerate(zip(data, result))):
        raise FormatError("TOC 原内容越出允许表项发生修改")
    after = read_toc(bytes(result))
    for index, bundle in enumerate(after):
        if bundle.locations != replacements.get(index, before[index].locations):
            raise FormatError("TOC 候选位置回读不同")
    return bytes(result)


def layout_cas_ids(data: bytes) -> list[int]:
    blob = field(Document.parse(data).root, "layeredInstallChunkFiles")
    if blob.kind != 19 or len(blob.data) % 8:
        raise FormatError("分层 CAS 注册表不是完整 UInt64 Blob")
    values = [i[0] for i in struct.iter_unpack("<Q", blob.data)]
    if len(set(values)) != len(values) or any(i >> 48 not in (0, 1) or not i & 0xFFFF for i in values):
        raise FormatError("分层 CAS 注册表有重复或未知标识")
    return values


def add_layout_cas(data: bytes, identifiers: set[int]) -> bytes:
    original = layout_cas_ids(data)
    if (not identifiers or set(original) & identifiers or
            any(i >> 48 not in (0, 1) or not i & 0xFFFF for i in identifiers)):
        raise FormatError("新增 CAS 标识为空、冲突或非法")
    doc = Document.parse(data)
    if doc.compile() != data:
        raise FormatError("layout 基线不能逐字节往返")
    blob = field(doc.root, "layeredInstallChunkFiles")
    saved = blob.data
    blob.data = b"".join(struct.pack("<Q", i) for i in sorted(set(original) | identifiers))
    result = doc.compile()
    verify = Document.parse(result)
    if layout_cas_ids(result) != sorted(set(original) | identifiers):
        raise FormatError("layout CAS 注册表回读不同")
    field(verify.root, "layeredInstallChunkFiles").data = saved
    if verify.compile() != data:
        raise FormatError("layout 非目标字段改变")
    return result
