"""本机界面的请求边界、串行任务和管理核心适配。"""

import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import fc27_web as WEB


def wait_worker(app):
    app.worker.join(timeout=5)
    if app.worker.is_alive():
        raise AssertionError("界面任务未结束")


class ActionTests(unittest.TestCase):
    def test_unknown_actions_extra_arguments_and_invalid_identifiers_rejected(self):
        for payload in ({"action": "launch"}, {"action": "refresh", "command": "anything"},
                        {"action": "check", "id": "../game"}, {"action": "restore", "run": "CON"},
                        {"action": "build", "id": "ok", "title": "x" * 121, "module": "all"},
                        {"action": "init", "config": "bad\0path"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                WEB.validate_action(payload)

    def test_selection_limits_duplicates_and_request_copy(self):
        for ids in ([], ["one"], ["one", "one"], ["one", "../../bad"], ["item-" + str(i) for i in range(65)]):
            with self.assertRaises(ValueError):
                WEB.validate_action({"action": "preview", "ids": ids})
        original = {"action": "preview", "ids": ["one", "two"]}
        value = WEB.validate_action(original)
        original["ids"].append("three")
        self.assertEqual(value["ids"], ["one", "two"])

    def test_adapter_passes_only_explicit_operations_to_manager(self):
        app = object.__new__(WEB.Application)
        manager = MagicMock()
        manager.check.return_value = ({"stats": {"assets_built": 2, "changed_values": 3, "changed_bytes": 4}},
                                      {"bundle_locations_checked": 10})
        result = app.dispatch(manager, {"action": "check", "id": "one"})
        manager.check.assert_called_once_with("one")
        self.assertTrue(result["preflight_passed"])
        self.assertFalse(result["loadable_mod"])
        app.dispatch(manager, {"action": "build", "id": "one", "title": "中文名称", "module": "all"})
        manager.build.assert_called_once_with("one", "中文名称", "all")
        app.dispatch(manager, {"action": "preview", "ids": ["one", "two"]})
        manager.preview.assert_called_once_with(["one", "two"])
        app.dispatch(manager, {"action": "compose", "id": "three", "title": "合并", "ids": ["one", "two"]})
        manager.compose.assert_called_once_with("three", "合并", ["one", "two"])
        for action, method in (("rehearse", "rehearse"), ("status", "rehearsal_status"), ("restore", "restore")):
            app.dispatch(manager, {"action": action, "id": "one", "run": "trial"})
            getattr(manager, method).assert_called_once_with(*(["one", "trial"] if action == "rehearse" else ["trial"]))
        payload = {"action": "register", "id": "one", "title": "登记", **{k: "local/" + k for k in WEB.CLI.PATH_KEYS}}
        app.dispatch(manager, payload)
        manager.register.assert_called_once_with("one", "登记", {k: Path(payload[k]) for k in WEB.CLI.PATH_KEYS})
        self.assertTrue(app.dispatch(manager, {"action": "refresh"})["refreshed"])
        with self.assertRaises(ValueError):
            app.dispatch(manager, {"action": "launch"})


class WorkerTests(unittest.TestCase):
    def context(self, root):
        app = WEB.Application(root / "local/manager")
        app.root.mkdir(parents=True)
        fake = MagicMock()
        fake.root = app.root
        snapshot = app.empty_snapshot()
        snapshot["workspace"].update(ready=True, modules=[{"id": "all", "title": "整场比赛"}])
        app.snapshot = copy.deepcopy(snapshot)
        return app, fake, snapshot

    def test_only_one_task_runs_and_state_reads_remain_responsive(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(WEB.CLI, "PROJECT_ROOT", Path(directory)):
            app, fake, snapshot = self.context(Path(directory))
            entered, release = threading.Event(), threading.Event()
            def blocked(_):
                entered.set()
                release.wait(4)
                return ({"stats": {"assets_built": 1, "changed_values": 1, "changed_bytes": 1}},
                        {"bundle_locations_checked": 2})
            fake.check.side_effect = blocked
            with patch.object(WEB.CLI, "Manager", return_value=fake), patch.object(app, "snapshot_for", return_value=snapshot):
                accepted = app.submit({"action": "check", "id": "one"})
                try:
                    self.assertTrue(entered.wait(2))
                    started = time.monotonic()
                    state = app.state()
                    self.assertLess(time.monotonic() - started, 0.5)
                    self.assertTrue(state["busy"])
                    self.assertEqual(state["jobs"][0]["subject"], "one")
                    with self.assertRaises(WEB.BusyError):
                        app.submit({"action": "check", "id": "two"})
                finally:
                    release.set()
                    wait_worker(app)
            state = app.state()
            self.assertFalse(state["busy"])
            self.assertEqual(state["jobs"][0]["id"], accepted["job_id"])
            self.assertEqual(state["jobs"][0]["state"], "succeeded")
            self.assertTrue(state["jobs"][0]["result"]["preflight_passed"])
            state["jobs"].clear()
            self.assertEqual(len(app.state()["jobs"]), 1)

    def test_error_is_visible_and_following_operation_can_run(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(WEB.CLI, "PROJECT_ROOT", Path(directory)):
            app, fake, snapshot = self.context(Path(directory))
            fake.restore.side_effect = ValueError("备份被修改，停止恢复")
            with patch.object(WEB.CLI, "Manager", return_value=fake), patch.object(app, "snapshot_for", return_value=snapshot), \
                    patch.object(app, "read_snapshot", return_value=snapshot):
                app.submit({"action": "restore", "run": "trial"})
                wait_worker(app)
                self.assertEqual(app.state()["jobs"][-1]["state"], "failed")
                self.assertIn("备份被修改", app.state()["jobs"][-1]["error"])
                app.submit({"action": "refresh"})
                wait_worker(app)
                self.assertEqual(app.state()["jobs"][-1]["state"], "succeeded")

    def test_module_whitelist_and_existing_directory_init_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(WEB.CLI, "PROJECT_ROOT", Path(directory)):
            app, _, _ = self.context(Path(directory))
            with self.assertRaises(ValueError):
                app.submit({"action": "build", "id": "one", "title": "实验", "module": "unknown"})
            with self.assertRaises(ValueError):
                app.submit({"action": "init", "config": "local/config.json"})
            self.assertFalse(app.state()["busy"])

    def test_ui_root_cannot_escape_project_local(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(WEB.CLI, "PROJECT_ROOT", Path(directory)):
            with self.assertRaises(ValueError):
                WEB.Application(Path(directory) / "outside")
            self.assertFalse((Path(directory) / "outside").exists())

    def test_shutdown_refuses_new_jobs_but_allows_existing_job_to_finish(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(WEB.CLI, "PROJECT_ROOT", Path(directory)):
            app, fake, snapshot = self.context(Path(directory))
            entered, release = threading.Event(), threading.Event()
            def blocked(*_):
                entered.set()
                release.wait(4)
                return {"refreshed": True}
            with patch.object(WEB.CLI, "Manager", return_value=fake), patch.object(app, "snapshot_for", return_value=snapshot), \
                    patch.object(app, "dispatch", side_effect=blocked):
                app.submit({"action": "refresh"})
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertIs(app.begin_shutdown(), app.worker)
                    self.assertTrue(app.state()["closing"])
                    self.assertTrue(app.worker.is_alive())
                    with self.assertRaises(WEB.BusyError):
                        app.submit({"action": "refresh"})
                finally:
                    release.set()
                    wait_worker(app)
                self.assertEqual(app.state()["jobs"][-1]["state"], "succeeded")
                with self.assertRaises(WEB.BusyError):
                    app.submit({"action": "refresh"})


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        self.root_patch = patch.object(WEB.CLI, "PROJECT_ROOT", self.project)
        self.root_patch.start()
        self.app = WEB.Application(self.project / "local/manager")
        self.server = WEB.LocalServer(("127.0.0.1", 0), self.app, ROOT / "resources/ui")
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        if self.app.worker:
            wait_worker(self.app)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.root_patch.stop()
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def post(self, payload, extra=None):
        headers = {"Content-Type": "application/json", "X-FC27-Token": self.app.token,
                   "Origin": self.server.origin}
        headers.update(extra or {})
        return self.request("POST", "/api/jobs", json.dumps(payload).encode(), headers)

    def test_static_assets_headers_and_state_are_available_without_file_serving(self):
        for path in WEB.ASSETS:
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, _, raw = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        value = json.loads(raw)
        self.assertFalse(value["workspace"]["ready"])
        self.assertEqual(value["token"], self.app.token)
        for path in ("/../../AGENTS.md", "/local/manager.json", "/api/launch"):
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_head_returns_headers_without_body(self):
        status, headers, body = self.request("HEAD", "/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_foreign_host_origin_and_invalid_tokens_are_rejected(self):
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.post({"action": "refresh"}, {"Origin": "https://evil.example"})[0], 403)
        for token in ("", "wrong", "\xe9"):
            self.assertEqual(self.post({"action": "refresh"}, {"X-FC27-Token": token})[0], 403)
        self.assertEqual(self.app.state()["jobs"], [])

    def test_non_json_unknown_action_extra_keys_and_oversize_rejected(self):
        self.assertEqual(self.post({"action": "refresh"}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post({"action": "launch"})[0], 400)
        self.assertEqual(self.post({"action": "refresh", "game_root": "elsewhere"})[0], 400)
        self.assertEqual(self.post({"action": "refresh"}, {"Content-Length": "32769"})[0], 400)
        self.assertEqual(self.app.state()["jobs"], [])

    def test_duplicate_json_keys_rejected_before_job_submission(self):
        headers = {"Content-Type": "application/json", "X-FC27-Token": self.app.token}
        self.assertEqual(self.request("POST", "/api/jobs", b'{"action":"refresh","action":"init"}', headers)[0], 400)
        self.assertEqual(self.app.state()["jobs"], [])

    def test_async_failure_is_reported_and_endpoint_remains_available(self):
        status, _, raw = self.post({"action": "refresh"})
        self.assertEqual(status, 202)
        accepted = json.loads(raw)
        wait_worker(self.app)
        state = json.loads(self.request("GET", "/api/state")[2])
        self.assertFalse(state["busy"])
        self.assertEqual(state["jobs"][-1]["id"], accepted["job_id"])
        self.assertEqual(state["jobs"][-1]["state"], "failed")
        self.assertTrue(state["jobs"][-1]["error"])

    def test_network_listener_cannot_bind_other_interfaces(self):
        with self.assertRaises(ValueError):
            WEB.LocalServer(("0.0.0.0", 0), self.app, ROOT / "resources/ui")

    def test_diagnostic_export_requires_session_token_and_has_no_private_workspace_fields(self):
        headers = {"Content-Type": "application/json", "X-FC27-Token": self.app.token,
                   "Origin": self.server.origin}
        self.assertEqual(self.request("POST", "/api/diagnostics", b"{}", {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.request("POST", "/api/diagnostics", b'{"path":"elsewhere"}', headers)[0], 400)
        status, _, raw = self.request("POST", "/api/diagnostics", b"{}", headers)
        self.assertEqual(status, 200)
        value = json.loads(raw)
        self.assertNotIn(self.app.token, json.dumps(value["report"]))
        self.assertNotIn(str(self.app.root), json.dumps(value["report"]))
        self.assertFalse(value["report"]["loadable_mod"])
        self.assertTrue(Path(value["saved_path"]).is_file())

    def exit_request(self, body=b"{}", extra=None):
        headers = {"Content-Type": "application/json", "X-FC27-Token": self.app.token,
                   "Origin": self.server.origin}
        headers.update(extra or {})
        return self.request("POST", "/api/exit", body, headers)

    def test_exit_requires_desktop_host_and_empty_payload(self):
        self.assertEqual(self.exit_request()[0], 400)
        callback = threading.Event()
        self.app.desktop, self.app.exit_callback = True, callback.set
        self.assertEqual(self.exit_request(b'{"command":"launch"}')[0], 400)
        self.assertFalse(self.app.state()["closing"])
        self.assertEqual(self.exit_request()[0], 202)
        self.assertTrue(callback.wait(1))
        self.assertTrue(self.app.state()["closing"])
        self.assertEqual(self.post({"action": "refresh"})[0], 409)

    def test_exit_retains_host_origin_token_and_json_checks(self):
        callback = threading.Event()
        self.app.desktop, self.app.exit_callback = True, callback.set
        for extra, status in (({"Origin": "https://evil.example"}, 403),
                              ({"Host": "evil.example"}, 403),
                              ({"X-FC27-Token": "wrong"}, 403),
                              ({"Content-Type": "text/plain"}, 415)):
            self.assertEqual(self.exit_request(extra=extra)[0], status)
        self.assertFalse(callback.is_set())
        self.assertFalse(self.app.state()["closing"])


if __name__ == "__main__":
    unittest.main()
