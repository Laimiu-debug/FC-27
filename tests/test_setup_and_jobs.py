"""首次使用边界、配置保留、任务重启与脱敏诊断。"""

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import fc27_setup as SETUP
import fc27_manager as CLI
import fc27_web as WEB
from fc27_jobs import JobStore
from fc27_management import UNVERIFIED
from fc27_progress import emit, listen
from fc27_schema_cache import cached, scoped, stats
from fc27_diagnostics import report


def game_fixture(root):
    game = root / "game"
    for layer in ("Data", "Patch"):
        (game / layer / "Win32/fc/fcgame").mkdir(parents=True)
        (game / layer / "layout.toc").write_bytes(b"read-only baseline")
        (game / layer / "Win32/fc/fcgame/fcgame.toc").write_bytes(b"read-only baseline")
    return game


class SetupTests(unittest.TestCase):
    def test_new_workspace_copies_only_public_templates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = SETUP.create_workspace(Path(temporary) / "中文 工作区")
            self.assertTrue((root / "local/inputs").is_dir())
            self.assertEqual((root / "resources/whole-match-study.json").read_bytes(),
                             (ROOT / "resources/whole-match-study.json").read_bytes())
            self.assertFalse(any((root / "local").rglob("*.dll")))
            self.assertEqual((root / ".gitignore").read_text(), "/local/\n")

    def test_nonempty_and_game_descendant_workspaces_are_rejected_without_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "keep").write_bytes(b"keep")
            with self.assertRaises(ValueError):
                SETUP.create_workspace(root)
            game = game_fixture(root)
            with self.assertRaises(ValueError):
                SETUP.create_workspace(game / "research")
            self.assertFalse((game / "research").exists())
            self.assertEqual((root / "keep").read_bytes(), b"keep")

    def test_missing_inputs_are_reported_and_unknown_codec_is_never_executed(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = SETUP.create_workspace(Path(temporary) / "project")
            game = game_fixture(Path(temporary))
            config = {**SETUP.DEFAULTS, "game_root": str(game)}
            result = SETUP.readiness(project, config)
            self.assertFalse(result["inputs_present"])
            self.assertTrue(result["checks"][0]["ready"])
            codec = project / config["codec"]
            codec.write_bytes(b"untrusted DLL")
            with patch("ctypes.WinDLL", create=True) as loader:
                result = SETUP.readiness(project, config)
                loader.assert_not_called()
            self.assertIn("散列", result["checks"][-2]["message"])

    def test_configuration_update_preserves_old_config_and_game_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = SETUP.create_workspace(base / "project")
            game = game_fixture(base)
            before = {str(p.relative_to(game)): p.read_bytes() for p in game.rglob("*.toc")}
            values = {**SETUP.DEFAULTS, "game_root": str(game)}
            manager = project / "local/manager"
            with patch.object(CLI, "PROJECT_ROOT", project):
                SETUP.configure(project, manager, values, CLI.initialize)
                first = CLI.Manager(manager).config_path
                raw = first.read_bytes()
                SETUP.configure(project, manager, values, CLI.initialize)
                second = CLI.Manager(manager).config_path
                self.assertNotEqual(first, second)
                self.assertEqual(first.read_bytes(), raw)
            self.assertEqual({str(p.relative_to(game)): p.read_bytes() for p in game.rglob("*.toc")}, before)

    def test_external_research_input_and_game_output_are_rejected_before_config_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = SETUP.create_workspace(base / "project")
            game = game_fixture(base)
            values = {**SETUP.DEFAULTS, "game_root": str(game), "sdk": str(base / "outside.dll")}
            with self.assertRaises(ValueError):
                SETUP.configure(project, project / "local/manager", values, CLI.initialize)
            self.assertFalse((project / "local/onboarding").exists())
            with self.assertRaises(ValueError):
                SETUP.configure(project, game / "manager", {**SETUP.DEFAULTS, "game_root": str(game)}, CLI.initialize)
            self.assertFalse((game / "manager").exists())


class HistoryAndProgressTests(unittest.TestCase):
    def test_restart_marks_unfinished_task_interrupted_without_replay_or_manager_creation(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(CLI, "PROJECT_ROOT", Path(temporary)):
            project = Path(temporary)
            app = WEB.Application(project / "local/manager")
            job = {"id": "a" * 16, "action": "build", "label": "构建", "subject": "example", "selected": [],
                   "state": "running", "phase": "编译", "started_at": "2026-10-09T00:00:00+00:00",
                   "finished_at": None, "elapsed_seconds": None, "result": None, "error": "", "events": []}
            app.store.save(job)
            with patch.object(CLI.Manager, "build") as build:
                restarted = WEB.Application(app.root)
                build.assert_not_called()
            self.assertFalse(restarted.busy)
            self.assertEqual(restarted.jobs[0]["state"], "interrupted")
            self.assertTrue(restarted.jobs[0]["historical"])
            self.assertFalse(app.root.exists())

    def test_corrupted_and_hardlinked_records_are_not_read_as_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            store = JobStore(project, project / "local/manager")
            store.folder.mkdir(parents=True)
            (store.folder / ("a" * 16 + ".json")).write_text("{broken")
            other = project / "private"
            other.write_bytes(b"private")
            os.link(other, store.folder / ("b" * 16 + ".json"))
            jobs, warnings = store.load()
            self.assertEqual(jobs, [])
            self.assertEqual(len(warnings), 2)
            self.assertEqual(other.read_bytes(), b"private")

    def test_history_write_failure_does_not_leave_application_busy(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(CLI, "PROJECT_ROOT", Path(temporary)):
            app = WEB.Application(Path(temporary) / "local/manager")
            with patch.object(app.store, "save", side_effect=OSError("disk full")):
                app.submit({"action": "refresh"})
                app.worker.join(3)
            self.assertFalse(app.busy)
            self.assertIn("history_error", app.jobs[-1])

    def test_original_operation_and_snapshot_double_failure_always_releases_busy(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(CLI, "PROJECT_ROOT", Path(temporary)):
            app = WEB.Application(Path(temporary) / "local/manager")
            with patch.object(CLI, "Manager", side_effect=RuntimeError("original")), \
                    patch.object(app, "read_snapshot", side_effect=RuntimeError("snapshot")):
                app.submit({"action": "refresh"})
                app.worker.join(3)
            self.assertFalse(app.busy)
            self.assertFalse(app.worker.is_alive())
            self.assertIn("original", app.jobs[-1]["error"])
            self.assertIn("snapshot", app.jobs[-1]["error"])

    def test_progress_is_delivered_and_scope_is_removed(self):
        events = []
        with listen(events.append):
            emit("study", assets_total=3, assets_read=1)
        emit("build")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["counts"]["assets_total"], 3)

    def test_schema_cache_is_content_bound_copied_and_scoped_to_one_operation(self):
        calls = []
        def factory():
            calls.append(True)
            return {"fields": [1]}
        @scoped
        def operation():
            first = cached(("sdk-sha", "shared-sha", "manifest-sha", "guid"), factory)
            first["fields"].append(9)
            self.assertEqual(cached(("sdk-sha", "shared-sha", "manifest-sha", "guid"), factory), {"fields": [1]})
            cached(("changed-sdk-sha", "shared-sha", "manifest-sha", "guid"), factory)
            self.assertEqual(stats(), {"hits": 1, "misses": 2})
        operation()
        operation()
        self.assertEqual(len(calls), 4)

    def test_shared_diagnostic_report_excludes_paths_token_error_and_results(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(CLI, "PROJECT_ROOT", Path(temporary)):
            app = WEB.Application(Path(temporary) / "local/manager")
            jobs = [{"action": "build", "state": "failed", "elapsed_seconds": 2,
                     "error": "PRIVATE_SECRET_PATH", "result": {"token": "PRIVATE_TOKEN"}}]
            value = report(app.snapshot, jobs)
            text = json.dumps(value)
            self.assertNotIn("PRIVATE", text)
            self.assertNotIn(str(Path(temporary)), text)
            self.assertFalse(value["loadable_mod"])
            self.assertFalse(value["real_game_testing_allowed"])
            exported = app.export_diagnostics()
            self.assertTrue(Path(exported["saved_path"]).is_relative_to(Path(temporary) / "local"))
            self.assertEqual(json.loads(Path(exported["saved_path"]).read_text(encoding="utf-8")), exported["report"])


if __name__ == "__main__":
    unittest.main()
