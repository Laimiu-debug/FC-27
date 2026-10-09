"""桌面会话的单实例锁与本机诊断记录；不保存界面令牌。"""

from __future__ import annotations

import hashlib
import json
import msvcrt
import os
from pathlib import Path
import threading

from fc27_management import plain_path, project_local, replace_bytes
from fc27_runtime import VERSION


class DesktopSession:
    def __init__(self, workspace: Path, manager: Path):
        self.folder = project_local(Path("local/desktop-manager"), workspace, exists=False)
        self.folder.mkdir(parents=True, exist_ok=True)
        identity = os.path.normcase(str(manager.resolve())).encode("utf-8")
        self.key = hashlib.sha256(identity).hexdigest()[:20]
        self.path = plain_path(self.folder, self.key + ".json", exists=False)
        self.lock_path = plain_path(self.folder, self.key + ".lock", exists=False)
        self.guard = threading.Lock()
        self.stream = None
        self.value = {"format": "fc27-desktop-session-v1", "version": VERSION,
                      "pid": os.getpid(), "workspace": str(workspace), "manager": str(manager),
                      "state": "starting", "native_loaded": False, "origin": ""}

    def __enter__(self):
        stream = self.lock_path.open("a+b")
        if self.lock_path.stat().st_nlink != 1:
            stream.close()
            raise ValueError("桌面锁不接受硬链接")
        if self.lock_path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            stream.close()
            raise ValueError("此工作区的桌面管理器已经打开，请使用已有窗口") from exc
        self.stream = stream
        try:
            self.update()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def update(self, **changes):
        with self.guard:
            self.value.update(changes)
            replace_bytes(self.path, (json.dumps(self.value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    def __exit__(self, *_):
        if self.stream is not None:
            try:
                self.update(state="stopped")
            finally:
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                self.stream.close()
                self.stream = None
