"""离线流水线的阶段顺序、模块选择、失败状态和文件边界。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fc27_pipeline as PIPELINE


class PipelineTests(unittest.TestCase):
    def config(self, root):
        game = root / "game"
        game.mkdir()
        recipe_path = root / "resources/recipe.json"
        recipe_path.parent.mkdir()
        recipe_path.write_bytes((ROOT / "resources/whole-match-study.json").read_bytes())
        return {"game_root": game, "fc27_export": root / "local/export", "recipe": recipe_path,
                "sample": root / "local/sample", "sdk": root / "local/sdk",
                "shared_types": root / "local/shared", "codec": root / "local/codec"}

    @staticmethod
    def study(*args):
        output = args[-1]
        (output / "plans").mkdir(parents=True)
        (output / "plans/combined.json").write_text("{}")
        (output / "plans/transitions.json").write_text("{}")
        (output / "study-report.json").write_text(json.dumps({"modules": [
            {"id": "transitions", "assets_rejected": 0, "assets_ready": 1}]}))
        return {"assets_ready": 1}

    @staticmethod
    def build(*args):
        args[-1].mkdir()
        return {"assets_built": 1, "changed_values": 5, "changed_bytes": 10}

    @staticmethod
    def package(*args):
        args[3].mkdir()
        return {"assets_packaged": 1}

    def test_module_selection_and_success_are_recorded_after_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.config(root)
            output = root / "local/pipeline"
            calls = []
            def checked_study(*args):
                calls.append("study")
                return self.study(*args)
            def checked_build(*args):
                calls.append("build")
                self.assertEqual(args[-2].name, "transitions.json")
                return self.build(*args)
            def checked_package(*args):
                calls.append("package")
                return self.package(*args)
            def checked_verify(*args):
                calls.append("verify")
                return {"files_verified": 11}
            with patch.object(PIPELINE, "load_config", return_value=config), patch("fc27_export.PROJECT_ROOT", root), \
                    patch.object(PIPELINE, "study", side_effect=checked_study), \
                    patch.object(PIPELINE, "build", side_effect=checked_build), \
                    patch.object(PIPELINE, "package", side_effect=checked_package), \
                    patch.object(PIPELINE, "verify", side_effect=checked_verify):
                result = PIPELINE.run(root / "config", output, "transitions")
                self.assertEqual(calls, ["study", "build", "package", "verify"])
                self.assertTrue(result["completed"])
                self.assertFalse(result["loadable_mod"])
                report = json.loads((output / "pipeline-report.json").read_text(encoding="utf-8"))
                self.assertTrue(report["completed"])
                self.assertEqual(report["completed_stage"], "verify")
                with self.assertRaises(FileExistsError):
                    PIPELINE.run(root / "config", output)

    def test_failed_compile_records_stage_without_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.config(root)
            output = root / "local/pipeline"
            with patch.object(PIPELINE, "load_config", return_value=config), patch("fc27_export.PROJECT_ROOT", root), \
                    patch.object(PIPELINE, "study", side_effect=self.study), \
                    patch.object(PIPELINE, "build", side_effect=ValueError("wrong baseline")), \
                    patch.object(PIPELINE, "package") as packager, self.assertRaises(ValueError):
                PIPELINE.run(root / "config", output)
            packager.assert_not_called()
            report = json.loads((output / "pipeline-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["failed_stage"], "build")
            self.assertFalse(report["completed"])

    def test_partial_selected_module_prevents_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.config(root)
            output = root / "local/pipeline"
            def rejected_study(*args):
                result = self.study(*args)
                (args[-1] / "study-report.json").write_text(json.dumps({"modules": [
                    {"id": "transitions", "assets_rejected": 1, "assets_ready": 1}]}))
                return result
            with patch.object(PIPELINE, "load_config", return_value=config), patch("fc27_export.PROJECT_ROOT", root), \
                    patch.object(PIPELINE, "study", side_effect=rejected_study), \
                    patch.object(PIPELINE, "build") as builder, self.assertRaises(ValueError):
                PIPELINE.run(root / "config", output, "transitions")
            builder.assert_not_called()
            report = json.loads((output / "pipeline-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["failed_stage"], "study")

    def test_game_output_and_unknown_module_rejected_before_study(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.config(root)
            with patch.object(PIPELINE, "load_config", return_value=config), patch("fc27_export.PROJECT_ROOT", root), \
                    patch.object(PIPELINE, "study") as reviewer:
                for output, module in ((config["game_root"] / "output", "all"), (root / "local/new", "../outside")):
                    with self.assertRaises(ValueError):
                        PIPELINE.run(root / "config", output, module)
                reviewer.assert_not_called()

    def test_config_unknown_keys_and_inputs_outside_local_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / "local"
            local.mkdir()
            config_path = local / "config.json"
            with patch.object(PIPELINE, "PROJECT_ROOT", root), patch("fc27_build.PROJECT_ROOT", root):
                config_path.write_text(json.dumps({"unknown": "x"}))
                with self.assertRaises(ValueError):
                    PIPELINE.load_config(config_path)
                config = {key: "outside" for key in PIPELINE.CONFIG_KEYS}
                (root / "outside").mkdir()
                config_path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    PIPELINE.load_config(config_path)

    def test_loader_stage_is_verified_after_package_and_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.config(root)
            output = root / "local/pipeline"
            with patch.object(PIPELINE, "load_config", return_value=config), patch("fc27_export.PROJECT_ROOT", root), \
                    patch.object(PIPELINE, "study", side_effect=self.study), \
                    patch.object(PIPELINE, "build", side_effect=self.build), \
                    patch.object(PIPELINE, "package", side_effect=self.package), \
                    patch.object(PIPELINE, "verify", return_value={"files_verified": 11}), \
                    patch.object(PIPELINE, "stage_loader", return_value={"offline_mount_verified": True}) as stager, \
                    patch.object(PIPELINE, "verify_loader_stage", side_effect=ValueError("changed fallback")), \
                    self.assertRaises(ValueError):
                PIPELINE.run(root / "config", output, with_loader_stage=True)
            stager.assert_called_once()
            report = json.loads((output / "pipeline-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["failed_stage"], "verify-loader-stage")
            self.assertFalse(report["completed"])
            self.assertFalse(report["loadable_mod"])


if __name__ == "__main__":
    unittest.main()
