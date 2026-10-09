"""项目 local 内的有界任务日志；重启后不自动重放操作。"""

import copy
import hashlib
from pathlib import Path
import re

from fc27_management import decode_json, json_bytes, plain_path, project_local, read_bytes, replace_bytes


class JobStore:
    def __init__(self, project: Path, manager: Path):
        self.project = project
        key = hashlib.sha256(str(manager).casefold().encode("utf-8")).hexdigest()[:24]
        self.folder = project_local(project / "local/task-history" / key, project, exists=False)

    def load(self):
        jobs, warnings = [], []
        if not self.folder.exists():
            return jobs, warnings
        for path in sorted(self.folder.glob("*.json"))[:200]:
            try:
                if not re.fullmatch(r"[0-9a-f]{16}", path.stem):
                    raise ValueError("任务文件名无效")
                value = decode_json(read_bytes(plain_path(self.folder, path.name), 1024 * 1024))
                if value.get("format") != "fc27-task-v1" or not isinstance(value.get("job"), dict):
                    raise ValueError("任务记录格式无效")
                job = value["job"]
                if (job.get("id") != path.stem or job.get("state") not in ("running", "succeeded", "failed", "interrupted")
                        or not isinstance(job.get("events"), list) or not isinstance(job.get("started_at"), str)
                        or not all(key in job for key in ("action", "label", "subject", "selected", "phase", "finished_at", "elapsed_seconds", "result", "error"))):
                    raise ValueError("任务字段不完整")
                job["historical"] = True
                if job["state"] == "running":
                    job.update(state="interrupted", phase="上次运行中断", error="程序在任务结束前关闭；请查看保留的输出和副本状态，不会自动重试。")
                jobs.append(job)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                warnings.append("历史记录无法读取：" + path.name + " · " + str(exc))
        return sorted(jobs, key=lambda job: job["started_at"])[-50:], warnings

    def save(self, job):
        self.folder = project_local(self.folder, self.project, exists=False)
        self.folder.mkdir(parents=True, exist_ok=True)
        value = copy.deepcopy(job)
        value.pop("historical", None)
        if not re.fullmatch(r"[0-9a-f]{16}", value["id"]):
            raise ValueError("任务标识无效")
        raw = json_bytes({"format": "fc27-task-v1", "job": value})
        if len(raw) > 1024 * 1024:
            value["result"] = {"history_result_omitted": True, "reason": "完整结果超过任务历史上限，请查看对应构建报告"}
            raw = json_bytes({"format": "fc27-task-v1", "job": value})
        replace_bytes(plain_path(self.folder, value["id"] + ".json", exists=False), raw)
        paths = sorted(self.folder.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
        for path in paths[:-50]:
            if re.fullmatch(r"[0-9a-f]{16}", path.stem):
                plain_path(self.folder, path.name).unlink()
