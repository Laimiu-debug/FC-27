"""提取一个已校验的公开 Anth 样本载荷，不声称支持通用 FETM 容器。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from fc27_assets import checked_range, decompress_cas, safe_relative
from fc27_dbobject import FormatError, MAX_FILE_BYTES, encode_length


@dataclass(frozen=True)
class SampleProfile:
    title: str
    payload_start: int
    attrib_records: int


KNOWN_SAMPLES = {
    "2ee9507f42c272beaedf1bfa5a4fb0ef152269890cebf160742e58d494dfda37":
        SampleProfile("Anth FC26 FREE V5 REGULAR", 57026, 462),
}


def varint(data: bytes, position: int, limit: int) -> tuple[int, int]:
    start, value = position, 0
    for shift in range(0, 63, 7):
        if position >= limit:
            raise FormatError("FETM 样本整数截断")
        byte = data[position]
        position += 1
        value |= (byte & 127) << shift
        if byte < 128:
            if data[start:position] != encode_length(value):
                raise FormatError("FETM 样本整数不是最短编码")
            return value, position
    raise FormatError("FETM 样本整数过长")


@dataclass(frozen=True)
class SampleAsset:
    name: str
    sha1: str
    packed: bytes
    original_size: int
    metadata_offset: int

    def decode(self, oodle=None) -> bytes:
        return decompress_cas(self.packed, self.original_size, oodle)


def read_sample(data: bytes) -> tuple[SampleProfile, list[SampleAsset]]:
    checksum = hashlib.sha256(data).hexdigest()
    if len(data) > MAX_FILE_BYTES or checksum not in KNOWN_SAMPLES:
        raise FormatError("当前只支持已校验的 Anth FREE V5 REGULAR 样本 SHA256")
    if checked_range(data, 0, 10) != b"FETM\x01\x04FC26":
        raise FormatError("FETM 样本版本或游戏标识不符")
    profile = KNOWN_SAMPLES[checksum]
    checked_range(data, profile.payload_start, 1)
    metadata = data[:profile.payload_start]
    position, found = 0, {}
    prefix = b"fifa/attribulator/"
    while True:
        position = metadata.find(prefix, position)
        if position < 0:
            break
        start = position
        position += len(prefix)
        end = metadata.find(b"\0", start, min(len(metadata), start + 4097))
        if end < 0:
            raise FormatError("样本资源名未结束")
        length_start = None
        for width in range(1, 5):
            if start < width:
                continue
            try:
                length, stop = varint(metadata, start - width, start)
            except FormatError:
                continue
            if stop == start and length == end - start:
                length_start = start - width
                break
        if length_start is None:
            continue
        try:
            name = metadata[start:end].decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise FormatError("样本资源名不是 UTF-8") from exc
        safe_relative(name)
        sha1 = checked_range(metadata, end + 1, 20).hex()
        cursor = end + 21
        offset, cursor = varint(metadata, cursor, len(metadata))
        size, cursor = varint(metadata, cursor, len(metadata))
        original_size, cursor = varint(metadata, cursor, len(metadata))
        if not 0 < size <= MAX_FILE_BYTES or not 0 < original_size <= MAX_FILE_BYTES:
            raise FormatError("样本资源大小超出上限")
        packed = checked_range(data, profile.payload_start + offset, size)
        if hashlib.sha1(packed).hexdigest() != sha1:
            raise FormatError("样本载荷 SHA1 与资源记录不一致")
        if name in found:
            raise FormatError("样本资源名重复")
        found[name] = SampleAsset(name, sha1, packed, original_size, length_start)
    if len(found) != profile.attrib_records:
        raise FormatError("样本解析的资源数量与已校验配置不一致")
    return profile, list(found.values())
