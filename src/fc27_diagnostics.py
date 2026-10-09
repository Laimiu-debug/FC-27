"""从登记证据汇总离线加载障碍；报告不赋予引擎验证状态。"""

from pathlib import Path
from fc27_management import MAX_JSON, UNVERIFIED, decode_json, plain_path, project_local, read_bytes, relative, sha
from fc27_version import VERSION
from fc27_progress import LABELS


GATES = [
    {"id": "container", "title": "改后封装与签名接受性", "status": "unverified",
     "next": "取得与当前 FC27 封装一致的公开格式依据；保留原始签名区域不等于签名有效。"},
    {"id": "profile", "title": "实际 FC27 加载器映射", "status": "unverified",
     "next": "核对明确支持当前版本的 profile 和写入实现；不照搬前代 profile。"},
    {"id": "fallback", "title": "引擎资源目录与 CAS 回退", "status": "unverified",
     "next": "取得受支持的加载目录和回退机制依据；自有挂载校验只作为离线证据。"},
    {"id": "scope", "title": "独立 Chunk 表及其他超级包", "status": "not_covered",
     "next": "补齐解析范围；当前仅审查明确的 fcgame Bundle 和所选载荷。"},
    {"id": "baseline", "title": "前代同版本原版基线", "status": "missing",
     "next": "在本机提供合法取得的同版本原版基线；当前比较同时包含版本与模组差异。"},
    {"id": "engine", "title": "引擎实际读取候选的证据", "status": "deferred",
     "next": "用户重新允许实机验证后，再独立验证最小诊断候选。"},
    {"id": "gameplay", "title": "比赛 AI 效果", "status": "deferred",
     "next": "实机授权后进行原版与候选的可重复对照；当前没有效果结论。"},
]


def report(snapshot, jobs, project=None):
    """可分享的结构化摘要：不含本机路径、令牌、原始错误或研究载荷。"""
    candidates = []
    for item in snapshot["entries"]:
        record = {"id": item["id"], "assets": item["assets_built"], "edits": item["changed_values"],
                  "bytes": item["changed_bytes"], "evidence": "登记快照，本次诊断未重新预检"}
        if project is not None:
            try:
                detail = snapshot["details"][item["id"]]
                root = project_local(Path(detail["paths"]["stage"]), project)
                raw = read_bytes(plain_path(root, "loader-stage-report.json"), MAX_JSON)
                if sha(raw) != detail["binding"]["report_hashes"]["stage"]:
                    raise ValueError("报告绑定已改变")
                stage = decode_json(raw)
                summary = stage["summary"]
                if any(summary.get(key) is not False for key in ("signature_validity_verified", "profile_mapping_verified", "loadable_mod", "gameplay_effect_verified")):
                    raise ValueError("报告宣称了未经验证能力")
                record["bound_report_read"] = True
                record["recorded_scope"] = {key: summary[key] for key in (
                    "staged_files", "original_cas_fallback_files", "original_cas_full_hashes_verified",
                    "fallback_engine_behavior_verified", "offline_mount_verified")}
                record["headers"] = [{key: impact[key] for key in ("path", "header_bytes", "header_preserved", "payload_changed",
                    "signature_region_nonzero", "signature_region_preserved", "signature_validity_verified")}
                    for impact in stage["header_impacts"]]
                for header in record["headers"]:
                    if not relative(header["path"]).startswith(("Data/", "Patch/")) or header["signature_validity_verified"] is not False:
                        raise ValueError("封装证据路径或状态不合法")
                record["evidence"] = "绑定报告散列通过；本次未重建挂载或验证引擎"
            except (OSError, ValueError, KeyError, TypeError):
                record.pop("headers", None)
                record.pop("recorded_scope", None)
                record.update(bound_report_read=False, evidence="报告缺失、损坏或绑定改变；请先运行离线预检")
        candidates.append(record)
    return {"format": "fc27-offline-diagnostics-v1", "version": VERSION,
            "real_game_testing_allowed": False, "gates": [dict(item) for item in GATES],
            "candidates": candidates,
            "tasks": [{"action": job["action"] if isinstance(job["action"], str) and job["action"] in {
                           "refresh", "init", "setup", "build", "register", "check", "preview", "compose", "rehearse", "status", "restore"} else "unknown",
                       "state": job["state"] if isinstance(job["state"], str) and job["state"] in {
                           "running", "succeeded", "failed", "interrupted"} else "unknown",
                       "stage": job.get("stage", "") if isinstance(job.get("stage", ""), str) and job.get("stage", "") in LABELS else "",
                       "elapsed_seconds": job["elapsed_seconds"] if type(job["elapsed_seconds"]) in (int, float) else None,
                       "error_code": job.get("error_code", "") if job.get("error_code", "") in ("operation_failed", "worker_start_failed") else "",
                       "historical": job.get("historical", False) is True}
                      for job in jobs], **UNVERIFIED}
