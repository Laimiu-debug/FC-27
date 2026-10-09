"""FC27 离线管理器桌面入口；可封装为单文件 Windows EXE。"""

from __future__ import annotations

import argparse
import ctypes
import logging
from pathlib import Path
import sys
import threading
import time
import webbrowser


if not getattr(sys, "frozen", False):
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source / "src"))
    sys.path.insert(0, str(source / "scripts"))

from fc27_runtime import VERSION, configure_workspace, discover_workspace, project_root, resource_root


def choose_workspace() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        folder = filedialog.askdirectory(parent=root, title="选择 FC27 研究项目目录（包含 resources 和 local）", mustexist=True)
        return Path(folder) if folder else None
    finally:
        root.destroy()


def show_error(message: str):
    ctypes.windll.user32.MessageBoxW(None, message, "FC27 管理器 · 启动失败", 0x10)


def desktop(app, server, session, smoke: bool) -> bool:
    import webview
    webview.settings.update(ALLOW_DOWNLOADS=False, ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
    window = webview.create_window("FC27 · 玩法模组管理器", server.origin, width=1280, height=860,
                                   min_size=(760, 560), background_color="#0c1512", text_select=True)
    close_guard = threading.Lock()
    waiting = False

    def closing():
        nonlocal waiting
        worker = app.begin_shutdown()
        session.update(state="closing")
        if worker is None or not worker.is_alive():
            return True
        with close_guard:
            if not waiting:
                waiting = True
                window.set_title("FC27 管理器 · 当前任务完成后退出")
                def finish():
                    worker.join()
                    window.destroy()
                threading.Thread(target=finish, name="fc27-finish-before-exit", daemon=True).start()
        return False

    def loaded():
        session.update(native_loaded=True)

    def smoke_check():
        deadline = time.monotonic() + 45
        facts, error = {}, ""
        try:
            if not window.events.loaded.wait(30):
                raise ValueError("桌面页面没有完成加载")
            while time.monotonic() < deadline:
                facts = window.evaluate_js("({title: document.title, connected: document.getElementById('connection-text').textContent, candidates: document.querySelectorAll('.candidate-card').length, exitVisible: !document.getElementById('exit-button').classList.contains('hidden')})")
                if facts and facts.get("connected") == "本机服务已连接" and facts.get("exitVisible"):
                    break
                time.sleep(0.25)
            else:
                raise ValueError("桌面页面未连接本机服务")
        except Exception as exc:
            error = str(exc)
        session.update(smoke_test={"passed": not error, "facts": facts, "error": error})
        window.destroy()

    app.desktop = True
    app.exit_callback = window.destroy
    window.events.closing += closing
    window.events.loaded += loaded
    webview.start(smoke_check if smoke else None, gui="edgechromium", debug=False,
                  private_mode=True, storage_path=str(session.folder / (session.key + "-webview")))
    return not smoke or session.value.get("smoke_test", {}).get("passed", False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="外部研究项目目录；默认从 EXE 所在目录向上查找")
    parser.add_argument("--root", type=Path, default=Path("local/mod-manager"))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--browser", action="store_true", help="以默认浏览器排查界面；页面提供退出按钮")
    parser.add_argument("--smoke-test", action="store_true", help="检查实际桌面页面连接后自动退出")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or (args.browser and args.smoke_test):
        raise ValueError("端口须为 0 至 65535；浏览器排查与桌面检查不能同时启用")
    workspace = args.workspace
    if workspace is None:
        executable = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
        workspace = discover_workspace(executable) or choose_workspace()
    if workspace is None:
        return 0
    configure_workspace(workspace)

    # 必须先绑定外部工作区，再导入各个编译/验证脚本中的路径常量。
    from fc27_desktop_session import DesktopSession
    from fc27_management import plain_path, project_local
    from fc27_web import Application, LocalServer
    app = Application(args.root)
    with DesktopSession(project_root(), app.root) as session:
        log_path = plain_path(session.folder, session.key + ".log", exists=False)
        if log_path.exists():
            plain_path(session.folder, log_path.name)
        project_local(session.folder / (session.key + "-webview"), project_root(), exists=False)
        with log_path.open("a", encoding="utf-8") as log:
            old_stdout, old_stderr = sys.stdout, sys.stderr
            if sys.stdout is None:
                sys.stdout = log
            if sys.stderr is None:
                sys.stderr = log
            log_handler = logging.StreamHandler(log)
            logging.getLogger("pywebview").addHandler(log_handler)
            try:
                with LocalServer(("127.0.0.1", args.port), app, resource_root() / "resources/ui") as server:
                    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
                    thread.start()
                    session.update(state="running", origin=server.origin)
                    try:
                        if args.browser:
                            finished = threading.Event()
                            app.desktop = True
                            app.exit_callback = finished.set
                            webbrowser.open(server.origin)
                            finished.wait()
                            passed = True
                        else:
                            passed = desktop(app, server, session, args.smoke_test)
                    finally:
                        worker = app.begin_shutdown()
                        server.shutdown()
                        thread.join()
                        if worker is not None:
                            worker.join()
                return 0 if passed else 1
            except Exception:
                logging.getLogger("pywebview").exception("桌面管理器启动失败")
                raise
            finally:
                logging.getLogger("pywebview").removeHandler(log_handler)
                sys.stdout, sys.stderr = old_stdout, old_stderr


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        show_error("无法打开管理器：" + str(exc) + "\n\n请确认已选择研究项目目录，并已安装 Microsoft Edge WebView2。")
        raise SystemExit(1)
