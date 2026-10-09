"""预设复现、跨版本拒绝、无 SDK 使用路径和普通用户能力边界。"""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from fc27_management import UNVERIFIED, json_bytes
import fc27_build as BUILD
import fc27_presets as PRESETS
import fc27_player as PLAYER
import fc27_preset as GENERATOR
import fc27_desktop as DESKTOP
import fc27_web as WEB
import test_package_index as package_fixtures


def fixture(root):
    game, exported, built = package_fixtures.PackageTests().prepare(root)
    report = json.loads((built / "build-report.json").read_bytes())
    recipe = root / "local/recipe.json"
    recipe.write_bytes(json_bytes({"modules": [{"id": "role_execution", "title": "角色执行",
        "assets": [{"name": a["resource"]} for a in report["assets"]]}]}))
    preset_file = root / "local/preset.json"
    GENERATOR.create(built, exported / "manifest.json", recipe, preset_file)
    return game, built, json.loads(preset_file.read_bytes())


class PresetTests(unittest.TestCase):
    def test_public_resource_contains_only_bound_edit_descriptions(self):
        preset = PRESETS.load_preset()
        self.assertEqual(len(preset["assets"]), 16)
        self.assertEqual(sum(len(a["changes"]) for a in preset["assets"]), 147)
        self.assertEqual(len(preset["modules"]), 6)
        self.assertFalse(preset["safety"]["loadable_mod"])
        self.assertLess(len(json_bytes(preset)), PRESETS.MAX_PRESET)

    def test_no_sdk_prepare_reproduces_candidates_and_never_installs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, reviewed, preset = fixture(root)
                before = {p.relative_to(game).as_posix(): p.read_bytes() for p in game.rglob("*") if p.is_file()}
                # 夹具准备之后，SDK/参考包/类型适配/网络接口均不得再使用。
                with patch.object(BUILD, "SharedTypes", side_effect=AssertionError("runtime SDK used")), \
                        patch.object(PRESETS, "codec_path", side_effect=AssertionError("codec unnecessary")):
                    result = PRESETS.prepare(root, game, "role_execution", preset)
                output = root / result["output"]
                for path in (reviewed / "candidate-assets").rglob("*.ebx"):
                    self.assertEqual(path.read_bytes(), (output / "built/candidate-assets" / path.relative_to(reviewed / "candidate-assets")).read_bytes())
                self.assertTrue(result["offline_mount_verified"])
                self.assertFalse(result["enabled"])
                self.assertFalse(result["installed"])
                self.assertFalse(result["loadable_mod"])
                self.assertEqual(before, {p.relative_to(game).as_posix(): p.read_bytes() for p in game.rglob("*") if p.is_file()})
                with patch.object(PLAYER, "load_preset", return_value=preset):
                    value = PLAYER.snapshot(root, [str(game)])
                self.assertTrue(value["can_prepare"])
                self.assertIsNotNone(value["last_prepared"])
                self.assertFalse(value["can_enable"])

    def test_updated_game_rejected_before_dependency_or_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, _, preset = fixture(root)
                toc = game / "Patch/layout.toc"
                toc.write_bytes(toc.read_bytes() + b"changed")
                with patch.object(PRESETS, "codec_path", side_effect=AssertionError("must not download")):
                    with self.assertRaisesRegex(ValueError, "游戏已更新"):
                        PRESETS.prepare(root, game, "all", preset)
                self.assertFalse((root / "local/preset-builds").exists())

    def test_whole_asset_hash_rejects_drift_even_when_edited_bytes_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                _, built, preset = fixture(root)
                item = preset["assets"][0]
                raw = (built / "original-assets" / (item["name"] + ".ebx")).read_bytes()
                changed = bytearray(raw)
                changed[-1] ^= 1
                with self.assertRaisesRegex(ValueError, "拒绝按旧偏移"):
                    PRESETS.apply_bound_asset(bytes(changed), item)

    def test_overlaps_nonfinite_values_and_false_capabilities_rejected(self):
        original = PRESETS.load_preset()
        for kind in ("overlap", "nonfinite", "enabled", "root", "candidate"):
            preset = copy.deepcopy(original)
            if kind == "overlap":
                preset["assets"][0]["changes"][1]["offset"] = preset["assets"][0]["changes"][0]["offset"]
            elif kind == "nonfinite":
                preset["assets"][0]["changes"][0]["after_hex"] = "0000807f"
            elif kind == "enabled":
                preset["safety"]["loadable_mod"] = True
            elif kind == "root":
                preset["assets"][0]["root_identity"]["guid"] = "not-a-guid"
            else:
                preset["assets"][0]["candidate_sha256"] = "bad"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                PRESETS.validate_preset(preset)

    def test_supported_module_subsets_and_unknown_module_rejected(self):
        preset = PRESETS.load_preset()
        self.assertEqual(len(PRESETS.select_assets(preset, "role_execution")), 1)
        self.assertEqual(len(PRESETS.select_assets(preset, "all")), 16)
        with self.assertRaises(ValueError):
            PRESETS.select_assets(preset, "unknown")

    def test_game_link_and_output_in_game_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, _, preset = fixture(root)
                (game / "local").mkdir()
                with self.assertRaises(ValueError):
                    PRESETS.prepare(game, game, "all", preset)
                alias = root / "alias"
                try:
                    alias.symlink_to(game, target_is_directory=True)
                except OSError:
                    return
                with self.assertRaises(ValueError):
                    PRESETS.prepare(root, alias, "all", preset)

    def test_codec_zip_mismatch_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            archive = root / "local/dependencies" / (PRESETS.CODEC_ZIP_SHA + ".zip")
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"modified archive")
            with patch.object(PRESETS.urllib.request, "urlopen", side_effect=AssertionError("no network")):
                with self.assertRaisesRegex(ValueError, "已缓存依赖发行包被修改"):
                    PRESETS.codec_path(root)

    def test_dependency_download_hash_failure_never_extracts_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "local").mkdir()
            with patch.object(PRESETS.urllib.request, "urlopen", return_value=io.BytesIO(b"unapproved download")):
                with self.assertRaisesRegex(ValueError, "大小或 SHA256 不符"):
                    PRESETS.codec_path(root)
            self.assertFalse((root / "local/dependencies/oo2core_9_win64.dll").exists())
            self.assertEqual(len(list((root / "local/dependencies").glob("*.part"))), 1)

    def test_cached_archive_only_extracts_the_approved_decoder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as archive:
                archive.writestr("tools/oo2core_9_win64.dll", b"approved synthetic codec")
                archive.writestr("tools/unknown.dll", b"must not be extracted")
            sha = hashlib.sha256(raw.getvalue()).hexdigest()
            target = root / "local/dependencies" / (sha + ".zip")
            target.parent.mkdir(parents=True)
            target.write_bytes(raw.getvalue())
            with patch.object(PRESETS, "CODEC_ZIP_SHA", sha), patch.object(PRESETS, "CODEC_ZIP_BYTES", len(raw.getvalue())), \
                    patch.object(PRESETS, "OODLE_SHA256", hashlib.sha256(b"approved synthetic codec").hexdigest()), \
                    patch.object(PRESETS.urllib.request, "urlopen", side_effect=AssertionError("cached, no network")):
                path = PRESETS.codec_path(root)
            self.assertEqual(path.read_bytes(), b"approved synthetic codec")
            self.assertFalse(list(root.rglob("unknown.dll")))

    def test_player_without_game_or_manager_never_claims_enabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "local").mkdir()
            value = PLAYER.snapshot(root, [])
            self.assertFalse(value["can_prepare"])
            self.assertFalse(value["can_enable"])
            self.assertFalse(value["enabled"])
            self.assertEqual(len(value["modules"]), 7)

    def test_player_can_save_different_version_but_will_not_prepare(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, _, preset = fixture(root)
                with patch.object(PLAYER, "load_preset", return_value=preset):
                    PLAYER.choose_game(root, str(game))
                    (game / "Data/layout.toc").write_bytes(b"new version")
                    value = PLAYER.snapshot(root, [])
                self.assertTrue(value["game_detected"])
                self.assertFalse(value["can_prepare"])
                self.assertFalse(value["can_enable"])
                self.assertIn("预设不同", value["error"])

    def test_player_api_has_no_install_or_launch_capability(self):
        self.assertEqual(WEB.validate_action({"action": "player-prepare", "module": "all"})["module"], "all")
        for action in ("player-enable", "install", "launch", "player-restore"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                WEB.validate_action({"action": action})
        with self.assertRaises(ValueError):
            WEB.validate_action({"action": "player-prepare", "module": "all", "preset": "untrusted.json"})

    def test_standalone_exe_creates_workspace_without_prompt_or_temporary_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(base)}):
                result = DESKTOP.choose_workspace()
                self.assertEqual(result, base / "FC27CareerLab/workspace")
                self.assertTrue((result / "local").is_dir())
                self.assertEqual(DESKTOP.choose_workspace(), result)


if __name__ == "__main__":
    unittest.main()
