"""只读提取 SDK 的 CLR 元数据属性；不加载或执行 DLL。"""

from __future__ import annotations

import struct
import uuid

from fc27_assets import checked_range
from fc27_dbobject import FormatError, MAX_FILE_BYTES


def compressed_integer(data: bytes, offset: int) -> tuple[int, int]:
    first = checked_range(data, offset, 1)[0]
    if first < 0x80:
        return first, offset + 1
    if first < 0xC0:
        raw = checked_range(data, offset, 2)
        value = ((raw[0] & 0x3F) << 8) | raw[1]
        if value < 0x80:
            raise FormatError("CLR 整数不是最短编码")
        return value, offset + 2
    if first < 0xE0:
        raw = checked_range(data, offset, 4)
        value = ((raw[0] & 0x1F) << 24) | int.from_bytes(raw[1:], "big")
        if value < 0x4000:
            raise FormatError("CLR 整数不是最短编码")
        return value, offset + 4
    raise FormatError("无效 CLR 压缩整数")


def serialized_string(data: bytes, offset: int) -> tuple[str | None, int]:
    if checked_range(data, offset, 1) == b"\xFF":
        return None, offset + 1
    length, position = compressed_integer(data, offset)
    try:
        value = checked_range(data, position, length).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise FormatError("CLR 字符串不是 UTF-8") from exc
    return value, position + length


def pe_metadata(data: bytes) -> bytes:
    if len(data) > MAX_FILE_BYTES or checked_range(data, 0, 2) != b"MZ":
        raise FormatError("不是支持大小的 PE 文件")
    pe_offset = struct.unpack("<I", checked_range(data, 60, 4))[0]
    if checked_range(data, pe_offset, 4) != b"PE\0\0":
        raise FormatError("缺少 PE 签名")
    sections = struct.unpack("<H", checked_range(data, pe_offset + 6, 2))[0]
    optional_size = struct.unpack("<H", checked_range(data, pe_offset + 20, 2))[0]
    optional = checked_range(data, pe_offset + 24, optional_size)
    magic = struct.unpack("<H", checked_range(optional, 0, 2))[0]
    directory_offset = {0x10B: 96, 0x20B: 112}.get(magic)
    if directory_offset is None:
        raise FormatError("不支持的 PE 可选头")
    count = struct.unpack("<I", checked_range(optional, directory_offset - 4, 4))[0]
    if count < 15 or not 1 <= sections <= 96:
        raise FormatError("PE 缺少 CLR 数据目录")
    clr_rva, _ = struct.unpack("<2I", checked_range(optional, directory_offset + 14*8, 8))
    section_table = checked_range(data, pe_offset + 24 + optional_size, sections * 40)

    def read_rva(rva: int, size: int) -> bytes:
        for i in range(sections):
            _, address, raw_size, pointer = struct.unpack_from("<4I", section_table, i*40 + 8)
            if address <= rva and rva - address + size <= raw_size:
                return checked_range(data, pointer + rva - address, size)
        raise FormatError("CLR RVA 没有有效的磁盘映射")

    clr = read_rva(clr_rva, 16)
    rva, size = struct.unpack_from("<2I", clr, 8)
    return read_rva(rva, size)


