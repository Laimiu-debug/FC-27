"""合成输入测试；不包含游戏资产，不启动游戏。"""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fc27_dbobject import Document, FormatError, HEADER_MAGIC, HEADER_SIZE, Node, verify_roundtrip


HEADER = HEADER_MAGIC + bytes(HEADER_SIZE - len(HEADER_MAGIC))


class DbObjectTests(unittest.TestCase):
    def test_known_int_field_binary(self):
        # 匿名字典，8 字节载荷：具名 int32 + 结束符。
        data = HEADER + b"\x82\x08\x08x\0\x2a\0\0\0\0"
        doc = Document.parse(data)
        self.assertEqual(doc.root.children[0].number_value(), 42)
        self.assertEqual(doc.compile(), data)

    def test_edit_changes_expected_bytes_only(self):
        data = HEADER + b"\x82\x08\x08x\0\x2a\0\0\0\0"
        doc = Document.parse(data)
        doc.root.children[0].data = struct.pack("<i", 43)
        result = doc.compile()
        self.assertEqual(result, HEADER + b"\x82\x08\x08x\0\x2b\0\0\0\0")

    def test_string_growth_recalculates_container_length(self):
        data = HEADER + b"\x82\x07\x07s\0\x02a\0\0"
        doc = Document.parse(data)
        doc.root.children[0].data = b"abcdef\0"
        result = doc.compile()
        self.assertEqual(result, HEADER + b"\x82\x0c\x07s\0\x07abcdef\0\0")
        self.assertEqual(Document.parse(result).root.children[0].string_value(), "abcdef")

    def test_float_bits_and_opaque_blob_preserved(self):
        nan_payload = bytes.fromhex("0100c07f")
        doc = Document(HEADER, Node(0x82, children=[
            Node(11, b"float", nan_payload),
            Node(19, b"encrypted", bytes(range(256))),
        ]))
        result = verify_roundtrip(doc.compile())
        self.assertTrue(result["byte_identical"])
        self.assertEqual(result["opaque_blob_bytes"], 256)

    def test_rejects_truncation_and_trailing_bytes(self):
        valid = HEADER + b"\x82\x01\0"
        for value in (valid[:-1], valid + b"\0", HEADER + b"\x82\x05\0"):
            with self.subTest(value=value[-5:]), self.assertRaises(FormatError):
                Document.parse(value)

    def test_rejects_early_terminator(self):
        with self.assertRaises(FormatError):
            Document.parse(HEADER + b"\x82\x02\0\0")

    def test_rejects_unknown_type_and_noncanonical_length(self):
        for body in (b"\x85", b"\x82\x81\0\0", b"\x82" + b"\x80" * 10):
            with self.subTest(body=body), self.assertRaises(FormatError):
                Document.parse(HEADER + body)

    def test_preserves_repeated_object_fields(self):
        data = HEADER + b"\x82\x0b\x07s\0\x01\0\x07s\0\x01\0\0"
        doc = Document.parse(data)
        self.assertEqual(len(doc.root.children), 2)
        self.assertEqual(doc.compile(), data)

    def test_rejects_unsupported_header(self):
        with self.assertRaises(FormatError):
            Document.parse(b"\0\xd1\xce\x03" + bytes(HEADER_SIZE - 4) + b"\x82\x01\0")

    def test_rejects_deep_nesting(self):
        root = Node(0x81)
        for _ in range(70):
            root = Node(0x81, children=[root])
        with self.assertRaises(FormatError):
            Document(HEADER, root).compile()


if __name__ == "__main__":
    unittest.main()
