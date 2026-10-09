"""验证报告路径限制，不使用真实游戏文件。"""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("offline_cli", ROOT / "scripts" / "fc27_offline.py")
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


class OutputBoundaryTests(unittest.TestCase):
    def test_report_only_created_in_project_local(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            with patch.object(CLI, "PROJECT_ROOT", root):
                output = root / "local" / "report.json"
                CLI.write_report(output, game, {"game_files_written": False})
                self.assertTrue(output.exists())
                with self.assertRaises(ValueError):
                    CLI.write_report(root / "outside.json", game, {})
                self.assertFalse((root / "outside.json").exists())

    def test_report_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(CLI, "PROJECT_ROOT", root):
                output = root / "local" / "report.json"
                CLI.write_report(output, root / "game", {"original": True})
                original = output.read_bytes()
                with self.assertRaises(FileExistsError):
                    CLI.write_report(output, root / "game", {"replacement": True})
                self.assertEqual(output.read_bytes(), original)

    def test_game_path_rejected_even_inside_project_local(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "local" / "game"
            game.mkdir(parents=True)
            with patch.object(CLI, "PROJECT_ROOT", root), self.assertRaises(ValueError):
                CLI.write_report(game / "report.json", game, {})
            self.assertFalse((game / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
