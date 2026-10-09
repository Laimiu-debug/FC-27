"""跨版本移植的名称身份、曲线几何、异常策略与原子计划测试。"""

import copy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from fc27_dbobject import FormatError
from fc27_schema import named_values
from fc27_reference import prepare_transfer
from test_schema_candidate import array_fixture, curve_fixture
import test_edit_build as edit_fixtures

SPEC = importlib.util.spec_from_file_location("study_cli", ROOT / "scripts/fc27_study.py")
STUDY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STUDY)


def named_schema(schema):
    for index, definition in enumerate(schema.types.values()):
        definition["name_hash"] = f"{index+1:08x}"
        for field_index, prop in enumerate(definition["properties"]):
            prop["name_hash"] = f"{field_index+100:08x}"
    return schema


def full_curve_fixture():
    raw, schema = curve_fixture()
    point = next(d for d in schema.types.values() if d["name"] == "FloatCurvePoint")
    for name, offset in (("InTangentOffsetX", 8), ("InTangentOffsetY", 12),
                         ("OutTangentOffsetX", 20), ("OutTangentOffsetY", 24)):
        point["properties"].append({"name": name, "offset": offset, "flags": 0x8267,
                                    "reference": None, "array_element": None})
    return raw, named_schema(schema)


def change(raw, schema, changes):
    mapped = named_values(raw, schema)
    result = bytearray(raw)
    for path, value in changes.items():
        leaf = mapped["leaves"][path]
        struct.pack_into(leaf["format"], result, leaf["offset"], value)
    return bytes(result)


class TransferStudyTests(unittest.TestCase):
    def test_float_array_full_selection_dry_build_and_noop(self):
        raw, schema = array_fixture()
        named_schema(schema)
        reference = change(raw, schema, {"Values[1]": 1.4})
        plan, report = prepare_transfer(raw, reference, schema, schema, ["Values"])
        self.assertEqual(len(plan["edits"]), 1)
        self.assertEqual(plan["edits"][0]["field"], "Values[1]")
        self.assertTrue(report["reverse_restore_verified"])
        self.assertFalse(report["gameplay_effect_verified"])
        self.assertTrue(report["comparison_includes_version_differences"])
        unchanged, report = prepare_transfer(raw, raw, schema, schema, ["Values"])
        self.assertEqual(unchanged["edits"], [])
        self.assertEqual(report["status"], "unchanged")

    def test_curve_y_and_y_tangents_move_together_with_type_proof(self):
        raw, schema = full_curve_fixture()
        reference = change(raw, schema, {"Curve.Points[0].Y": 3.5, "Curve.Points[0].InTangentOffsetY": 0.4,
                                          "Curve.Points[0].OutTangentOffsetY": 0.6})
        plan, report = prepare_transfer(raw, reference, schema, schema, ["Curve"])
        self.assertEqual(len(plan["edits"]), 3)
        self.assertEqual(len(report["curve_type_proofs"]), 2)
        self.assertTrue(report["reverse_restore_verified"])
        self.assertTrue(all(path["field"].endswith("Y") for path in plan["edits"]))

    def test_curve_x_bounds_tangent_x_and_enum_changes_rejected(self):
        raw, schema = full_curve_fixture()
        for field in ("Curve.MinX", "Curve.MaxX", "Curve.Points[0].X", "Curve.Points[0].InTangentOffsetX",
                      "Curve.Points[0].OutTangentOffsetX", "Curve.Points[0].CurveType"):
            leaf = named_values(raw, schema)["leaves"][field]
            reference = change(raw, schema, {field: leaf["value"] + 1})
            with self.subTest(field=field), self.assertRaises(FormatError):
                prepare_transfer(raw, reference, schema, schema, ["Curve"])

    def test_missing_or_different_root_and_field_name_hash_rejected(self):
        raw, schema = array_fixture()
        named_schema(schema)
        for kind in ("root", "field", "missing"):
            reference_schema = copy.deepcopy(schema)
            definition = next(iter(reference_schema.types.values()))
            if kind == "root":
                definition["name_hash"] = "ffff0000"
            else:
                definition["properties"][0]["name_hash"] = None if kind == "missing" else "ffff0000"
            with self.assertRaises(FormatError):
                prepare_transfer(raw, raw, schema, reference_schema, ["Values"])

    def test_curve_point_type_hash_mismatch_rejected(self):
        raw, schema = full_curve_fixture()
        reference_schema = copy.deepcopy(schema)
        point = next(d for d in reference_schema.types.values() if d["name"] == "FloatCurvePoint")
        point["name_hash"] = "f0000000"
        with self.assertRaises(FormatError):
            prepare_transfer(raw, raw, schema, reference_schema, ["Curve"])

    def test_diagnostic_strategy_and_nonfinite_reference_rejected(self):
        raw, schema = array_fixture()
        named_schema(schema)
        reference = change(raw, schema, {"Values[0]": float("inf")})
        with self.assertRaises(FormatError):
            prepare_transfer(raw, reference, schema, schema, ["Values"])
        schema.allow_legacy_curve_metadata = True
        with self.assertRaises(FormatError):
            prepare_transfer(raw, raw, schema, schema, ["Values"])

    def test_partial_missing_root_selector_does_not_return_partial_plan(self):
        raw, schema = array_fixture()
        named_schema(schema)
        reference = change(raw, schema, {"Values[0]": 1.7})
        for fields in (["Values", "Missing"], ["Values", "Values"], ["Values[0]"], [], [{}]):
            with self.assertRaises(FormatError):
                prepare_transfer(raw, reference, schema, schema, fields)

    def test_array_identifier_shape_mismatch_rejected(self):
        raw, schema = array_fixture()
        named_schema(schema)
        target, source = named_values(raw, schema), named_values(raw, schema)
        source["shapes"]["Values"]["array_identifier"] = "99999999"
        with patch("fc27_reference.named_values", side_effect=[target, source]), self.assertRaises(FormatError):
            prepare_transfer(raw, raw, schema, schema, ["Values"])


