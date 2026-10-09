"""EXE 的外部工作区边界、资源分离与桌面单实例锁。"""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import fc27_runtime as RUNTIME
from fc27_desktop_session import DesktopSession


def make_workspace(path):
    (path / "resources").mkdir(parents=True)
    (path / "resources/whole-match-study.json").write_text("{}", encoding="utf-8")
    (path / "local").mkdir()
    return path.resolve()


class RuntimeTests(unittest.TestCase):
    def test_frozen_resources_and_external_workspace_remain_separate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(RUNTIME, "_workspace", None):
            base = Path(directory)
            bundle = make_workspace(base / "_MEI")
            workspace = make_workspace(base / "research")
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(bundle), create=True):
                with self.assertRaises(ValueError):
                    RUNTIME.project_root()
                RUNTIME.configure_workspace(workspace)
                self.assertEqual(RUNTIME.project_root(), workspace)
                self.assertEqual(RUNTIME.resource_root(), bundle)
                with self.assertRaises(ValueError):
                    RUNTIME.configure_workspace(bundle)
                self.assertEqual(RUNTIME.project_root(), workspace)

    def test_discovery_uses_executable_ancestors_instead_of_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = make_workspace(Path(directory) / "research")
            executable = workspace / "dist/release/FC27Manager.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            self.assertEqual(RUNTIME.discover_workspace(executable), workspace)
            self.assertIsNone(RUNTIME.discover_workspace(Path(directory) / "standalone/manager.exe"))

    def test_unmarked_folder_is_rejected_without_creating_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(ValueError):
                RUNTIME.validate_workspace(path)
            self.assertEqual(list(path.iterdir()), [])

    def test_workspace_inside_game_is_rejected_even_with_workspace_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            (game / "FC27.exe").write_bytes(b"fixture")
            workspace = make_workspace(game / "research")
            with self.assertRaises(ValueError):
                RUNTIME.validate_workspace(workspace)

    def test_source_mode_uses_repository_resources(self):
        with patch.object(RUNTIME, "_workspace", None), patch.object(sys, "frozen", False, create=True):
            self.assertEqual(RUNTIME.project_root(), ROOT)
            self.assertEqual(RUNTIME.resource_root(), ROOT)


class SessionTests(unittest.TestCase):
    def test_second_instance_cannot_replace_active_record_and_lock_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = make_workspace(Path(directory))
            manager = workspace / "local/manager"
            with DesktopSession(workspace, manager) as first:
                first.update(origin="http://127.0.0.1:12345", state="running")
                before = first.path.read_bytes()
                with self.assertRaises(ValueError):
                    with DesktopSession(workspace, manager):
                        self.fail("第二实例不应取得锁")
                self.assertEqual(first.path.read_bytes(), before)
                self.assertFalse(manager.exists())
            self.assertEqual(json.loads(first.path.read_text(encoding="utf-8"))["state"], "stopped")
            with DesktopSession(workspace, manager):
                pass

    def test_hardlinked_lock_is_rejected_without_changing_external_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = make_workspace(Path(directory))
            session = DesktopSession(workspace, workspace / "local/manager")
            outside = workspace / "outside.txt"
            outside.write_bytes(b"unchanged")
            os.link(outside, session.lock_path)
            with self.assertRaises(ValueError):
                with session:
                    self.fail("硬链接锁不得被接受")
            self.assertEqual(outside.read_bytes(), b"unchanged")
            self.assertFalse(session.path.exists())


if __name__ == "__main__":
    unittest.main()
