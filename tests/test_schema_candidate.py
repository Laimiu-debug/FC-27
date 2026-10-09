"""类型身份、SDK 偏移、数组指针和修改范围的合成测试。"""

import hashlib
from pathlib import Path
import struct
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fc27_assets import decompress_cas
from fc27_candidate import encode_uncompressed_blocks, transfer_arrays
from fc27_dbobject import FormatError, encode_length
from fc27_modsample import SampleProfile, read_sample
from fc27_schema import Schemas, named_values
from test_riff import chunk, riff


GUIDS = [str(uuid.UUID(int=i)) for i in range(3)]
SIGNATURES = [0x636051D6, 0x7CEE14C5, 0x8CB2702C]


def attr(name, data):
    return {"name": name, "blob": (b"\x01\0" + data + b"\0\0").hex()}


def definition(name, guid, signature, size, properties, namespace="", alignment=8, flags=0x63):
    attrs = [attr("TypeGuidAttribute", bytes([36]) + guid.encode()),
             attr("TypeSignatureAttribute", struct.pack("<I", signature)),
             attr("TypeInfoAttribute", struct.pack("<HBH", flags, alignment, size))]
    props = {}
    for i, (field, offset, field_flags, reference, element) in enumerate(properties):
        text = b"\xFF" if reference is None else bytes([len(reference)]) + reference.encode()
        props[i] = {"name": field, "array_element": element,
                    "attrs": [attr("FieldTypeInfoAttribute", struct.pack("<HI", field_flags, offset) + text)]}
    return {"name": name, "namespace": namespace, "attrs": attrs, "properties": props}


def fixup(guids, signatures, instances, pointers, payload_size, arrays_offset=0):
    data = bytes(16) + struct.pack("<I", len(guids))
    data += b"".join(uuid.UUID(g).bytes_le for g in guids)
    data += struct.pack("<I", len(signatures)) + b"".join(struct.pack("<I", s) for s in signatures)
    data += struct.pack("<2I", len(instances), len(instances))
    data += b"".join(struct.pack("<I", i) for i in instances)
    data += struct.pack("<I", len(pointers)) + b"".join(struct.pack("<I", i) for i in pointers)
    data += bytes(16) + struct.pack("<4I", arrays_offset, payload_size, payload_size, 0)
    return data


def array_fixture(values=(0.3, 0.5), aliased=False):
    payload = bytearray(160)
    struct.pack_into("<i", payload, 56, 24)
    pointers = [56]
    if aliased:
        struct.pack_into("<i", payload, 64, 16)
        pointers.append(64)
    struct.pack_into("<I2f", payload, 76, 2, *values)
    extra = struct.pack("<2I3I2H", 1, 0, 80, 2, 0x1234, 0x268, 0xFFFF)
    data = riff(chunk(b"EBXD", bytes(12) + payload),
                chunk(b"EFIX", fixup(GUIDS[:1], SIGNATURES[:1], [16], pointers, len(payload))),
                chunk(b"EBXX", extra))
    properties = [("Values", 40, 0x89, None, "System.Single")]
    if aliased:
        properties.append(("Alias", 48, 0x89, None, "System.Single"))
    schema = Schemas([definition("TestRoot", GUIDS[0], SIGNATURES[0], 64, properties)], "test-sdk")
    return data, schema


