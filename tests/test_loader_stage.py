"""离线加载副本、跨层 CAS 回退、版本绑定与封装证据边界。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from fc27_assets import Location, read_toc
from fc27_dbobject import FormatError
from fc27_mount import OfflineMount, header_impact, mount_relative
import fc27_stage_loader as STAGE
import fc27_package as PACKAGE
import fc27_build as BUILD
import test_package_index as package_fixtures


class HeaderTests(unittest.TestCase):
    def test_changed_payload_retained_signature_is_never_claimed_verified(self):
        original = b"\x00\xd1\xce\x01" + bytes(4) + b"S" * 256 + bytes(292) + b"old"
        result = header_impact(original, original[:556] + b"new")
        self.assertTrue(result["header_preserved"])
        self.assertTrue(result["signature_region_nonzero"])
        self.assertTrue(result["payload_changed"])
        self.assertFalse(result["signature_validity_verified"])

    def test_unknown_header_or_short_input_rejected(self):
        for raw in (b"", bytes(556), b"\x00\xd1\xce\x01" + bytes(550)):
            with self.assertRaises(FormatError):
                header_impact(raw, raw)

    def test_noncanonical_windows_and_outside_paths_rejected(self):
        for name in ("Data//x", "Data/./x", "Data/../x", "Patch/CON.txt", "Data/x ",
                     "Data/x.", "Data/x\0", "Data/a:b", "Data/a?b", "data/file", "other/file"):
            with self.subTest(name=name), self.assertRaises(FormatError):
                mount_relative(name)


class LoaderStageTests(unittest.TestCase):
    def context(self, root):
        fixture = package_fixtures.PackageTests()
        game, export, built = fixture.prepare(root)
        (game / "Data/initfs_Win32").write_bytes(b"opaque initfs")
        (game / "Patch/initfs_Win32").write_bytes(b"opaque patch initfs")
        (game / "Data/locale.ini").write_bytes(b"locale unchanged")
        package = root / "local/packaged"
        PACKAGE.run(game, built, export, package, "FC27")
        return game, export, built, package

    def test_stage_and_independent_verify_keep_game_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built, package = self.context(root)
                before = {p.relative_to(game).as_posix(): p.read_bytes() for p in game.rglob("*") if p.is_file()}
                stage = root / "local/staged"
                result = STAGE.run(game, built, export, package, stage)
                verified = STAGE.verify(game, built, export, package, stage)
                self.assertTrue(result["offline_mount_verified"])
                self.assertEqual(verified["bundle_locations_checked"], 6)
                self.assertEqual(verified["selected_memberships_verified"], 4)
                self.assertEqual(result["metadata_copies"], 3)
                for key in ("loadable_mod", "standalone_moddata", "signature_validity_verified",
                            "filesystem_links_created", "game_started_by_tool", "game_files_written"):
                    self.assertFalse(result[key])
                self.assertEqual(before, {p.relative_to(game).as_posix(): p.read_bytes()
                                         for p in game.rglob("*") if p.is_file()})
                with self.assertRaises(FileExistsError):
                    STAGE.run(game, built, export, package, stage)

    def test_explicit_original_fallback_and_cross_layer_registry_union(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built, package = self.context(root)
                stage = root / "local/staged"
                STAGE.run(game, built, export, package, stage)
                report = json.loads((stage / "loader-stage-report.json").read_text(encoding="utf-8"))
                mount = OfflineMount(game, stage / "ModData", {f["path"] for f in report["staged_files"]},
                                     {f["path"]: f for f in report["fallback_files"]})
                original = read_toc((game / "Data/Win32/fc/fcgame/fcgame.toc").read_bytes())[0].locations[0]
                self.assertEqual(mount.resolve(mount.relative_cas(original))[1], "original")
                self.assertEqual(mount.read(original), (game / mount.relative_cas(original)).read_bytes()[:original.size])
                # Patch 的请求可以指向 Data 标识；分层表按实际 CAS ID 的层查包。
                self.assertIn(original.cas_id, mount.registered)
                with self.assertRaises(FormatError):
                    mount.range_path(Location(original.cas_id, 10**9, 1))
                with self.assertRaises(FormatError):
                    mount.range_path(Location(0xA3A00DE300FF, 0, 1))
                with self.assertRaises(FormatError):
                    mount.resolve("Data/unlisted.cas")
                target = game / mount.relative_cas(original)
                target.write_bytes(target.read_bytes() + b"changed")
                with self.assertRaises(FormatError):
                    mount.resolve(mount.relative_cas(original))

    def test_corrupt_or_missing_stage_and_forged_loading_claim_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built, package = self.context(root)
                stage = root / "local/staged"
                STAGE.run(game, built, export, package, stage)
                path = stage / "ModData/Data/locale.ini"
                raw = path.read_bytes()
                path.write_bytes(b"corrupt")
                with self.assertRaises(ValueError):
                    STAGE.verify(game, built, export, package, stage)
                path.unlink()
                with self.assertRaises(ValueError):
                    STAGE.verify(game, built, export, package, stage)
                path.write_bytes(raw)
                report_path = stage / "loader-stage-report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["summary"]["loadable_mod"] = True
                report_path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaises(ValueError):
                    STAGE.verify(game, built, export, package, stage)

    def test_original_metadata_drift_and_mtime_binding_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built, package = self.context(root)
                stage = root / "local/staged"
                STAGE.run(game, built, export, package, stage)
                path = game / "Data/locale.ini"
                raw = path.read_bytes()
                path.write_bytes(b"new version")
                with self.assertRaises(ValueError):
                    STAGE.verify(game, built, export, package, stage)
                path.write_bytes(raw)
                # 恢复字节仍不能静默更换版本：记录包含修改时间。
                with self.assertRaises(ValueError):
                    STAGE.verify(game, built, export, package, stage)

    def test_outputs_in_game_and_metadata_limit_fail_before_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built, package = self.context(root)
                with self.assertRaises(ValueError):
                    STAGE.run(game, built, export, package, game / "ModData")
                output = root / "local/staged"
                with patch.object(STAGE, "MAX_STAGE_BYTES", 1), self.assertRaises(ValueError):
                    STAGE.run(game, built, export, package, output)
                self.assertFalse(output.exists())

    def test_extra_output_files_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built, package = self.context(root)
                stage = root / "local/staged"
                STAGE.run(game, built, export, package, stage)
                (stage / "ModData/Data/unexpected.bin").write_bytes(b"unexpected")
                with self.assertRaises(ValueError):
                    STAGE.verify(game, built, export, package, stage)

    def test_package_changes_after_verification_fail_before_stage_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built, package = self.context(root)
                original_verify = STAGE.verify_package
                def changed_package(*args):
                    result = original_verify(*args)
                    cas = next((package / "index-candidate").rglob("*.cas"))
                    raw = cas.read_bytes()
                    cas.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                    return result
                output = root / "local/staged"
                with patch.object(STAGE, "verify_package", side_effect=changed_package), self.assertRaises(ValueError):
                    STAGE.run(game, built, export, package, output)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
