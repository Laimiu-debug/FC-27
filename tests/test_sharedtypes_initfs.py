"""本机共享类型的布局来源、边界和 initfs 导出范围测试。"""

from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fc27_dbobject import Document, FormatError, HEADER_MAGIC, HEADER_SIZE, Node, encode_node
from fc27_initfs import extract_types
from fc27_schema import named_values
from fc27_sharedtypes import SharedTypes
from test_riff import chunk, riff
from test_schema_candidate import GUIDS, SIGNATURES, attr, definition, fixup, curve_fixture


def shared_fixture(first=0, reference=0xFFFF, name_hash=0x1234):
    import uuid
    identity = uuid.UUID(GUIDS[0]).bytes_le + struct.pack("<I", SIGNATURES[0] + 1)
    body = struct.pack("<I", 1) + identity
    body += struct.pack("<IIi4H", 1, name_hash, first, 2, 0x60, 80, 8)
    body += struct.pack("<I", 2)
    body += struct.pack("<II2H", 1, 64, 19 << 5, reference)
    body += struct.pack("<II2H", 2, 68, 19 << 5, 0xFFFF)
    body += bytes(12)
    payload = b"EBXT" + chunk(b"RFL2", body)
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


class SharedTypeTests(unittest.TestCase):
    def test_duplicate_identity_across_chunks_is_rejected(self):
        block = shared_fixture()[12:]
        payload = b"EBXT" + block + block
        with self.assertRaises(FormatError):
            SharedTypes(b"RIFF" + struct.pack("<I", len(payload)) + payload)

    def test_reference_field_range_and_truncation_checked(self):
        for data in (shared_fixture(first=1), shared_fixture(reference=1), shared_fixture()[:-1]):
            with self.assertRaises(FormatError):
                SharedTypes(data)

    def test_fc27_offsets_and_signature_are_read_from_shared_table(self):
        sdk = definition("Root", GUIDS[0], SIGNATURES[0], 72,
                         [("Value", 40, 19 << 5, None, None)])
        sdk["attrs"].append(attr("NameHashAttribute", struct.pack("<I", 0x1234)))
        next(iter(sdk["properties"].values()))["attrs"].append(attr("NameHashAttribute", struct.pack("<I", 1)))
        with patch("fc27_sharedtypes.pe_metadata", return_value=b"metadata"), patch("fc27_sharedtypes.Metadata") as factory:
            factory.return_value.type_names_by_guids.return_value = {"Root"}
            factory.return_value.type_attributes.return_value = [sdk]
            schemas = SharedTypes(shared_fixture()).adapt(b"static-sdk", {GUIDS[0]})
        payload = bytearray(160)
        struct.pack_into("<f", payload, 56, 999.0)  # 旧 SDK 的位置应被忽略。
        struct.pack_into("<2f", payload, 80, 1.25, 2.5)
        data = riff(chunk(b"EBXD", bytes(12) + payload),
                    chunk(b"EFIX", fixup(GUIDS[:1], [SIGNATURES[0] + 1], [16], [], 160)),
                    chunk(b"EBXX", bytes(8)))
        values = named_values(data, schemas)
        self.assertEqual(values["leaves"]["Value"]["value"], 1.25)
        self.assertEqual(values["leaves"]["Field_00000002"]["value"], 2.5)
        self.assertEqual(values["unresolved_names"], ["Field_00000002"])
        self.assertEqual(values["sdk_version"], "FC27")
        self.assertNotIn((GUIDS[0], f"{SIGNATURES[0]:08x}"), schemas.types)
        with patch("fc27_sharedtypes.pe_metadata", return_value=b"metadata"), patch("fc27_sharedtypes.Metadata") as factory:
            factory.return_value.type_names_by_guids.return_value = {"Root"}
            factory.return_value.type_attributes.return_value = [sdk]
            with self.assertRaises(FormatError):
                SharedTypes(shared_fixture(name_hash=999)).adapt(b"static-sdk", {GUIDS[0]})

    def test_reference_metadata_anomaly_is_not_enabled_by_default(self):
        data, schemas = curve_fixture()
        modified = bytearray(data)
        struct.pack_into("<I2H", modified, len(modified) - 8, 0x19036493, 0x4020, 2)
        with self.assertRaises(FormatError):
            named_values(bytes(modified), schemas)
        schemas.allow_legacy_curve_metadata = True
        values = named_values(bytes(modified), schemas)
        self.assertEqual(values["leaves"]["Curve.Points[0].Y"]["value"], 2.5)
        self.assertEqual(len(values["metadata_warnings"]), 1)


def initfs_fixture(duplicate=False):
    def file(name, payload):
        return Node(0x82, children=[Node(2, b"$file", children=[
            Node(7, b"name", (name + "\0").encode()), Node(19, b"payload", payload)])])
    files = [file("SharedTypeDescriptors.ebx", shared_fixture()), file("internal.key", b"private-data")]
    if duplicate:
        files.append(file("SharedTypeDescriptors.ebx", shared_fixture()))
    return Node(0x81, children=files)


class InitfsTests(unittest.TestCase):
    def test_export_is_limited_to_type_files_and_inner_roundtrip(self):
        plain = encode_node(initfs_fixture())
        header = HEADER_MAGIC + bytes(HEADER_SIZE - 4)
        outer = Document(header, Node(0x82, children=[Node(19, b"encrypted", bytes(16))])).compile()
        exports, report = extract_types(outer, lambda _: plain)
        self.assertEqual(list(exports), ["SharedTypeDescriptors.ebx"])
        self.assertEqual(report["total_embedded_files"], 2)
        self.assertTrue(report["inner_roundtrip_verified"])
        self.assertNotIn(b"private-data", exports["SharedTypeDescriptors.ebx"])
        with self.assertRaises(FormatError):
            extract_types(outer)
        with self.assertRaises(FormatError):
            extract_types(outer, lambda _: plain + b"extra")

    def test_duplicate_export_name_rejected(self):
        data = Document(HEADER_MAGIC + bytes(HEADER_SIZE - 4), initfs_fixture(True)).compile()
        with self.assertRaises(FormatError):
            extract_types(data)


if __name__ == "__main__":
    unittest.main()