def curve_fixture():
    payload = bytearray(240)
    struct.pack_into("<q", payload, 56, 40)
    struct.pack_into("<H", payload, 96, 1)
    struct.pack_into("<i", payload, 120, 44)
    struct.pack_into("<2f", payload, 128, 1.0, 0.0)
    struct.pack_into("<Iif2ff2f", payload, 160, 1, 0, 0.5, 0.0, 0.0, 2.5, 0.0, 0.0)
    # 前代样本确实存在超出 EFIX 类型数的 EBXX type_ref；不要把它猜作 EFIX 索引。
    extra = struct.pack("<2I3I2H", 1, 0, 164, 1, 0xABCD, 0x48, 4)
    data = riff(chunk(b"EBXD", bytes(12) + payload),
                chunk(b"EFIX", fixup(GUIDS, SIGNATURES, [16, 96], [56, 120], len(payload))),
                chunk(b"EBXX", extra))
    definitions = [
        definition("Root", GUIDS[0], SIGNATURES[0], 64,
                   [("Curve", 40, 0x63, "Frostbite.Core.FloatCurve", None)]),
        definition("FloatCurve", GUIDS[1], SIGNATURES[1], 40,
                   [("Points", 24, 0x89, None, "Frostbite.Core.FloatCurvePoint"),
                    ("MaxX", 32, 0x8267, None, None), ("MinX", 36, 0x8267, None, None)], "Frostbite.Core"),
        definition("FloatCurvePoint", GUIDS[2], SIGNATURES[2], 28,
                   [("CurveType", 0, 0x810B, None, None), ("X", 4, 0x8267, None, None),
                    ("Y", 16, 0x8267, None, None)], "Frostbite.Core", 4, 0x45),
    ]
    return data, Schemas(definitions, "test-sdk")


class SchemaTests(unittest.TestCase):
    def test_shared_empty_array_requires_efix_reserved_zero_region(self):
        payload = bytearray(160)
        struct.pack_into("<i", payload, 56, 40)
        data = riff(chunk(b"EBXD", bytes(12) + payload),
                    chunk(b"EFIX", fixup(GUIDS[:1], SIGNATURES[:1], [16], [56], 160, 80)),
                    chunk(b"EBXX", bytes(8)))
        _, schema = array_fixture()
        self.assertEqual(named_values(data, schema)["shapes"]["Values"]["count"], 0)
        for offset, value in ((56, 44), (92, 1), (60, 1)):
            damaged = bytearray(data)
            struct.pack_into("<I", damaged, 32 + offset, value)
            with self.assertRaises(FormatError):
                named_values(bytes(damaged), schema)

    def test_primitive_array_clr_type_cannot_disagree_with_ebxx(self):
        data, schema = array_fixture()
        next(iter(schema.types.values()))["properties"][0]["array_element"] = "System.Int32"
        with self.assertRaises(FormatError):
            named_values(data, schema)

    def test_signed_numeric_array_retains_integer_values(self):
        data, schema = array_fixture()
        damaged = bytearray(data)
        struct.pack_into("<2i", damaged, 112, -23, 256)
        struct.pack_into("<H", damaged, len(damaged) - 4, (15 << 5) | (4 << 1))
        next(iter(schema.types.values()))["properties"][0]["array_element"] = "System.Int32"
        values = named_values(bytes(damaged), schema)
        self.assertEqual(values["leaves"]["Values[0]"]["value"], -23)
        self.assertEqual(values["shapes"]["Values"]["kind"], "numeric_array")

    def test_named_array_resolved_by_sdk_offset_and_ebxx_pointer(self):
        data, schema = array_fixture()
        mapped = named_values(data, schema)
        self.assertAlmostEqual(mapped["leaves"]["Values[0]"]["value"], 0.3)
        self.assertEqual(mapped["leaves"]["Values[0]"]["offset"], 112)

    def test_guid_signature_mismatch_rejected(self):
        data, schema = array_fixture()
        schema.types = {(GUIDS[0], "00000000"): next(iter(schema.types.values()))}
        with self.assertRaises(FormatError):
            named_values(data, schema)

    def test_wrong_field_offset_cannot_guess_an_array(self):
        data, schema = array_fixture()
        next(iter(schema.types.values()))["properties"][0]["offset"] = 32
        with self.assertRaises(FormatError):
            named_values(data, schema)

    def test_curve_element_type_uses_clr_and_exact_identity(self):
        data, schema = curve_fixture()
        mapped = named_values(data, schema)
        self.assertEqual(mapped["leaves"]["Curve.Points[0].X"]["value"], 0.5)
        self.assertEqual(mapped["leaves"]["Curve.Points[0].Y"]["value"], 2.5)

    def test_curve_cannot_fall_back_to_an_unmatched_point_type(self):
        data, schema = curve_fixture()
        del schema.types[GUIDS[2], f"{SIGNATURES[2]:08x}"]
        with self.assertRaises(FormatError):
            named_values(data, schema)


