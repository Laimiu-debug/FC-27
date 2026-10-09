"""公开 Frosty v6 容器的受限 EBX 读写；不证明 FC27 加载器兼容性。

独立实现自 FrostyToolsuite-v2 的 FrostyMod/BaseModResource 格式。
仅支持已存在、无 handler、无增删 Bundle 的数值候选，不处理 FETM。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct

from fc27_assets import checked_range, decompress_cas, safe_relative
from fc27_dbobject import FormatError, MAX_FILE_BYTES

MAGIC = 0x01005954534F5246
VERSION = 6
META_KEYS = ("title", "author", "category", "version", "description", "mod_page_link")
MAX_RESOURCES = 64
MAX_STRING = 16384


def cstring(value: str) -> bytes:
    if not isinstance(value, str) or "\0" in value:
        raise FormatError("模组字符串类型错误或包含零字节")
    raw = value.encode("utf-8")
    if len(raw) > MAX_STRING:
        raise FormatError("模组字符串过长")
    return raw + b"\0"


@dataclass(frozen=True)
class EbxResource:
    name: str
    original_size: int
    payload: bytes


@dataclass(frozen=True)
class ModContainer:
    profile: str
    head: int
    metadata: dict[str, str]
    resources: tuple[EbxResource, ...]
    resource_sha1: str


def resource_name(name: str):
    if not isinstance(name, str) or not name.startswith("fifa/attribulator/") or "\0" in name:
        raise FormatError("只允许现有玩法资产名")
    if safe_relative(name).as_posix() != name:
        raise FormatError("资产名不是规范相对路径")
    cstring(name)


def write_mod(profile: str, head: int, metadata: dict, resources: list[EbxResource]) -> bytes:
    if not profile or type(head) is not int or not 0 <= head <= 0xFFFFFFFF:
        raise FormatError("模组 profile 或 head 无效")
    if set(metadata) != set(META_KEYS) or not 0 < len(resources) <= MAX_RESOURCES:
        raise FormatError("模组元数据不完整或资产数量超限")
    strings = cstring(profile) + struct.pack("<I", head)
    strings += b"".join(cstring(metadata[key]) for key in META_KEYS)
    section = bytearray(struct.pack("<i", len(resources)))
    table, payloads, names = bytearray(), bytearray(), set()
    for index, resource in enumerate(resources):
        resource_name(resource.name)
        if resource.name.casefold() in names:
            raise FormatError("模组资产名重复")
        names.add(resource.name.casefold())
        decompress_cas(resource.payload, resource.original_size)
        # Type=Ebx(1), index, name, flags=0, SHA1, size, handler=0,
        # empty userData, addCount=0, removeCount=0。
        section += struct.pack("<Bi", 1, index) + cstring(resource.name) + b"\0"
        section += hashlib.sha1(resource.payload).digest()
        section += struct.pack("<qi", resource.original_size, 0) + b"\0" + bytes(8)
        table += struct.pack("<qi", len(payloads), len(resource.payload))
        payloads += resource.payload
        if len(payloads) > MAX_FILE_BYTES:
            raise FormatError("模组总载荷超过 32 MiB")
    header_size = 24 + len(strings) + 20
    raw = (struct.pack("<QIqi", MAGIC, VERSION, header_size + len(section), len(resources))
           + strings + hashlib.sha1(section).digest() + section + table + payloads)
    if len(raw) > MAX_FILE_BYTES:
        raise FormatError("模组文件超过 32 MiB")
    decoded = read_mod(raw)
    if decoded.resources != tuple(resources) or decoded.profile != profile or decoded.head != head or decoded.metadata != metadata:
        raise FormatError("模组重新读取结果不同")
    return raw


class Reader:
    def __init__(self, data: bytes):
        self.data, self.position = data, 0

    def take(self, size):
        result = checked_range(self.data, self.position, size)
        self.position += size
        return result

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))

    def string(self):
        end = self.data.find(b"\0", self.position, min(len(self.data), self.position + MAX_STRING + 1))
        if end < 0:
            raise FormatError("模组字符串没有结束符或过长")
        try:
            value = self.take(end - self.position).decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise FormatError("模组字符串不是 UTF-8") from exc
        self.take(1)
        return value


def read_mod(data: bytes) -> ModContainer:
    if not 24 <= len(data) <= MAX_FILE_BYTES:
        raise FormatError("模组大小无效")
    reader = Reader(data)
    magic, version, data_offset, data_count = reader.unpack("<QIqi")
    if (magic, version) != (MAGIC, VERSION):
        raise FormatError("仅支持 Frosty v6 容器")
    if not 0 < data_count <= MAX_RESOURCES or not 24 <= data_offset <= len(data):
        raise FormatError("模组数据表数量或位置无效")
    profile = reader.string()
    if not profile:
        raise FormatError("模组缺少 profile")
    head, = reader.unpack("<I")
    metadata = {key: reader.string() for key in META_KEYS}
    section_sha = reader.take(20)
    section_start = reader.position
    count, = reader.unpack("<i")
    if count != data_count:
        raise FormatError("受限容器要求每个 EBX 对应唯一载荷")
    headers, names = [], set()
    for expected_index in range(count):
        kind, index = reader.unpack("<Bi")
        name = reader.string()
        resource_name(name)
        flags, = reader.unpack("<B")
        if kind != 1 or index != expected_index or flags != 0 or name.casefold() in names:
            raise FormatError("不支持的资源类型、索引、标记或重复名称")
        names.add(name.casefold())
        sha1 = reader.take(20)
        original_size, handler = reader.unpack("<qi")
        user = reader.string()
        added, removed = reader.unpack("<ii")
        if handler or user or added or removed or not 0 < original_size <= MAX_FILE_BYTES:
            raise FormatError("不支持 handler、用户数据、Bundle 增删或过大载荷")
        headers.append((name, original_size, sha1))
    if reader.position != data_offset or hashlib.sha1(data[section_start:data_offset]).digest() != section_sha:
        raise FormatError("资源段长度或 SHA1 不符")
    table = [reader.unpack("<qi") for _ in range(data_count)]
    base = reader.position
    position, resources = 0, []
    for (name, size, sha1), (offset, length) in zip(headers, table):
        if offset != position or length <= 0:
            raise FormatError("载荷范围重叠、有空隙或长度无效")
        payload = checked_range(data, base + offset, length)
        if hashlib.sha1(payload).digest() != sha1:
            raise FormatError("载荷 SHA1 不符")
        decompress_cas(payload, size)
        resources.append(EbxResource(name, size, payload))
        position += length
    if base + position != len(data):
        raise FormatError("模组末尾存在未解释数据")
    return ModContainer(profile, head, metadata, tuple(resources), section_sha.hex())