class RecipeStudyTests(unittest.TestCase):
    def test_committed_recipe_has_six_disjoint_modules_and_16_assets(self):
        recipe = json.loads((ROOT / "resources/whole-match-study.json").read_text(encoding="utf-8"))
        STUDY.validate_recipe(recipe)
        self.assertEqual(len(recipe["modules"]), 6)
        self.assertEqual(sum(len(m["assets"]) for m in recipe["modules"]), 16)

    def test_recipe_unknown_keys_duplicate_asset_and_unsafe_module_rejected(self):
        recipe = json.loads((ROOT / "resources/whole-match-study.json").read_text(encoding="utf-8"))
        changed = copy.deepcopy(recipe)
        changed["modules"][0]["id"] = "../outside"
        duplicated = copy.deepcopy(recipe)
        duplicated["modules"][1]["assets"].append(duplicated["modules"][0]["assets"][0])
        for value in ({**recipe, "extra": True}, changed, duplicated):
            with self.assertRaises(ValueError):
                STUDY.validate_recipe(value)

    def test_reserved_module_output_names_cannot_override_combined_plan_or_windows_device(self):
        recipe = json.loads((ROOT / "resources/whole-match-study.json").read_text(encoding="utf-8"))
        for identifier in ("all", "combined", "con", "nul", "com1", "lpt9"):
            changed = copy.deepcopy(recipe)
            changed["modules"][0]["id"] = identifier
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                STUDY.validate_recipe(changed)

    def test_study_run_and_atomic_rejection_generate_audit(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with patch.object(STUDY, "PROJECT_ROOT", root), patch.object(edit_fixtures.BUILD, "PROJECT_ROOT", root), \
                        patch("fc27_export.PROJECT_ROOT", root), patch("fc27_build.PROJECT_ROOT", root):
                    game, export, sdk, shared, _, schema = edit_fixtures.BuildTests().prepare(root)
                    named_schema(schema)
                    original = (export / "asset0.ebx").read_bytes()
                    reference = change(original, schema, {"Values[1]": 1.4})
                    name = "fifa/attribulator/gameplay/test0"
                    recipe = {"format": "fc27-whole-match-study-v1", "id": "test", "description": "test",
                              "modules": [{"id": "module", "title": "测试", "hypothesis": "待验证",
                                           "assets": [{"name": name, "fields": ["Values", "Missing"] if invalid else ["Values"]}]}]}
                    recipe_path = root / "resources/test.json"
                    recipe_path.parent.mkdir()
                    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
                    sample, codec = root / "local/sample", root / "local/codec"
                    sample.write_bytes(b"sample")
                    codec.write_bytes(b"not executed")
                    output = root / "local/study"
                    sample_asset = SimpleNamespace(name=name, decode=lambda _: reference)
                    with patch.object(STUDY, "check_shared_source"), patch.object(STUDY, "OodleDecoder"), \
                            patch.object(STUDY, "read_sample", return_value=(SimpleNamespace(title="test"), [sample_asset])), \
                            patch.object(STUDY.Schemas, "from_sdk", return_value=schema), patch.object(STUDY, "SharedTypes") as factory:
                        factory.return_value.adapt.return_value = schema
                        summary = STUDY.run(game, export, sample, sdk, shared, codec, recipe_path, output)
                    self.assertEqual(summary["assets_ready"], 0 if invalid else 1)
                    self.assertEqual(summary["assets_rejected"], 1 if invalid else 0)
                    self.assertEqual((output / "plans/combined.json").exists(), not invalid)
                    self.assertTrue((output / "review.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
                    self.assertTrue((output / "study-report.json").exists())
                    with self.assertRaises(FileExistsError):
                        STUDY.run(game, export, sample, sdk, shared, codec, recipe_path, output)


if __name__ == "__main__":
    unittest.main()