class CandidateTests(unittest.TestCase):
    def test_transfer_changes_only_selected_values_and_roundtrips_blocks(self):
        current, schema = array_fixture()
        reference, _ = array_fixture((0.8, 1.4))
        result, report = transfer_arrays(current, reference, schema, ["Values"], hashlib.sha256(current).hexdigest())
        self.assertEqual(report["changed_values"], 2)
        changed = {i for i, (a, b) in enumerate(zip(current, result)) if a != b}
        self.assertTrue(changed <= set(range(112, 120)))
        self.assertEqual(decompress_cas(encode_uncompressed_blocks(result), len(result)), result)
        self.assertFalse(report["loadable_mod"])

    def test_changed_baseline_duplicate_and_unknown_fields_rejected(self):
        current, schema = array_fixture()
        for fields, sha in ((["Values"], "bad"), (["Values", "Values"], hashlib.sha256(current).hexdigest()),
                            (["Missing"], hashlib.sha256(current).hexdigest())):
            with self.assertRaises(FormatError):
                transfer_arrays(current, current, schema, fields, sha)

    def test_aliasing_cannot_overwrite_same_bytes_twice(self):
        current, schema = array_fixture(aliased=True)
        reference, _ = array_fixture((0.8, 1.4), aliased=True)
        with self.assertRaises(FormatError):
            transfer_arrays(current, reference, schema, ["Values", "Alias"], hashlib.sha256(current).hexdigest())

    def test_nonfinite_candidate_values_rejected(self):
        current, schema = array_fixture()
        reference, _ = array_fixture((float("nan"), 1.4))
        with self.assertRaises(FormatError):
            transfer_arrays(current, reference, schema, ["Values"], hashlib.sha256(current).hexdigest())

    def test_multiple_uncompressed_blocks(self):
        data = bytes(range(256))*512
        packed = encode_uncompressed_blocks(data)
        self.assertEqual(len(packed), len(data) + 16)
        self.assertEqual(decompress_cas(packed, len(data)), data)


class SampleTests(unittest.TestCase):
    def make_sample(self):
        payload = encode_uncompressed_blocks(b"abc")
        name = b"fifa/attribulator/gameplay/example"
        metadata = b"FETM\x01\x04FC26" + encode_length(len(name)) + name + b"\0"
        metadata += hashlib.sha1(payload).digest() + encode_length(0) + encode_length(len(payload)) + encode_length(3)
        data = metadata + payload
        return data, SampleProfile("synthetic", len(metadata), 1)

    def test_known_sample_profile_still_checks_each_payload_sha1(self):
        data, profile = self.make_sample()
        with patch("fc27_modsample.KNOWN_SAMPLES", {hashlib.sha256(data).hexdigest(): profile}):
            _, records = read_sample(data)
            self.assertEqual(records[0].decode(), b"abc")
        damaged = data[:-1] + b"d"
        with patch("fc27_modsample.KNOWN_SAMPLES", {hashlib.sha256(damaged).hexdigest(): profile}), self.assertRaises(FormatError):
            read_sample(damaged)

    def test_unknown_package_and_wrong_expected_record_count_rejected(self):
        data, profile = self.make_sample()
        with self.assertRaises(FormatError):
            read_sample(data)
        wrong = SampleProfile("synthetic", profile.payload_start, 2)
        with patch("fc27_modsample.KNOWN_SAMPLES", {hashlib.sha256(data).hexdigest(): wrong}), self.assertRaises(FormatError):
            read_sample(data)


if __name__ == "__main__":
    unittest.main()
