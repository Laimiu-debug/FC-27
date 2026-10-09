"""RIFF EBX 无修改读写与数组范围检查，不使用游戏资产。"""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fc27_dbobject import FormatError
from fc27_riff import RiffDocument, verify_ebx


def chunk(name: bytes, data: bytes, padding=b"\0") -> bytes:
    return name + struct.pack("<I", len(data)) + data + (padding if len(data) & 1 else b"")


def riff(*chunks) -> bytes:
    payload = b"EBX\0" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def fixture(count=2) -> bytes:
    payload = bytearray(100)
    struct.pack_into("<I2f", payload, 60, 2, 0.3, 0.5)
    fixup = bytes(16) + struct.pack("<I", 1) + bytes(16)
    fixup += struct.pack("<6I", 1, 0x636051D6, 1, 1, 16, 0)
    fixup += struct.pack("<8I", 0, 0, 0, 0, 60, 100, 100, 0)
    extra = struct.pack("<2I3I2H", 1, 0, 64, count, 0x12345678, 0x268, 0xFFFF)
    return riff(chunk(b"EBXD", bytes(12) + payload), chunk(b"EFIX", fixup), chunk(b"EBXX", extra))


class RiffTests(unittest.TestCase):
    def test_unknown_chunks_and_nonzero_padding_preserved(self):
        data = riff(chunk(b"TEST", b"abc", b"\xA5"))
        self.assertEqual(RiffDocument.parse(data).compile(), data)

    def test_realistic_float_array_is_read_without_invented_name(self):
        result = verify_ebx(fixture())
        self.assertTrue(result["byte_identical"])
        self.assertEqual(result["instances"], 1)
        self.assertEqual(result["types"][0]["signature"], "636051d6")
        array = result["float_arrays"][0]
        self.assertIsNone(array["field_name"])
        self.assertAlmostEqual(array["values"][0], 0.3)
        self.assertEqual(array["values"][1], 0.5)

    def test_array_count_and_payload_bounds_checked(self):
        for count in (3, 100):
            with self.assertRaises(FormatError):
                verify_ebx(fixture(count))

    def test_truncated_riff_and_wrong_declared_length_rejected(self):
        data = fixture()
        bad = bytearray(data)
        struct.pack_into("<I", bad, 4, len(data))
        for malformed in (data[:-1], bytes(bad), b"RIFF"):
            with self.assertRaises(FormatError):
                RiffDocument.parse(malformed)


if __name__ == "__main__":
    unittest.main()
