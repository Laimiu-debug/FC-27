"""定点编辑、反向恢复与批量构建的输入边界测试。"""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fc27_dbobject import FormatError
from fc27_edit import edit_values, revert_values
from fc27_riff import RiffDocument
from fc27_schema import Schemas, named_values
from test_riff import chunk, riff
from test_schema_candidate import GUIDS, SIGNATURES, array_fixture, curve_fixture, definition, fixup

SPEC = importlib.util.spec_from_file_location("build_cli", ROOT / "scripts" / "fc27_build.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)
VERIFY_SPEC = importlib.util.spec_from_file_location("verify_build_cli", ROOT / "scripts" / "fc27_verify_build.py")
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
with patch.dict(sys.modules, {"fc27_build": BUILD}):
    VERIFY_SPEC.loader.exec_module(VERIFY)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def edit(data, schema, field, value):
    return {"field": field, "expected_hex": named_values(data, schema)["leaves"][field]["raw_hex"], "value": value}


def scalar_fixture(kind=15, fmt="<i", value=12):
    payload = bytearray(96)
    struct.pack_into(fmt, payload, 56, value)
    data = riff(chunk(b"EBXD", bytes(12) + payload),
                chunk(b"EFIX", fixup(GUIDS[:1], SIGNATURES[:1], [16], [], len(payload))),
                chunk(b"EBXX", bytes(8)))
    schema = Schemas([definition("Root", GUIDS[0], SIGNATURES[0], 64,
                               [("Value", 40, kind << 5, None, None)])], "test-sdk")
    return data, schema


class EditTests(unittest.TestCase):
    def test_curve_y_and_array_values_are_reversible_without_metadata_changes(self):
        for data, schema, field, value in ((*array_fixture(), "Values[1]", 1.4),
                                          (*curve_fixture(), "Curve.Points[0].Y", 3.5)):
            candidate, report = edit_values(data, schema, sha(data), [edit(data, schema, field, value)])
            self.assertEqual(named_values(candidate, schema)["leaves"][field]["value"], struct.unpack("<f", struct.pack("<f", value))[0])
            self.assertEqual(revert_values(candidate, report), data)
            self.assertTrue(report["reverse_restore_verified"])
            for old, new in zip(RiffDocument.parse(data).chunks, RiffDocument.parse(candidate).chunks):
                if old.name != b"EBXD":
                    self.assertEqual(old, new)

    def test_numeric_widths_and_integer_precision(self):
        for kind, fmt, value in ((10, "<?", True), (11, "<b", -128), (12, "<B", 255),
                                 (13, "<h", -32768), (14, "<H", 65535), (15, "<i", -(2**31)),
                                 (16, "<I", 2**32-1), (17, "<q", -(2**63)), (18, "<Q", 2**64-1),
                                 (19, "<f", 1.5), (20, "<d", 1.5)):
            data, schema = scalar_fixture(kind, fmt, False if kind == 10 else 0)
            candidate, report = edit_values(data, schema, sha(data), [edit(data, schema, "Value", value)])
            self.assertEqual(named_values(candidate, schema)["leaves"]["Value"]["value"], value)
            self.assertEqual(revert_values(candidate, report), data)

    def test_wrong_baseline_expected_bits_identity_and_duplicate_rejected(self):
        data, schema = array_fixture()
        item = edit(data, schema, "Values[0]", 0.8)
        for digest, items, identity in (("0"*64, [item], None),
                                        (sha(data), [{**item, "expected_hex": "00000000"}], None),
                                        (sha(data), [item, item], None),
                                        (sha(data), [item], {"guid": GUIDS[0], "signature": "00000000"})):
            with self.assertRaises(FormatError):
                edit_values(data, schema, digest, items, identity)

    def test_unknown_names_and_even_unselected_aliases_rejected(self):
        data, schema = array_fixture(aliased=True)
        with self.assertRaises(FormatError):
            edit_values(data, schema, sha(data), [edit(data, schema, "Values[0]", 0.8)])
        data, schema = array_fixture()
        next(iter(schema.types.values()))["properties"][0]["name_source"] = "unresolved name hash"
        with self.assertRaises(FormatError):
            edit_values(data, schema, sha(data), [edit(data, schema, "Values[0]", 0.8)])

    def test_nonfinite_overflow_wrong_types_and_noncanonical_bool_rejected(self):
        for kind, fmt, value in ((12, "<B", -1), (11, "<b", 128), (15, "<i", True),
                                 (15, "<i", 1.5), (10, "<?", 1), (19, "<f", 1e100),
                                 (19, "<f", float("nan")), (20, "<d", float("inf"))):
            data, schema = scalar_fixture(kind, fmt, False if kind == 10 else 0)
            with self.assertRaises(FormatError):
                edit_values(data, schema, sha(data), [edit(data, schema, "Value", value)])
        data, schema = scalar_fixture(10, "<?", False)
        with self.assertRaises(FormatError):
            edit_values(data, schema, sha(data), [{"field": "Value", "expected_hex": "00", "new_hex": "02"}])

    def test_raw_float_bits_are_used_without_rounding_through_json(self):
        data, schema = array_fixture()
        item = edit(data, schema, "Values[0]", 0.8)
        item.pop("value")
        item["new_hex"] = "0100803f"
        candidate, report = edit_values(data, schema, sha(data), [item])
        self.assertEqual(named_values(candidate, schema)["leaves"]["Values[0]"]["raw_hex"], "0100803f")
        self.assertEqual(revert_values(candidate, report), data)

    def test_nonfinite_raw_float_bits_are_rejected(self):
        data, schema = array_fixture()
        expected = named_values(data, schema)["leaves"]["Values[0]"]["raw_hex"]
        for raw in ("0000c07f", "0000807f"):
            with self.assertRaises(FormatError):
                edit_values(data, schema, sha(data), [{"field": "Values[0]", "expected_hex": expected, "new_hex": raw}])

    def test_legacy_diagnostic_policy_no_op_and_missing_fields_rejected(self):
        data, schema = array_fixture()
        for item in (edit(data, schema, "Values[0]", named_values(data, schema)["leaves"]["Values[0]"]["value"]),
                     {"field": "Missing", "expected_hex": "00000000", "value": 1.0},
                     {**edit(data, schema, "Values[0]", 0.8), "offset": 112}):
            with self.assertRaises(FormatError):
                edit_values(data, schema, sha(data), [item])
        schema.allow_legacy_curve_metadata = True
        with self.assertRaises(FormatError):
            edit_values(data, schema, sha(data), [edit(data, schema, "Values[0]", 0.8)])

    def test_restore_rejects_tampered_candidate_and_report(self):
        data, schema = curve_fixture()
        candidate, report = edit_values(data, schema, sha(data), [edit(data, schema, "Curve.Points[0].Y", 3.5)])
        with self.assertRaises(FormatError):
            revert_values(candidate[:-1] + b"x", report)
        for key, value in (("offset", 0), ("after_hex", "00000000"), ("before_hex", "00000000")):
            damaged = copy.deepcopy(report)
            damaged["changes"][0][key] = value
            with self.assertRaises(FormatError):
                revert_values(candidate, damaged)
        damaged = copy.deepcopy(report)
        damaged["changes"].append(damaged["changes"][0])
        with self.assertRaises(FormatError):
            revert_values(candidate, damaged)


class BuildTests(unittest.TestCase):
    def test_json_duplicate_keys_and_nonfinite_constants_rejected(self):
        for data in (b'{"value": 1, "value": 2}', b'{"value": NaN}', b'{"value": Infinity}'):
            with self.assertRaises(ValueError):
                BUILD.strict_json(data)

    def prepare(self, root, bad_second=False):
        game, local = root / "game", root / "local"
        game.mkdir()
        export = local / "export"
        export.mkdir(parents=True)
        sources = {}
        for path in BUILD.SOURCE_FILES:
            target = game / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.encode())
            sources[path] = sha(path.encode())
        data, schema = array_fixture()
        records, entries = [], []
        for i in range(2):
            name = "fifa/attribulator/gameplay/test" + str(i)
            output = "asset" + str(i) + ".ebx"
            (export / output).write_bytes(data)
            records.append({"name": name, "output": output, "decoded_sha256": sha(data)})
            entries.append({"name": name, "expected_sha256": sha(data),
                            "root_identity": named_values(data, schema)["root_identity"],
                            "edits": [edit(data, schema, "Values[0]", 0.8)]})
        if bad_second:
            entries[1]["edits"][0]["expected_hex"] = "00000000"
        manifest = {"all_cas_sha1_verified": True, "all_ebx_roundtrips_verified": True,
                    "source_hashes": sources, "assets": records}
        manifest_data = json.dumps(manifest).encode()
        (export / "manifest.json").write_bytes(manifest_data)
        sdk, shared, plan_path = local / "sdk", local / "shared", local / "plan.json"
        sdk.write_bytes(b"sdk")
        shared.write_bytes(b"shared")
        plan = {"format": "fc27-fixed-edit-plan-v1", "export_manifest_sha256": sha(manifest_data),
                "sdk_sha256": sha(b"sdk"), "shared_types_sha256": sha(b"shared"), "assets": entries}
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return game, export, sdk, shared, plan_path, schema

    def test_build_validates_entire_batch_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, export, sdk, shared, plan, schema = self.prepare(root, bad_second=True)
            output = root / "local" / "built"
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root), \
                    patch.object(BUILD, "check_shared_source"), patch.object(BUILD, "SharedTypes") as shared_factory:
                shared_factory.return_value.adapt.return_value = schema
                with self.assertRaises(FormatError):
                    BUILD.run(game, export, sdk, shared, plan, output)
            self.assertFalse(output.exists())

    def test_changed_installed_index_prevents_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, export, sdk, shared, plan, _ = self.prepare(root)
            (game / "Data/layout.toc").write_bytes(b"updated")
            output = root / "local" / "built"
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root), self.assertRaises(ValueError):
                BUILD.run(game, export, sdk, shared, plan, output)
            self.assertFalse(output.exists())

    def test_successful_batch_preserves_originals_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, export, sdk, shared, plan, schema = self.prepare(root)
            output = root / "local" / "built"
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root), \
                    patch.object(BUILD, "check_shared_source"), patch.object(BUILD, "SharedTypes") as shared_factory:
                shared_factory.return_value.adapt.return_value = schema
                summary = BUILD.run(game, export, sdk, shared, plan, output)
                self.assertEqual(summary["assets_built"], 2)
                self.assertTrue(summary["all_reverse_restores_verified"])
                self.assertEqual((output / "original-assets/fifa/attribulator/gameplay/test0.ebx").read_bytes(), (export / "asset0.ebx").read_bytes())
                with self.assertRaises(FileExistsError):
                    BUILD.run(game, export, sdk, shared, plan, output)

    def test_written_bundle_verification_detects_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, export, sdk, shared, plan, schema = self.prepare(root)
            output = root / "local" / "built"
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root), \
                    patch.object(BUILD, "check_shared_source"), patch.object(BUILD, "SharedTypes") as shared_factory:
                shared_factory.return_value.adapt.return_value = schema
                BUILD.run(game, export, sdk, shared, plan, output)
                self.assertEqual(VERIFY.run(output)["assets_verified"], 2)
                candidate = output / "candidate-assets/fifa/attribulator/gameplay/test0.ebx"
                raw = candidate.read_bytes()
                candidate.write_bytes(raw[:-1] + b"x")
                with self.assertRaises(ValueError):
                    VERIFY.run(output)

    def test_shared_type_provenance_requires_current_initfs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            (game / "Data").mkdir(parents=True)
            initfs = game / "Data/initfs_Win32"
            initfs.write_bytes(b"source-initfs")
            exported = root / "local/types"
            (exported / "Data").mkdir(parents=True)
            shared = exported / "Data/SharedTypeDescriptors.ebx"
            shared.write_bytes(b"types")
            manifest = {"files": [{"layer": "Data", "source_sha256": sha(b"source-initfs"),
                                   "outer_roundtrip_verified": True, "inner_roundtrip_verified": True,
                                   "files": [{"name": shared.name, "sha256": sha(b"types")}]}]}
            (exported / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            BUILD.check_shared_source(shared, game, sha(b"types"))
            initfs.write_bytes(b"updated-initfs")
            with self.assertRaises(ValueError):
                BUILD.check_shared_source(shared, game, sha(b"types"))

    def test_verifier_binds_report_changes_to_the_explicit_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, export, sdk, shared, plan, schema = self.prepare(root)
            output = root / "local" / "built"
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root), \
                    patch.object(BUILD, "check_shared_source"), patch.object(BUILD, "SharedTypes") as shared_factory:
                shared_factory.return_value.adapt.return_value = schema
                BUILD.run(game, export, sdk, shared, plan, output)
                report_path = output / "build-report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["assets"][0]["changes"][0]["field"] = "Values[1]"
                report_path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaises(ValueError):
                    VERIFY.run(output)

    def test_output_inside_game_is_rejected_before_any_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("fc27_export.PROJECT_ROOT", root), self.assertRaises(ValueError):
                BUILD.run(root / "local/game", root / "missing", root / "missing", root / "missing", root / "missing", root / "local/game/built")


if __name__ == "__main__":
    unittest.main()
