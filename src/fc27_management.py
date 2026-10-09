"""模组登记、计划合并与项目内文件事务；不安装到游戏。"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import uuid


MAX_FILES = 10_000
MAX_BYTES = 160 * 1024 * 1024
MAX_JSON = 8 * 1024 * 1024
PLAN_KEYS = {"format", "export_manifest_sha256", "sdk_sha256", "shared_types_sha256", "assets"}
UNVERIFIED = {"loadable_mod": False, "loader_compatibility_verified": False,
              "gameplay_effect_verified": False, "game_started_by_tool": False,
              "game_files_written": False, "filesystem_links_created": False}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def manifest_sha(value: dict) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8"))


def identifier(value: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", value) or
            re.fullmatch(r"con|prn|aux|nul|com[1-9]|lpt[1-9]", value)):
        raise ValueError("标识须为 1 至 48 位小写字母、数字或连字符，且不使用 Windows 保留名称")
    return value


def relative(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("管理路径必须是字符串")
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or path.as_posix() != value or ".." in path.parts or
            any(re.search(r'[\\:<>"|?*\x00-\x1f]', p) or p.endswith((".", " ")) or
                re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p)
                for p in path.parts)):
        raise ValueError("管理路径不是规范、安全的相对路径")
    return value


def plain_path(root: Path, name: str, exists: bool = True) -> Path:
    relative(name)
    current = root
    if current.is_symlink() or current.is_junction():
        raise ValueError("管理目录不接受链接")
    for part in PurePosixPath(name).parts:
        current /= part
        if current.is_symlink() or current.is_junction():
            raise ValueError("管理文件或目录不接受链接")
    result = current.resolve(strict=exists)
    if not result.is_relative_to(root.resolve()):
        raise ValueError("管理路径解析后越界")
    if exists and not result.is_file():
        raise ValueError("管理输入不是普通文件")
    if exists and result.stat().st_nlink != 1:
        raise ValueError("管理文件不接受硬链接")
    return result


def project_local(path: Path, project: Path, game: Path | None = None, exists: bool = True) -> Path:
    path = Path(path)
    if not path.is_absolute():
        path = project / path
    local_root = project.resolve() / "local"
    if local_root.is_symlink() or local_root.is_junction():
        raise ValueError("项目 local 不接受链接")
    local = local_root.resolve()
    # 先检查词法路径的所有祖先，再比较规范路径；Windows 8.3 别名
    # 与长路径可能指向同一目录，不能把未解析别名与规范 local 作比较。
    lexical = Path(os.path.abspath(path))
    for current in (lexical, *lexical.parents):
        if current.is_symlink() or current.is_junction():
            raise ValueError("管理输入和输出不接受链接")
    resolved = lexical.resolve(strict=exists)
    if resolved == local or not resolved.is_relative_to(local) or (game and resolved.is_relative_to(game.resolve())):
        raise ValueError("管理路径越界或位于游戏目录")
    return resolved


def read_bytes(path: Path, limit: int = MAX_BYTES) -> bytes:
    if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > limit:
        raise ValueError("管理文件不是独立普通文件或超过读取上限")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("管理文件读取期间超过上限")
    return raw


def decode_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("管理 JSON 有重复键")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("管理 JSON 不接受非有限常量")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def write_new(path: Path, raw: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def replace_bytes(path: Path, raw: bytes):
    """只用于已受边界验证且归管理器所有的文件。"""
    if path.exists() and (path.is_symlink() or path.is_junction() or path.stat().st_nlink != 1):
        raise ValueError("事务目标不接受链接")
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        write_new(temp, raw)
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def operation_lock(root: Path):
    """操作系统文件锁，进程退出后释放；不按超时抢占其他操作。"""
    path = plain_path(root, ".operation-lock", exists=False)
    if path.exists() and path.stat().st_nlink != 1:
        raise ValueError("操作锁不接受硬链接")
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("已有管理操作正在运行，请稍后重试") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def merge_plans(plans: list[dict]) -> dict:
    if not plans or len(plans) > 64:
        raise ValueError("合并须选择 1 至 64 份已验证计划")
    combined, assets = copy.deepcopy(plans[0]), {}
    for plan in plans:
        if (not isinstance(plan, dict) or set(plan) != PLAN_KEYS or
                plan["format"] != "fc27-fixed-edit-plan-v1" or not isinstance(plan["assets"], list)):
            raise ValueError("合并输入不是定点编辑计划")
        if any(plan[key] != combined[key] for key in PLAN_KEYS - {"assets"}):
            raise ValueError("合并计划的导出、SDK 或共享类型基线不同")
        seen_assets = set()
        for asset in plan["assets"]:
            if (not isinstance(asset, dict) or set(asset) != {"name", "expected_sha256", "root_identity", "edits"} or
                    not isinstance(asset["edits"], list)):
                raise ValueError("合并资产计划格式不同")
            name = relative(asset["name"])
            if name in seen_assets:
                raise ValueError("同一计划有重复资产")
            seen_assets.add(name)
            if name not in assets:
                assets[name] = {**copy.deepcopy(asset), "edits": {}}
            target = assets[name]
            if any(target[key] != asset[key] for key in ("expected_sha256", "root_identity")):
                raise ValueError("同名资产的原始内容或完整类型身份不同")
            seen = set()
            for edit in asset["edits"]:
                key = edit["field"]
                if not isinstance(key, str) or key in seen:
                    raise ValueError("同一计划有重复或无效字段")
                seen.add(key)
                if key in target["edits"] and target["edits"][key] != edit:
                    raise ValueError(f"所选模组对同一字段给出不同修改：{name} / {key}")
                target["edits"][key] = copy.deepcopy(edit)
    if len(assets) > 64 or sum(len(a["edits"]) for a in assets.values()) > 8192:
        raise ValueError("合并计划超过定点构建上限")
    combined["assets"] = [{**asset, "edits": [asset["edits"][key] for key in sorted(asset["edits"])]}
                          for name, asset in sorted(assets.items())]
    return combined


def snapshot_tree(root: Path) -> dict[str, dict]:
    result, folded, total = {}, set(), 0
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise ValueError("演练目录不接受链接")
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise ValueError("演练目录不接受链接")
        if not path.is_file():
            continue
        name = relative(path.relative_to(root).as_posix())
        if name.casefold() in folded:
            raise ValueError("演练文件有大小写冲突")
        folded.add(name.casefold())
        raw = read_bytes(plain_path(root, name))
        total += len(raw)
        if total > MAX_BYTES or len(result) >= MAX_FILES:
            raise ValueError("演练文件集超过大小或数量上限")
        result[name] = {"bytes": len(raw), "sha256": sha(raw)}
    return result


def inspect_rehearsal(run: Path) -> dict:
    record_path = plain_path(run, "transaction.json")
    record = decode_json(read_bytes(record_path, MAX_JSON))
    keys = {"format", "id", "entry_sha256", "entry_id", "baseline", "applied", "changes", "state", "safety"}
    if (not isinstance(record, dict) or set(record) != keys or
            record["format"] != "fc27-local-rehearsal-v1" or identifier(record["id"]) != run.name or
            record["state"] not in ("prepared", "applying", "applied", "restoring", "restored") or
            record["safety"] != UNVERIFIED):
        raise ValueError("演练事务记录格式不同或宣称了未验证能力")
    before, after, changes = record["baseline"], record["applied"], record["changes"]
    if not isinstance(changes, list) or not 1 <= len(changes) <= 128:
        raise ValueError("演练事务没有有效修改清单")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("演练事务缺少文件集")
    changed = {c["path"] for c in changes}
    if len(changed) != len(changes) or not changed.issubset(after):
        raise ValueError("演练修改路径重复或缺失")
    expected_after = dict(before)
    for change in changes:
        name = relative(change["path"])
        if not name.startswith(("Data/", "Patch/")) or set(change) != {"path", "before", "after"}:
            raise ValueError("演练修改路径或字段无效")
        if change["before"] != before.get(name) or change["after"] != after[name]:
            raise ValueError("演练修改与原始/候选清单不同")
        expected_after[name] = change["after"]
    if after != expected_after:
        raise ValueError("演练候选清单有计划外修改")
    for manifest in (before, after):
        if len(manifest) > MAX_FILES or len({name.casefold() for name in manifest}) != len(manifest):
            raise ValueError("演练事务超过文件数上限")
        for name, item in manifest.items():
            relative(name)
            if (not name.startswith(("Data/", "Patch/")) or not isinstance(item, dict) or
                    set(item) != {"bytes", "sha256"} or type(item["bytes"]) is not int or item["bytes"] < 0 or
                    not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
                raise ValueError("演练文件清单无效")
        if sum(item["bytes"] for item in manifest.values()) > MAX_BYTES:
            raise ValueError("演练事务超过大小上限")
    backups = snapshot_tree(plain_path(run, "backup", exists=False))
    if backups != {c["path"]: c["before"] for c in changes if c["before"] is not None}:
        raise ValueError("演练备份缺失、损坏或包含额外文件")
    actual = snapshot_tree(plain_path(run, "target/ModData", exists=False))
    if record["state"] == "applied" and actual != after:
        raise ValueError("演练候选被修改，拒绝覆盖手动更改")
    if record["state"] in ("prepared", "restored") and actual != before:
        raise ValueError("演练原始副本与基线不同")
    if record["state"] in ("applying", "restoring"):
        if set(actual) - set(after) or any(name not in actual for name in before):
            raise ValueError("中断演练含额外或缺失文件")
        for name, item in actual.items():
            if item not in (before.get(name), after.get(name)):
                raise ValueError("中断演练文件不属于原版或候选内容")
    return record


def restore_rehearsal(run: Path) -> dict:
    record = inspect_rehearsal(run)
    if record["state"] == "restored":
        return {"run": record["id"], "state": "restored", "baseline_verified": True, **UNVERIFIED}
    # 所有备份和现有副本验证完成后才开始恢复；中断可继续。
    saved = {c["path"]: read_bytes(plain_path(run, "backup/" + c["path"]))
             for c in record["changes"] if c["before"] is not None}
    if any({"bytes": len(saved[c["path"]]), "sha256": sha(saved[c["path"]])} != c["before"]
           for c in record["changes"] if c["before"] is not None):
        raise ValueError("备份在恢复准备期间改变")
    record["state"] = "restoring"
    replace_bytes(plain_path(run, "transaction.json"), json_bytes(record))
    target = plain_path(run, "target/ModData", exists=False)
    for change in record["changes"]:
        path = plain_path(run, "target/ModData/" + change["path"], exists=False)
        if change["before"] is None:
            if path.exists():
                # 只删除本事务明确新增、已经核对内容的项目内文件。
                if sha(read_bytes(path)) != change["after"]["sha256"]:
                    raise ValueError("待移除的演练新增文件在恢复期间改变")
                path.unlink()
        else:
            current = read_bytes(path)
            if {"bytes": len(current), "sha256": sha(current)} not in (change["before"], change["after"]):
                raise ValueError("演练文件在恢复期间改变")
            replace_bytes(path, saved[change["path"]])
    if snapshot_tree(target) != record["baseline"]:
        raise ValueError("恢复后的演练文件集与原版不同")
    record["state"] = "restored"
    replace_bytes(plain_path(run, "transaction.json"), json_bytes(record))
    inspect_rehearsal(run)
    return {"run": record["id"], "state": "restored", "baseline_verified": True, **UNVERIFIED}
