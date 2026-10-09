"""RIFF EBX 封装和数值数组检查；保留不透明块，不推断 AI 字段名。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
import uuid

from fc27_assets import checked_range
from fc27_dbobject import FormatError, MAX_FILE_BYTES


@dataclass(frozen=True)
class Chunk:
    name: bytes
    data: bytes
    padding: bytes
    offset: int


@dataclass
class RiffDocument:
    kind: bytes
    chunks: list[Chunk]

    @classmethod
    def parse(cls, data: bytes) -> RiffDocument:
        if len(data) > MAX_FILE_BYTES or checked_range(data, 0, 4) != b"RIFF":
            raise FormatError("不支持的 RIFF 封装")
        if struct.unpack("<I", checked_range(data, 4, 4))[0] + 8 != len(data):
            raise FormatError("RIFF 声明长度不一致")
        kind = checked_range(data, 8, 4)
        if kind not in (b"EBX\0", b"EBXS"):
            raise FormatError("RIFF 不是支持的 EBX")
        position = 12
        chunks = []
        while position < len(data):
            name, size = struct.unpack("<4sI", checked_range(data, position, 8))
            payload = checked_range(data, position + 8, size)
            padding = checked_range(data, position + 8 + size, size & 1)
            chunks.append(Chunk(name, payload, padding, position))
            position += 8 + size + len(padding)
        return cls(kind, chunks)

    def compile(self) -> bytes:
        # 重建外层长度，块内数据及奇数长度的填充字节逐字节保留。
        payload = self.kind + b"".join(
            c.name + struct.pack("<I", len(c.data)) + c.data + c.padding for c in self.chunks)
        result = b"RIFF" + struct.pack("<I", len(payload)) + payload
        RiffDocument.parse(result)
        return result

    def chunk(self, name: bytes) -> Chunk:
        found = [c for c in self.chunks if c.name == name]
        if len(found) != 1:
            raise FormatError(f"EBX 缺少唯一的 {name!r} 块")
        return found[0]

    def inspect(self) -> dict:
        data_chunk = self.chunk(b"EBXD")
        # EBXD 实际载荷按绝对文件位置对齐到 16 字节。
        padding = -(data_chunk.offset + 8) % 16
        payload = checked_range(data_chunk.data, padding, len(data_chunk.data) - padding)
        fixup = self.chunk(b"EFIX").data
        position = 16
        partition = uuid.UUID(bytes_le=checked_range(fixup, 0, 16))

        def u32() -> int:
            nonlocal position
            value = struct.unpack("<I", checked_range(fixup, position, 4))[0]
            position += 4
            return value

        def vector(width: int) -> list[bytes]:
            nonlocal position
            count = u32()
            if count > 1_000_000:
                raise FormatError("EFIX 元素数过多")
            raw = checked_range(fixup, position, width * count)
            position += len(raw)
            return [raw[i*width:(i+1)*width] for i in range(count)]

        guids = vector(16)
        signatures = vector(4)
        exported = u32()
        instances = [int.from_bytes(v, "little") for v in vector(4)]
        pointers, resources, imports, import_offsets, type_offsets = (vector(w) for w in (4, 4, 32, 4, 4))
        arrays_offset, boxed_offset, strings_offset, _ = (u32() for _ in range(4))
        if position != len(fixup) or len(guids) != len(signatures) or exported > len(instances):
            raise FormatError("EFIX 类型表、实例数或长度不一致")
        if len(set(instances)) != len(instances):
            raise FormatError("EFIX 实例位置重复")
        for offset in instances:
            checked_range(payload, offset, 2)
            type_ref = struct.unpack_from("<H", payload, offset)[0]
            if type_ref >= len(guids):
                raise FormatError("实例引用了未知类型")
        for offsets in (pointers, resources, import_offsets, type_offsets):
            for offset in offsets:
                checked_range(payload, int.from_bytes(offset, "little"), 8)
        for offset in (arrays_offset, boxed_offset, strings_offset):
            checked_range(payload, offset, 0)

        extra = self.chunk(b"EBXX").data
        array_count, boxed_count = struct.unpack("<2I", checked_range(extra, 0, 8))
        if array_count + boxed_count > 1_000_000 or len(extra) != 8 + 16*(array_count + boxed_count):
            raise FormatError("EBXX 数组表长度不一致")
        float_arrays = []
        descriptors = []
        for i in range(array_count):
            offset, count, identifier, flags, type_ref = struct.unpack_from("<3I2H", extra, 8 + 16*i)
            descriptors.append({"offset": offset, "count": count, "identifier": f"{identifier:08x}",
                                "flags": flags, "type_ref": type_ref})
            if (flags >> 5) & 31 != 0x13 or (flags >> 1) & 15 != 4:
                continue
            if type_ref != 0xFFFF or count > MAX_FILE_BYTES // 4:
                raise FormatError("Float32 数组描述不支持")
            values = checked_range(payload, offset, count * 4)
            stored_count = struct.unpack("<I", checked_range(payload, offset - 4, 4))[0]
            if stored_count != count:
                raise FormatError("Float32 数组元素数与载荷不符")
            float_arrays.append({
                "array_index": i, "identifier": f"{identifier:08x}", "field_name": None,
                "count": count, "payload_offset": offset,
                "values": list(struct.unpack("<" + str(count) + "f", values)),
            })
        return {
            "partition_guid": str(partition),
            "types": [{"guid": str(uuid.UUID(bytes_le=g)),
                       "signature": f"{int.from_bytes(s, 'little'):08x}"}
                      for g, s in zip(guids, signatures)],
            "instances": len(instances), "arrays": array_count, "boxed_values": boxed_count,
            "instance_offsets": instances,
            "pointer_offsets": [int.from_bytes(p, "little") for p in pointers],
            "payload_file_offset": data_chunk.offset + 8 + padding,
            "payload_size": len(payload), "array_descriptors": descriptors,
            "arrays_offset": arrays_offset, "boxed_offset": boxed_offset,
            "strings_offset": strings_offset,
            "float_arrays": float_arrays, "field_names_resolved": False,
            "chunks": [{"name": c.name.decode("ascii", errors="replace"), "size": len(c.data)}
                       for c in self.chunks],
        }


def verify_ebx(data: bytes) -> dict:
    document = RiffDocument.parse(data)
    encoded = document.compile()
    result = document.inspect()
    result.update(byte_identical=data == encoded, sha256=hashlib.sha256(data).hexdigest())
    return result