class Metadata:
    def __init__(self, metadata: bytes):
        if checked_range(metadata, 0, 4) != b"BSJB":
            raise FormatError("缺少 CLR 元数据签名")
        version_length = struct.unpack("<I", checked_range(metadata, 12, 4))[0]
        position = (16 + version_length + 3) & ~3
        _, stream_count = struct.unpack("<2H", checked_range(metadata, position, 4))
        position += 4
        if stream_count > 32:
            raise FormatError("CLR 流数量过多")
        self.streams = {}
        for _ in range(stream_count):
            offset, size = struct.unpack("<2I", checked_range(metadata, position, 8))
            position += 8
            end = metadata.find(b"\0", position, min(len(metadata), position + 32))
            if end < 0:
                raise FormatError("CLR 流名称未结束")
            name = metadata[position:end]
            if name in self.streams:
                raise FormatError("重复 CLR 流")
            self.streams[name] = checked_range(metadata, offset, size)
            position = (end + 4) & ~3
        if not all(n in self.streams for n in (b"#~", b"#Strings", b"#Blob")):
            raise FormatError("仅支持包含 #~ 表的 CLR 元数据")
        self.tables = self.streams[b"#~"]
        checked_range(self.tables, 0, 24)
        heap = self.tables[6]
        valid = struct.unpack_from("<Q", self.tables, 8)[0]
        position = 24
        self.counts = {}
        for i in range(64):
            if valid >> i & 1:
                count = struct.unpack("<I", checked_range(self.tables, position, 4))[0]
                position += 4
                if count > 2_000_000:
                    raise FormatError("CLR 表过大")
                self.counts[i] = count

        def index(table):
            return 4 if self.counts.get(table, 0) >= 65536 else 2

        def coded(bits, *tables):
            return 4 if max(self.counts.get(t, 0) for t in tables) >= 1 << (16-bits) else 2

        string, guid, blob = (4 if heap & mask else 2 for mask in (1, 2, 4))
        type_ref = coded(2, 2, 1, 27)
        custom_parent = coded(5, 6, 4, 1, 2, 8, 9, 10, 0, 14, 23, 20, 17,
                              26, 27, 32, 35, 38, 39, 40, 42, 44, 43)
        layouts = {
            0: [2, string, guid, guid, guid], 1: [coded(2, 0, 26, 35, 1), string, string],
            2: [4, string, string, type_ref, index(4), index(6)], 3: [index(4)],
            4: [2, string, blob], 5: [index(6)], 6: [4, 2, 2, string, blob, index(8)],
            7: [index(8)], 8: [2, 2, string], 9: [index(2), type_ref],
            10: [coded(3, 2, 1, 26, 6, 27), string, blob],
            11: [2, coded(2, 4, 8, 23), blob], 12: [custom_parent, coded(3, 6, 10), blob],
            13: [coded(1, 4, 8), blob], 14: [2, coded(2, 2, 6, 32), blob],
            15: [2, 4, index(2)], 16: [4, index(4)], 17: [blob],
            18: [index(2), index(20)], 19: [index(20)], 20: [2, string, type_ref],
            21: [index(2), index(23)], 22: [index(23)], 23: [2, string, blob],
        }
        self.row_formats = {}
        # 只计算本组件需要的 0..23 表；后续表的行数参与编码索引宽度计算。
        for i in range(24):
            if self.counts.get(i, 0):
                widths = layouts[i]
                size = sum(widths)
                checked_range(self.tables, position, size * self.counts[i])
                self.row_formats[i] = position, size, "<" + "".join("H" if w == 2 else "I" for w in widths)
                position += size * self.counts[i]

    def row(self, table: int, row: int) -> tuple[int, ...]:
        if not 1 <= row <= self.counts.get(table, 0):
            raise FormatError("CLR 表行索引越界")
        offset, size, fmt = self.row_formats[table]
        return struct.unpack_from(fmt, self.tables, offset + (row-1)*size)

    def string(self, offset: int) -> str:
        heap = self.streams[b"#Strings"]
        checked_range(heap, offset, 1)
        end = heap.find(b"\0", offset, min(len(heap), offset + 4097))
        if end < 0:
            raise FormatError("CLR 字符串截断或过长")
        try:
            return heap[offset:end].decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise FormatError("CLR 名称不是 UTF-8") from exc

    def blob(self, offset: int) -> bytes:
        heap = self.streams[b"#Blob"]
        size, position = compressed_integer(heap, offset)
        return checked_range(heap, position, size)

    def property_array_element(self, signature: bytes) -> str | None:
        """只解析无参数属性的集合元素类型，其他属性不展开。"""
        if checked_range(signature, 0, 1)[0] & 15 != 8:
            raise FormatError("不是 CLR 属性签名")
        count, position = compressed_integer(signature, 1)
        if count != 0 or checked_range(signature, position, 1)[0] != 0x15:
            return None
        position += 1
        if checked_range(signature, position, 1)[0] not in (0x11, 0x12):
            raise FormatError("CLR 泛型不是 class/value type")
        _, position = compressed_integer(signature, position + 1)
        arguments, position = compressed_integer(signature, position)
        if arguments != 1:
            return None
        kind = checked_range(signature, position, 1)[0]
        primitives = {2: "System.Boolean", 4: "System.SByte", 5: "System.Byte",
                      6: "System.Int16", 7: "System.UInt16", 8: "System.Int32",
                      9: "System.UInt32", 10: "System.Int64", 11: "System.UInt64",
                      12: "System.Single", 13: "System.Double"}
        if kind in primitives:
            if position + 1 != len(signature):
                raise FormatError("CLR 集合属性签名尾部异常")
            return primitives[kind]
        if kind not in (0x11, 0x12):
            return None
        reference, position = compressed_integer(signature, position + 1)
        if position != len(signature):
            raise FormatError("CLR 集合属性签名尾部异常")
        tag, rid = reference & 3, reference >> 2
        if tag == 0:
            row = self.row(2, rid)
            name, namespace = self.string(row[1]), self.string(row[2])
        elif tag == 1:
            _, name_offset, namespace_offset = self.row(1, rid)
            name, namespace = self.string(name_offset), self.string(namespace_offset)
        else:
            return None
        return namespace + "." + name if namespace else name

    def type_attributes(self, names: set[str]) -> list[dict]:
        selected, parents = {}, {}
        for rid in range(1, self.counts.get(2, 0) + 1):
            row = self.row(2, rid)
            name = self.string(row[1])
            if name in names:
                selected[rid] = {"name": name, "namespace": self.string(row[2]),
                                 "attrs": [], "properties": {}}
                parents[rid << 5 | 3] = selected[rid]
        for rid in range(1, self.counts.get(21, 0) + 1):
            type_rid, first = self.row(21, rid)
            if type_rid not in selected:
                continue
            end = self.row(21, rid + 1)[1] if rid < self.counts[21] else self.counts[23] + 1
            if not 1 <= first <= end <= self.counts[23] + 1:
                raise FormatError("CLR 属性区间越界")
            for property_rid in range(first, end):
                _, name, signature = self.row(23, property_rid)
                prop = {"name": self.string(name), "attrs": [],
                        "array_element": self.property_array_element(self.blob(signature))}
                selected[type_rid]["properties"][property_rid] = prop
                parents[property_rid << 5 | 9] = prop
        for rid in range(1, self.counts.get(12, 0) + 1):
            parent, constructor, value = self.row(12, rid)
            if parent not in parents:
                continue
            if constructor & 7 != 3:
                raise FormatError("仅支持 MemberRef 属性构造器")
            type_parent, _, signature = self.row(10, constructor >> 3)
            if type_parent & 7 != 1:
                raise FormatError("属性构造器不是 TypeRef")
            _, name, namespace = self.row(1, type_parent >> 3)
            parents[parent]["attrs"].append({
                "name": self.string(name), "namespace": self.string(namespace),
                "blob": self.blob(value).hex(), "constructor_signature": self.blob(signature).hex(),
            })
        return list(selected.values())

    def type_names_by_guids(self, guids: set[str]) -> set[str]:
        """通过类型 GUID 选择 SDK 类，避免依赖资产文件名猜测类名。"""
        selected = set()
        constructors = {}
        for rid in range(1, self.counts.get(12, 0) + 1):
            parent, constructor, value = self.row(12, rid)
            if parent & 31 != 3 or constructor & 7 != 3:
                continue
            if constructor not in constructors:
                reference, _, _ = self.row(10, constructor >> 3)
                if reference & 7 == 1:
                    _, name, _ = self.row(1, reference >> 3)
                    constructors[constructor] = self.string(name)
                else:
                    constructors[constructor] = None
            if constructors[constructor] != "TypeGuidAttribute":
                continue
            raw = self.blob(value)
            if checked_range(raw, 0, 2) != b"\x01\0":
                raise FormatError("无效 SDK 类型属性")
            text, _ = serialized_string(raw, 2)
            if text is not None and str(uuid.UUID(text)) in guids:
                selected.add(self.string(self.row(2, parent >> 5)[1]))
        return selected
