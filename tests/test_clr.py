"""SDK 静态元数据的边界与编码测试；不运行 PE 文件。"""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fc27_clr import Metadata, compressed_integer, pe_metadata, serialized_string
from fc27_dbobject import FormatError


def metadata_fixture():
    tables = struct.pack("<IBBBBQQI", 0, 2, 0, 0, 1, 2, 0, 1) + struct.pack("<3H", 0, 1, 7)
    streams = [(b"#~", tables), (b"#Strings", b"\0Point\0Core\0"), (b"#Blob", b"\0\x03abc")]
    header = b"BSJB" + struct.pack("<HHII", 1, 1, 0, 4) + b"v4\0\0" + struct.pack("<2H", 0, 3)
    offset = len(header) + sum(8 + ((len(name)+4)//4*4) for name, _ in streams)
    descriptions, payload = bytearray(), bytearray()
    for name, data in streams:
        descriptions.extend(struct.pack("<2I", offset + len(payload), len(data)))
        descriptions.extend(name + b"\0" + bytes(-(len(name)+1) % 4))
        payload.extend(data)
        payload.extend(bytes(-len(payload) % 4))
    return header + descriptions + payload


def pe_fixture():
    data = bytearray(1536)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 70, 1)
    struct.pack_into("<H", data, 84, 224)
    struct.pack_into("<H", data, 88, 0x10B)
    struct.pack_into("<I", data, 88+92, 16)
    struct.pack_into("<2I", data, 88+96+14*8, 0x2000, 72)
    struct.pack_into("<4I", data, 88+224+8, 1024, 0x2000, 1024, 512)
    metadata = metadata_fixture()
    struct.pack_into("<2I", data, 512+8, 0x2020, len(metadata))
    data[544:544+len(metadata)] = metadata
    return bytes(data)


class ClrTests(unittest.TestCase):
    def test_all_compressed_integer_widths(self):
        for encoded, value in ((b"\x7f", 127), (b"\x80\x80", 128),
                               (b"\xbf\xff", 16383), (b"\xc0\x00\x40\x00", 16384)):
            self.assertEqual(compressed_integer(encoded, 0), (value, len(encoded)))

    def test_noncanonical_truncated_and_reserved_integer_rejected(self):
        for encoded in (b"\x80\x01", b"\x80", b"\xff", b"\xc0\0\0\x01"):
            with self.assertRaises(FormatError):
                compressed_integer(encoded, 0)

    def test_nullable_and_utf8_serialized_strings(self):
        self.assertEqual(serialized_string(b"\xff", 0), (None, 1))
        self.assertEqual(serialized_string(b"\x03abc", 0), ("abc", 4))
        with self.assertRaises(FormatError):
            serialized_string(b"\x03a", 0)

    def test_static_pe_to_metadata_mapping(self):
        self.assertEqual(pe_metadata(pe_fixture()), metadata_fixture())
        damaged = bytearray(pe_fixture())
        struct.pack_into("<I", damaged, 512+8, 0x9000)
        with self.assertRaises(FormatError):
            pe_metadata(bytes(damaged))

    def test_table_heaps_and_generic_property_element(self):
        metadata = Metadata(metadata_fixture())
        self.assertEqual(metadata.string(1), "Point")
        self.assertEqual(metadata.blob(1), b"abc")
        self.assertEqual(metadata.property_array_element(bytes.fromhex("2800151205011105")), "Core.Point")
        with self.assertRaises(FormatError):
            metadata.row(1, 0)
        with self.assertRaises(FormatError):
            metadata.string(9999)

    def test_primitive_generic_collection_element(self):
        metadata = Metadata(metadata_fixture())
        for encoded, name in (("2800151205010c", "System.Single"),
                              ("28001512050108", "System.Int32"),
                              ("28001512050102", "System.Boolean")):
            self.assertEqual(metadata.property_array_element(bytes.fromhex(encoded)), name)
        with self.assertRaises(FormatError):
            metadata.property_array_element(bytes.fromhex("2800151205010c00"))


if __name__ == "__main__":
    unittest.main()
