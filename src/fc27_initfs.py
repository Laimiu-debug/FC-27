"""只读解封 initfs，默认仅导出共享类型表，不导出其凭据或其他内嵌文件。"""

from __future__ import annotations

from collections.abc import Callable
import hashlib

from fc27_dbobject import Document, FormatError, MAX_FILE_BYTES, Node, Reader, encode_node


TYPE_FILES = {"SharedTypeDescriptors.ebx", "SharedTypeDescriptors_patch.ebx"}


def unique(node: Node, name: bytes, kind: int) -> Node:
    found = [c for c in node.children if c.name == name]
    if len(found) != 1 or found[0].kind != kind:
        raise FormatError("initfs 缺少唯一且类型正确的必要字段")
    return found[0]


def extract_types(data: bytes, decryptor: Callable[[bytes], bytes] | None = None) -> tuple[dict[str, bytes], dict]:
    document = Document.parse(data)
    if document.compile() != data:
        raise FormatError("initfs 外层封装读写不一致")
    node = document.root
    encrypted = node.kind == 2
    if encrypted:
        cipher = unique(node, b"encrypted", 19).data
        if decryptor is None or not cipher or len(cipher) % 16:
            raise FormatError("initfs 需要有效解码器与完整 AES 块")
        plain = decryptor(cipher)
        if not 0 < len(plain) <= MAX_FILE_BYTES:
            raise FormatError("initfs 解码结果大小不支持")
        reader = Reader(plain)
        node = reader.node(len(plain))
        if reader.position != len(plain) or encode_node(node) != plain:
            raise FormatError("initfs 内层结构或封装读写不一致")
    if node.kind != 1:
        raise FormatError("initfs 内层不是文件列表")
    exports = {}
    total_files = 0
    for stub in node.children:
        if stub.kind != 2:
            raise FormatError("initfs 文件项不是对象")
        file = unique(stub, b"$file", 2)
        name = unique(file, b"name", 7).string_value()
        payload = unique(file, b"payload", 19).data
        total_files += 1
        if name not in TYPE_FILES:
            continue
        if name in exports:
            raise FormatError("initfs 共享类型文件重复")
        exports[name] = payload
    return exports, {"source_sha256": hashlib.sha256(data).hexdigest(), "encrypted": encrypted,
                     "total_embedded_files": total_files, "exported_types": len(exports),
                     "outer_roundtrip_verified": True, "inner_roundtrip_verified": True,
                     "game_files_written": False}
