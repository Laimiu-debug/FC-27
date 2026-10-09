"""线程内阶段通知；不把 UI 或回调带入编译文件格式。"""

from contextlib import contextmanager
from contextvars import ContextVar


_listener = ContextVar("fc27_progress_listener", default=None)
LABELS = {"study": "审查研究资产", "build": "编译修改计划", "package": "生成索引与封装",
          "verify": "重建核对编译包", "loader-stage": "准备加载副本",
          "verify-loader-stage": "重建核对加载副本", "register": "预检并登记候选",
          "preflight": "核对版本与候选", "merge": "合并受绑定的计划"}


@contextmanager
def listen(callback):
    token = _listener.set(callback)
    try:
        yield
    finally:
        _listener.reset(token)


def emit(stage, message="", **counts):
    callback = _listener.get()
    if callback is not None:
        callback({"stage": stage, "message": message or LABELS.get(stage, stage),
                  "counts": counts})
