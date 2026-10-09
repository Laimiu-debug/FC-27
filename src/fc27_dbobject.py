"""Frostbite DbObject 离线读写原型；不支持 EBX 或可加载玩法模组。

依据公开格式资料独立实现。保留原文件封装头，不解释其中的签名。
只支持明文 DbObject 载荷；encrypted Blob 作为不透明字节保留。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import struct


HEADER_SIZE = 0x22C
HEADER_MAGIC = b"\x00\xd1\xce\x01"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 500_000
TYPE_NAMES = {
    1: "list", 2: "dict", 6: "bool", 7: "string", 8: "int32",
    9: "int64", 11: "float32", 12: "float64", 15: "guid",
    16: "sha1", 19: "blob",
}
FIXED_WIDTHS = {6: 1, 8: 4, 9: 8, 11: 4, 12: 8, 15: 16, 16: 20}


class FormatError(ValueError):
    """不支持的格式或损坏的输入。"""


def encode_length(value: int) -> bytes:
    if not 0 <= value < (1 << 63):
        raise FormatError("长度超出允许范围")
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


@dataclass
class Node:
    tag: int
    name: bytes | None = None
    data: bytes = b""
    children: list[Node] = field(default_factory=list)

    @property
    def kind(self) -> int:
        return self.tag & 0x7F

    def string_value(self) -> str:
        if self.kind != 7 or not self.data.endswith(b"\0"):
            raise FormatError("节点不是有效字符串")
        return self.data[:-1].decode("utf-8", errors="strict")

    def number_value(self) -> int | float:
        formats = {8: "<i", 9: "<q", 11: "<f", 12: "<d"}
        if self.kind not in formats:
            raise FormatError("节点不是数值")
        return struct.unpack(formats[self.kind], self.data)[0]


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0
        self.nodes = 0

    def take(self, count: int, limit: int) -> bytes:
        end = self.position + count
        if count < 0 or end > limit:
            raise FormatError(f"载荷截断或长度越界，位置 {self.position}")
        value = self.data[self.position:end]
        self.position = end
        return value

    def length(self, limit: int) -> int:
        start = self.position
        result = 0
        for shift in range(0, 63, 7):
            byte = self.take(1, limit)[0]
            result |= (byte & 0x7F) << shift
            if byte < 0x80:
                if self.data[start:self.position] != encode_length(result):
                    raise FormatError("非规范长度编码，拒绝静默改写")
                return result
        raise FormatError("长度编码过长")

    def node(self, limit: int, depth: int = 0) -> Node:
        if depth > MAX_DEPTH:
            raise FormatError("嵌套层级超过限制")
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FormatError("节点数量超过限制")
        tag = self.take(1, limit)[0]
        kind = tag & 0x7F
        if kind not in TYPE_NAMES:
            raise FormatError(f"未知类型 0x{tag:02x}，位置 {self.position - 1}")
        name = None
        if not tag & 0x80:
            end = self.data.find(b"\0", self.position, limit)
            if end == -1:
                raise FormatError("字段名没有结束符")
            name = self.take(end - self.position, limit)
            self.take(1, limit)
        node = Node(tag, name)
        if kind in (1, 2):
            size = self.length(limit)
            end = self.position + size
            if size < 1 or end > limit:
                raise FormatError("容器长度无效")
            while self.position < end and self.data[self.position] != 0:
                node.children.append(self.node(end, depth + 1))
            if self.position != end - 1 or self.take(1, end) != b"\0":
                raise FormatError("容器长度与结束符不一致")
            # 以有序节点保存对象，避免将实际文件中的同名字段折叠进 dict。
        elif kind in (7, 19):
            node.data = self.take(self.length(limit), limit)
            if kind == 7 and not node.data.endswith(b"\0"):
                raise FormatError("字符串没有结束符")
        else:
            node.data = self.take(FIXED_WIDTHS[kind], limit)
            if kind == 6 and node.data not in (b"\0", b"\1"):
                raise FormatError("布尔值编码无效")
        return node


def encode_node(node: Node, depth: int = 0, budget: list[int] | None = None) -> bytes:
    if budget is None:
        budget = [MAX_NODES]
    budget[0] -= 1
    if depth > MAX_DEPTH or budget[0] < 0:
        raise FormatError("节点或嵌套层级超过限制")
    if not 0 <= node.tag <= 255 or node.kind not in TYPE_NAMES:
        raise FormatError("不支持的节点类型")
    prefix = bytes([node.tag])
    if node.tag & 0x80:
        if node.name is not None:
            raise FormatError("匿名节点不能带名称")
    else:
        if node.name is None or b"\0" in node.name:
            raise FormatError("节点名称无效")
        prefix += node.name + b"\0"
    if node.kind in (1, 2):
        if node.data:
            raise FormatError("容器不能带标量载荷")
        body = b"".join(encode_node(child, depth + 1, budget) for child in node.children) + b"\0"
        result = prefix + encode_length(len(body)) + body
    else:
        if node.children:
            raise FormatError("标量不能包含子节点")
        if node.kind in (7, 19):
            if node.kind == 7 and not node.data.endswith(b"\0"):
                raise FormatError("字符串必须以零字节结束")
            result = prefix + encode_length(len(node.data)) + node.data
        else:
            if len(node.data) != FIXED_WIDTHS[node.kind]:
                raise FormatError("标量长度不正确")
            if node.kind == 6 and node.data not in (b"\0", b"\1"):
                raise FormatError("布尔值编码无效")
            result = prefix + node.data
    if len(result) > MAX_FILE_BYTES:
        raise FormatError("编码结果过大")
    return result


@dataclass
class Document:
    header: bytes
    root: Node

    @classmethod
    def parse(cls, data: bytes) -> Document:
        if not HEADER_SIZE < len(data) <= MAX_FILE_BYTES:
            raise FormatError("文件大小不支持")
        if data[:4] != HEADER_MAGIC:
            raise FormatError("只支持 00 D1 CE 01 封装；不尝试解密或猜测格式")
        reader = Reader(data[HEADER_SIZE:])
        root = reader.node(len(reader.data))
        if reader.position != len(reader.data):
            raise FormatError("根节点后存在未解释字节")
        return cls(data[:HEADER_SIZE], root)

    def compile(self) -> bytes:
        if len(self.header) != HEADER_SIZE or self.header[:4] != HEADER_MAGIC:
            raise FormatError("封装头无效")
        result = self.header + encode_node(self.root)
        if len(result) > MAX_FILE_BYTES:
            raise FormatError("编译结果过大")
        # 只验证格式自洽；不验证原封装头中可能存在的签名或引擎接受性。
        Document.parse(result)
        return result

    def summary(self) -> dict:
        counts: Counter[str] = Counter()
        opaque_bytes = 0
        pending = [self.root]
        while pending:
            node = pending.pop()
            counts[TYPE_NAMES[node.kind]] += 1
            if node.kind == 19:
                opaque_bytes += len(node.data)
            pending.extend(node.children)
        return {
            "node_counts": dict(sorted(counts.items())),
            "total_nodes": sum(counts.values()),
            "opaque_blob_bytes": opaque_bytes,
            "root_fields": [child.name.decode("utf-8", errors="replace")
                            for child in self.root.children if child.name is not None],
            "ebx_fields_decoded": False,
            "loadable_gameplay_mod_generated": False,
        }


def verify_roundtrip(data: bytes) -> dict:
    document = Document.parse(data)
    rebuilt = document.compile()
    return {
        **document.summary(),
        "byte_identical": rebuilt == data,
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "rebuilt_sha256": hashlib.sha256(rebuilt).hexdigest(),
        "input_bytes": len(data),
        "rebuilt_bytes": len(rebuilt),
        "verification_scope": "DbObject 格式与未修改内容的字节一致性；未验证游戏加载",
    }
