"""本机 Web 界面适配层；操作仅转发给离线管理核心。"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import threading
import time
from urllib.parse import urlsplit

import fc27_manager as CLI
from fc27_management import (MAX_JSON, UNVERIFIED, decode_json, identifier, json_bytes,
                             operation_lock, plain_path, project_local, read_bytes)
from fc27_study import recipe_input, validate_recipe
from fc27_progress import listen
from fc27_jobs import JobStore
from fc27_setup import DEFAULTS, FIELDS, configure, detect_games, readiness
from fc27_diagnostics import GATES, report as diagnostic_report
from fc27_version import VERSION
from fc27_schema_cache import scoped
from fc27_player import snapshot as player_snapshot, choose_game
from fc27_presets import prepare as prepare_preset


ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/pitch.svg": ("pitch.svg", "image/svg+xml")}
LABELS = {"refresh": "刷新工作区", "init": "初始化工作区", "register": "登记候选",
          "check": "离线预检", "build": "构建候选", "preview": "检查合并冲突",
          "compose": "合并重编译", "rehearse": "创建并应用副本", "status": "核对副本状态",
          "restore": "还原副本", "setup": "配置研究工作区",
          "player-game": "识别游戏版本", "player-prepare": "自动准备内置方案"}
RULES = {"refresh": set(), "init": {"config"}, "register": {"id", "title", "bundle", "export", "package", "stage"},
         "check": {"id"}, "build": {"id", "title", "module"}, "preview": {"ids"},
         "compose": {"id", "title", "ids"}, "rehearse": {"id", "run"},
         "status": {"run"}, "restore": {"run"}, "setup": set(FIELDS),
         "player-game": {"game_root"}, "player-prepare": {"module"}}
PHASES = {"refresh": "读取登记快照", "init": "创建项目内管理目录", "register": "核对候选并登记",
          "check": "重建检查版本、索引和载荷", "build": "研究、编译、打包并核对副本",
          "preview": "预检候选并比较修改计划", "compose": "合并计划、重编译并核对副本",
          "rehearse": "预检、备份并应用到项目副本", "status": "核对日志、备份和副本散列",
          "restore": "核对备份并恢复项目副本", "setup": "保存本机配置并检查研究依赖",
          "player-game": "只读核对游戏索引指纹", "player-prepare": "从本机原版自动生成预设副本"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_action(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("action") not in RULES:
        raise ValueError("不支持的界面操作")
    action = payload["action"]
    if set(payload) != RULES[action] | {"action"}:
        raise ValueError("操作参数缺失或包含未知参数")
    for key in RULES[action] - {"ids"}:
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 2048 or any(ord(c) < 32 for c in value):
            raise ValueError("操作参数必须是有效文字")
    for key in ("id", "run"):
        if key in payload:
            identifier(payload[key])
    if "title" in payload and len(payload["title"]) > 120:
        raise ValueError("候选名称最多 120 个字符")
    if "ids" in payload:
        ids = payload["ids"]
        if not isinstance(ids, list) or not 2 <= len(ids) <= 64:
            raise ValueError("请选择 2 至 64 个候选")
        for value in ids:
            identifier(value)
        if len(set(ids)) != len(ids):
            raise ValueError("不能重复选择同一候选")
    return copy.deepcopy(payload)


class BusyError(ValueError):
    pass


class Application:
    def __init__(self, root: Path):
        self.root = project_local(root, CLI.PROJECT_ROOT, exists=False)
        self.token = secrets.token_urlsafe(32)
        self.guard = threading.Lock()
        self.jobs: list[dict] = []
        self.store = JobStore(CLI.PROJECT_ROOT, self.root)
        self.history_warnings = []
        try:
            self.jobs, self.history_warnings = self.store.load()
        except Exception as exc:
            self.history_warnings = ["任务历史暂不可读取：" + str(exc)]
        self.detected_games = detect_games()
        self.busy = False
        self.closing = False
        self.desktop = False
        self.exit_callback = None
        self.worker: threading.Thread | None = None
        self.snapshot = self.read_snapshot()

    def empty_snapshot(self) -> dict:
        hint = CLI.PROJECT_ROOT / "local/research/2026-10-08/notes/pipeline-config-v1.json"
        return {"workspace": {"ready": False, "root": str(self.root), "config": "", "game_root": "",
                              "config_hint": hint.relative_to(CLI.PROJECT_ROOT).as_posix() if hint.is_file() else "local/pipeline-config.json",
                              "error": "", "build_error": "", "modules": [], "snapshot_at": now(),
                              "setup_values": {**DEFAULTS, "game_root": next(iter(self.detected_games), "")},
                              "detected_games": self.detected_games, "readiness": None},
                "entries": [], "details": {}, "rehearsals": [], "incomplete_builds": [],
                "warnings": list(self.history_warnings), "gates": GATES, "version": VERSION,
                "player": player_snapshot(CLI.PROJECT_ROOT, self.detected_games), "safety": dict(UNVERIFIED)}

    def snapshot_for(self, manager: CLI.Manager) -> dict:
        result = self.empty_snapshot()
        result["workspace"].update(ready=True, config=str(manager.config_path), game_root=str(manager.game))
        result["player"] = player_snapshot(CLI.PROJECT_ROOT, self.detected_games, str(manager.game))
        result["entries"] = manager.listing()["entries"]
        for entry in result["entries"]:
            detail, _ = manager.load_entry(entry["id"])
            result["details"][entry["id"]] = detail
        try:
            config = decode_json(read_bytes(manager.config_path, MAX_JSON))
            result["workspace"]["setup_values"] = config
            result["workspace"]["readiness"] = readiness(CLI.PROJECT_ROOT, config)
            recipe = Path(config["recipe"])
            if not recipe.is_absolute():
                recipe = CLI.PROJECT_ROOT / recipe
            recipe = decode_json(read_bytes(recipe_input(recipe), MAX_JSON))
            validate_recipe(recipe)
            result["workspace"]["modules"] = [{"id": "all", "title": "整场比赛 · 全部六组"}] + [
                {"id": m["id"], "title": m["title"]} for m in recipe["modules"]]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result["workspace"]["build_error"] = "研究方案无法读取：" + str(exc)
        builds = manager.local(manager.root / "builds")
        registered = {entry["id"] for entry in result["entries"]}
        for path in sorted(builds.iterdir())[-50:]:
            if path.name not in registered:
                manager.local(path)
                item = {"id": path.name, "path": str(path), "state": "未登记的输出，已保留"}
                try:
                    record = decode_json(read_bytes(plain_path(path, "pipeline-report.json"), MAX_JSON))
                    item.update(state="离线流水线完成，登记未完成" if record.get("completed") else "流水线失败",
                                stage=record.get("failed_stage", record.get("completed_stage", "")))
                except (OSError, ValueError, TypeError):
                    pass
                result["incomplete_builds"].append(item)
        folder = manager.local(manager.root / "rehearsals")
        for path in sorted(folder.iterdir()):
            try:
                run_id = identifier(path.name)
                run = manager.rehearsal_path(run_id)
                record = decode_json(read_bytes(plain_path(run, "transaction.json"), MAX_JSON))
                if (record["format"] != "fc27-local-rehearsal-v1" or record["id"] != run_id or
                        record["safety"] != UNVERIFIED or record["state"] not in
                        ("prepared", "applying", "applied", "restoring", "restored")):
                    raise ValueError("事务快照格式不同")
                result["rehearsals"].append({"id": run_id, "entry_id": record["entry_id"], "state": record["state"],
                    "baseline_files": len(record["baseline"]), "applied_files": len(record["applied"]),
                    "modified_files": len(record["changes"]), "path": str(run), "live_verification_performed": False})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                result["warnings"].append("副本记录 " + path.name + " 无法读取：" + str(exc))
        return result

    def read_snapshot(self) -> dict:
        result = self.empty_snapshot()
        if not self.root.exists():
            return result
        try:
            manager = CLI.Manager(self.root)
            with operation_lock(manager.root):
                return self.snapshot_for(manager)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result["workspace"]["error"] = str(exc)
            return result

    def state(self) -> dict:
        with self.guard:
            return copy.deepcopy({**self.snapshot, "token": self.token, "busy": self.busy,
                                  "closing": self.closing, "desktop": self.desktop, "jobs": self.jobs})

    def begin_shutdown(self) -> threading.Thread | None:
        with self.guard:
            self.closing = True
            return self.worker

    def submit(self, payload: dict) -> dict:
        payload = validate_action(payload)
        with self.guard:
            if self.closing:
                raise BusyError("管理器正在退出，不再接收新任务")
            if self.busy:
                raise BusyError("已有任务正在执行，请等待完成")
            if payload["action"] == "build":
                workspace = self.snapshot["workspace"]
                if workspace["build_error"]:
                    raise ValueError(workspace["build_error"])
                if payload["module"] not in {m["id"] for m in workspace["modules"]}:
                    raise ValueError("所选模块不在当前研究方案中")
                if workspace.get("readiness") and not workspace["readiness"]["inputs_present"]:
                    raise ValueError("研究依赖尚未齐备，请在工作区设置中查看缺失项目")
            if payload["action"] == "player-prepare":
                player = self.snapshot["player"]
                if not player["can_prepare"]:
                    raise ValueError(player["error"] or "内置方案不能用于当前游戏版本")
                if payload["module"] not in {m["id"] for m in player["modules"]}:
                    raise ValueError("所选方案不在内置预设中")
            if payload["action"] in ("build", "compose", "rehearse", "player-prepare"):
                if shutil.disk_usage(CLI.PROJECT_ROOT).free < 512 * 1024 * 1024:
                    raise ValueError("工作区可用空间不足 512 MiB；请先释放空间，再生成候选或副本")
            if payload["action"] == "init" and self.root.exists():
                raise ValueError("管理目录已经存在，不能重新初始化")
            job = {"id": secrets.token_hex(8), "action": payload["action"], "label": LABELS[payload["action"]],
                   "subject": payload.get("id", payload.get("run", "")), "selected": payload.get("ids", []),
                   "state": "running", "phase": PHASES[payload["action"]], "started_at": now(),
                   "finished_at": None, "elapsed_seconds": None, "result": None, "error": "",
                   "events": [{"time": now(), "message": "任务开始：" + PHASES[payload["action"]]}]}
            self.jobs = (self.jobs + [job])[-50:]
            self.persist(job)
            self.busy = True
            self.worker = threading.Thread(target=self.execute, args=(payload, job), name="fc27-ui-operation", daemon=False)
            try:
                self.worker.start()
            except Exception:
                self.busy = False
                self.worker = None
                job.update(state="failed", phase="无法启动任务", finished_at=now(), error="后台线程启动失败", error_code="worker_start_failed")
                self.persist(job)
                raise
            return {"accepted": True, "job_id": job["id"]}

    def dispatch(self, manager: CLI.Manager, payload: dict) -> dict:
        action = payload["action"]
        if action == "refresh":
            return {"refreshed": True, **UNVERIFIED}
        if action == "check":
            entry, checked = manager.check(payload["id"])
            return {"id": payload["id"], "preflight_passed": True, **entry["stats"],
                    "bundle_locations_checked": checked["bundle_locations_checked"], **UNVERIFIED}
        if action == "register":
            return manager.register(payload["id"], payload["title"], {k: Path(payload[k]) for k in CLI.PATH_KEYS})
        if action == "build":
            return manager.build(payload["id"], payload["title"], payload["module"])
        if action == "preview":
            return manager.preview(payload["ids"])
        if action == "compose":
            return manager.compose(payload["id"], payload["title"], payload["ids"])
        if action == "rehearse":
            return manager.rehearse(payload["id"], payload["run"])
        if action == "status":
            return manager.rehearsal_status(payload["run"])
        if action == "restore":
            return manager.restore(payload["run"])
        raise ValueError("操作没有管理核心适配")

    @scoped
    def execute(self, payload: dict, job: dict):
        started = time.monotonic()
        result, error, snapshot = None, "", None
        try:
            def update(event):
                with self.guard:
                    job.update(phase=event["message"], stage=event["stage"], counts=event["counts"])
                    job["events"] = (job["events"] + [{"time": now(), **event}])[-100:]
                    self.persist(job)
            with listen(update):
                if payload["action"] == "player-game":
                    result = choose_game(CLI.PROJECT_ROOT, payload["game_root"])
                    snapshot = self.read_snapshot()
                elif payload["action"] == "player-prepare":
                    result = prepare_preset(CLI.PROJECT_ROOT, Path(self.snapshot["player"]["game_root"]), payload["module"])
                    snapshot = self.read_snapshot()
                elif payload["action"] == "refresh" and not self.root.exists():
                    result = {"refreshed": True, **UNVERIFIED}
                    snapshot = self.read_snapshot()
                elif payload["action"] == "init":
                    result = CLI.initialize(self.root, Path(payload["config"]))
                    snapshot = self.read_snapshot()
                elif payload["action"] == "setup":
                    if self.root.exists():
                        with operation_lock(self.root):
                            result = configure(CLI.PROJECT_ROOT, self.root, payload, CLI.initialize)
                    else:
                        result = configure(CLI.PROJECT_ROOT, self.root, payload, CLI.initialize)
                    snapshot = self.read_snapshot()
                else:
                    manager = CLI.Manager(self.root)
                    with operation_lock(manager.root):
                        result = self.dispatch(manager, payload)
                        snapshot = self.snapshot_for(manager)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        finally:
            if snapshot is None:
                try:
                    snapshot = self.read_snapshot()
                except Exception as exc:
                    error = (error + "；状态刷新失败：" + str(exc)).strip("；")
            with self.guard:
                if snapshot is not None:
                    self.snapshot = snapshot
                job.update(state="failed" if error else "succeeded", phase="操作失败" if error else "操作完成",
                           finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 2),
                           result=result, error=error[:2000], error_code="operation_failed" if error else "",
                           advice="查看工作区依赖与保留输出；构建重试使用新标识，恢复失败先核对备份和当前副本。" if error else "")
                job["events"].append({"time": now(), "message": "失败：" + error[:2000] if error else "操作完成，游戏加载与比赛效果仍未验证"})
                self.busy = False
                self.persist(job)

    def persist(self, job):
        try:
            self.store.save(job)
        except Exception as exc:
            message = "任务历史未保存：" + str(exc)
            if message not in self.history_warnings:
                self.history_warnings = (self.history_warnings + [message])[-10:]
            job["history_error"] = "任务记录未能写入；当前操作状态仍可查看"

    def diagnostics(self):
        with self.guard:
            snapshot, jobs = copy.deepcopy(self.snapshot), copy.deepcopy(self.jobs)
        return diagnostic_report(snapshot, jobs, CLI.PROJECT_ROOT)

    def export_diagnostics(self):
        value = self.diagnostics()
        path = project_local(CLI.PROJECT_ROOT / "local/diagnostics" / ("diagnostics-" + secrets.token_hex(8) + ".json"),
                             CLI.PROJECT_ROOT, exists=False)
        from fc27_management import write_new
        write_new(path, json_bytes(value))
        return {"report": value, "saved_path": str(path)}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: Application, assets: Path):
        if address[0] != "127.0.0.1":
            raise ValueError("界面服务只能监听本机 127.0.0.1")
        self.app, self.assets = app, assets
        super().__init__(address, Handler)
        self.origin = "http://127.0.0.1:" + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    server: LocalServer

    def log_message(self, *_):
        pass

    def allowed(self) -> bool:
        if (self.client_address[0] != "127.0.0.1" or self.headers.get("Host") != self.server.origin.removeprefix("http://") or
                (self.headers.get("Origin") is not None and self.headers["Origin"] != self.server.origin)):
            self.reply(403, {"error": "界面只接受同源本机请求"})
            return False
        return True

    def reply(self, status: int, value, mime: str = "application/json; charset=utf-8", head: bool = False):
        body = value if isinstance(value, bytes) else json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self):
        self.get()

    def do_HEAD(self):
        self.get(head=True)

    def get(self, head: bool = False):
        if not self.allowed():
            return
        path = urlsplit(self.path).path
        if path == "/api/state":
            self.reply(200, self.server.app.state(), head=head)
        elif path in ASSETS:
            name, mime = ASSETS[path]
            self.reply(200, read_bytes(plain_path(self.server.assets, name), MAX_JSON), mime, head)
        else:
            self.reply(404, {"error": "页面不存在"}, head=head)

    def do_POST(self):
        if not self.allowed():
            return
        if self.path not in ("/api/jobs", "/api/exit", "/api/diagnostics"):
            self.reply(404, {"error": "操作接口不存在"})
            return
        token = self.headers.get("X-FC27-Token", "")
        if not hmac.compare_digest(token.encode("utf-8"), self.server.app.token.encode("ascii")):
            self.reply(403, {"error": "界面会话已失效，请刷新页面"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.reply(415, {"error": "操作必须使用 JSON"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 32768 or self.headers.get("Transfer-Encoding"):
                raise ValueError("操作内容大小无效")
            payload = decode_json(self.rfile.read(length))
            if self.path == "/api/diagnostics":
                if payload != {}:
                    raise ValueError("诊断导出不接受额外参数")
                self.reply(200, self.server.app.export_diagnostics())
                return
            if self.path == "/api/exit":
                if payload != {} or not self.server.app.desktop or self.server.app.exit_callback is None:
                    raise ValueError("当前界面不支持桌面退出请求")
                self.server.app.begin_shutdown()
                self.reply(202, {"accepted": True})
                threading.Thread(target=self.server.app.exit_callback, name="fc27-desktop-exit", daemon=True).start()
                return
            result = self.server.app.submit(payload)
            self.reply(202, result)
        except BusyError as exc:
            self.reply(409, {"error": str(exc)})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.reply(400, {"error": str(exc)})
