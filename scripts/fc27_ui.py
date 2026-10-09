"""启动 FC27 中文本地管理界面；不启动游戏。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import webbrowser

from fc27_build import PROJECT_ROOT
from fc27_web import Application, LocalServer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("local/mod-manager"))
    parser.add_argument("--port", type=int, default=8767, help="本机端口；0 表示自动选择")
    parser.add_argument("--open", action="store_true", help="在默认浏览器打开界面")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口须为 0 至 65535")
    try:
        app = Application(args.root)
        with LocalServer(("127.0.0.1", args.port), app, PROJECT_ROOT / "resources/ui") as server:
            print("FC27 本地管理界面：" + server.origin, flush=True)
            print("仅管理项目内候选与副本。关闭服务前请等待任务完成。", flush=True)
            if args.open:
                webbrowser.open(server.origin)
            try:
                server.serve_forever(poll_interval=0.25)
            except KeyboardInterrupt:
                print("停止接收请求，等待当前管理任务结束。", flush=True)
            finally:
                worker = app.begin_shutdown()
                if worker is not None:
                    worker.join()
        return 0
    except (OSError, ValueError) as exc:
        print("界面启动失败：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
