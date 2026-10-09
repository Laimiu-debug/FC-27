"""FC27 离线模组管理核心：登记、构建、合并、预检与项目内应用/还原演练。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from fc27_build import PROJECT_ROOT, run as fixed_build
from fc27_package import run as package_build
from fc27_pipeline import CONFIG_KEYS, load_config, run as pipeline_build
from fc27_stage_loader import run as loader_stage, verify as verify_stage
from fc27_management import (MAX_JSON, UNVERIFIED, decode_json, identifier, inspect_rehearsal,
                             json_bytes, manifest_sha, merge_plans, operation_lock, plain_path, project_local,
                             read_bytes, relative, replace_bytes, restore_rehearsal, sha,
                             snapshot_tree, write_new)
from fc27_mount import OfflineMount, audit_mount, plain_file
from fc27_progress import emit


PATH_KEYS = {"bundle", "export", "package", "stage"}
REPORTS = {"bundle": "build-report.json", "package": "package-report.json", "stage": "loader-stage-report.json"}


class Manager:
    def __init__(self, root: Path):
        self.root = project_local(root, PROJECT_ROOT)
        self.marker = decode_json(read_bytes(plain_path(self.root, "manager.json"), MAX_JSON))
        if (not isinstance(self.marker, dict) or set(self.marker) != {"format", "config", "safety"} or
                self.marker["format"] != "fc27-offline-manager-v1" or self.marker["safety"] != UNVERIFIED):
            raise ValueError("该目录不是当前离线管理器的目录")
        self.config_path = project_local(self.marker["config"], PROJECT_ROOT)
        config = decode_json(read_bytes(self.config_path, MAX_JSON))
        if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
            raise ValueError("管理器配置不是完整流水线配置")
        value = config["game_root"]
        if not isinstance(value, str) or not value or "\0" in value:
            raise ValueError("管理器配置缺少有效游戏目录")
        game = Path(value)
        self.game = (game if game.is_absolute() else PROJECT_ROOT / game).resolve()
        project_local(self.root, PROJECT_ROOT, self.game)

    def local(self, path: Path | str, exists: bool = True) -> Path:
        return project_local(path, PROJECT_ROOT, self.game, exists)

    def ref(self, path: Path) -> str:
        return self.local(path).relative_to(PROJECT_ROOT.resolve()).as_posix()

    def load_entry(self, entry_id: str) -> tuple[dict, bytes]:
        name = "library/" + identifier(entry_id) + ".json"
        raw = read_bytes(plain_path(self.root, name), MAX_JSON)
        wrapper = decode_json(raw)
        if (not isinstance(wrapper, dict) or set(wrapper) != {"format", "content", "content_sha256"} or
                wrapper["format"] != "fc27-manager-entry-v1" or
                sha(json_bytes(wrapper["content"])) != wrapper["content_sha256"]):
            raise ValueError("模组登记内容被修改或格式不同")
        entry = wrapper["content"]
        keys = {"id", "title", "origin", "paths", "binding", "stats", "assets_fields", "override_paths", "unresolved", "safety"}
        if (not isinstance(entry, dict) or set(entry) != keys or entry["id"] != entry_id or
                entry["safety"] != UNVERIFIED or not isinstance(entry["paths"], dict) or
                set(entry["paths"]) != PATH_KEYS):
            raise ValueError("模组登记字段不同或宣称了未验证能力")
        for path in entry["paths"].values():
            relative(path)
            if not path.startswith("local/"):
                raise ValueError("登记引用不是项目 local 路径")
        return entry, raw

    def describe(self, entry_id: str, title: str, paths: dict, origin: dict, verified: dict) -> dict:
        if not isinstance(title, str) or not title.strip() or len(title) > 120 or any(ord(c) < 32 for c in title):
            raise ValueError("模组标题须为 1 至 120 位无控制字符的文字")
        raws = {key: read_bytes(plain_path(paths[key], name), MAX_JSON) for key, name in REPORTS.items()}
        reports = {key: decode_json(raw) for key, raw in raws.items()}
        build, package, stage = (reports[key] for key in ("bundle", "package", "stage"))
        if ({key: sha(raw) for key, raw in raws.items()} != verified["manager_report_hashes"] or
                sha(raws["bundle"]) != package["summary"]["build_report_sha256"] or
                sha(raws["package"]) != verified["package_report_sha256"] or
                stage["summary"]["package_report_sha256"] != sha(raws["package"])):
            raise ValueError("报告在管理器预检期间改变")
        plan = read_bytes(plain_path(paths["bundle"], "plan.json"), MAX_JSON)
        if sha(plan) != build["summary"]["plan_sha256"]:
            raise ValueError("登记的计划与编译报告不同")
        return {"id": identifier(entry_id), "title": title, "origin": origin,
                "paths": {key: self.ref(path) for key, path in sorted(paths.items())},
                "binding": {"report_hashes": {key: sha(raw) for key, raw in raws.items()},
                            "plan_sha256": sha(plan), "source_hashes": package["summary"]["source_hashes"],
                            "patch_head": package["summary"]["patch_head"],
                            "source_inventory_sha256": sha(json_bytes(stage["source_inventory"])),
                            "baseline_metadata_sha256": manifest_sha({item["path"]: {
                                "bytes": item["bytes"], "sha256": item["sha256"]}
                                for item in stage["source_inventory"] if item["kind"] == "metadata"}),
                            "staged_files_sha256": manifest_sha({item["path"]: {
                                "bytes": item["bytes"], "sha256": item["sha256"]}
                                for item in stage["staged_files"]})},
                "stats": {key: build["summary"][key] for key in ("assets_built", "changed_values", "changed_bytes")},
                "assets_fields": {asset["name"]: [edit["field"] for edit in asset["edits"]]
                                  for asset in decode_json(plan)["assets"]},
                "override_paths": stage["override_paths"], "unresolved": stage["unresolved"],
                "safety": dict(UNVERIFIED)}

    def verified(self, paths: dict) -> dict:
        hashes = {key: sha(read_bytes(plain_path(paths[key], name), MAX_JSON)) for key, name in REPORTS.items()}
        result = verify_stage(self.game, paths["bundle"], paths["export"], paths["package"], paths["stage"])
        if hashes != {key: sha(read_bytes(plain_path(paths[key], name), MAX_JSON)) for key, name in REPORTS.items()}:
            raise ValueError("报告在管理预检期间改变")
        return {**result, "manager_report_hashes": hashes}

    def register(self, entry_id: str, title: str, paths: dict, origin: dict | None = None) -> dict:
        identifier(entry_id)
        if set(paths) != PATH_KEYS:
            raise ValueError("登记需要构建、导出、打包和加载副本四个目录")
        target = plain_path(self.root, "library/" + entry_id + ".json", exists=False)
        if target.exists():
            raise FileExistsError("该模组标识已登记，不覆盖旧记录")
        paths = {key: self.local(path) for key, path in paths.items()}
        verified = self.verified(paths)
        entry = self.describe(entry_id, title, paths, origin or {"kind": "registered", "parents": []}, verified)
        write_new(target, json_bytes({"format": "fc27-manager-entry-v1", "content": entry,
                                      "content_sha256": sha(json_bytes(entry))}))
        return {"id": entry_id, "title": title, "registered": True, **entry["stats"], **UNVERIFIED}

    def check(self, entry_id: str) -> tuple[dict, dict]:
        emit("preflight", "预检候选：" + entry_id)
        entry, _ = self.load_entry(entry_id)
        paths = {key: self.local(path) for key, path in entry["paths"].items()}
        for key, name in REPORTS.items():
            if sha(read_bytes(plain_path(paths[key], name), MAX_JSON)) != entry["binding"]["report_hashes"][key]:
                raise ValueError("登记后报告发生变化，需要重新研究而非继续使用旧登记")
        verified = self.verified(paths)
        expected = self.describe(entry_id, entry["title"], paths, entry["origin"], verified)
        if expected != entry:
            raise ValueError("登记与当前绑定基线重建结果不同")
        return entry, verified

    def listing(self) -> dict:
        folder = self.root / "library"
        entries = []
        for path in sorted(folder.iterdir()):
            if path.suffix != ".json" or not path.is_file():
                raise ValueError("登记目录含有计划外内容")
            entry, _ = self.load_entry(path.stem)
            entries.append({"id": entry["id"], "title": entry["title"], **entry["stats"],
                            "live_preflight_performed": False, "status": "已登记的离线候选，使用前需预检",
                            **UNVERIFIED})
        return {"entries": entries, "count": len(entries), **UNVERIFIED}

    def build(self, entry_id: str, title: str, module: str) -> dict:
        identifier(entry_id)
        if (self.root / "library" / (entry_id + ".json")).exists():
            raise FileExistsError("模组标识已使用")
        output = self.local(self.root / "builds" / entry_id, exists=False)
        if output.exists():
            raise FileExistsError("构建目录已存在，不覆盖旧构建")
        pipeline_build(self.config_path, output, module, with_loader_stage=True)
        config = load_config(self.config_path)
        emit("register")
        return self.register(entry_id, title, {"bundle": output / "built", "export": config["fc27_export"],
                                              "package": output / "packaged", "stage": output / "loader-stage"},
                             {"kind": "pipeline", "module": module, "parents": []})

    def selected_plans(self, ids: list[str]) -> tuple[list[dict], list[dict]]:
        if not ids or len(ids) > 64 or len(set(ids)) != len(ids):
            raise ValueError("须选择 1 至 64 个不重复的登记模组")
        entries, plans = [], []
        for entry_id in ids:
            entry, _ = self.check(entry_id)
            entries.append(entry)
            plans.append(decode_json(read_bytes(plain_path(self.local(entry["paths"]["bundle"]), "plan.json"), MAX_JSON)))
        if any(entry["binding"][key] != entries[0]["binding"][key]
               for entry in entries for key in ("source_hashes", "patch_head", "source_inventory_sha256")):
            raise ValueError("所选模组的游戏版本或原版数据清单不同")
        return entries, plans

    def preview(self, ids: list[str]) -> dict:
        entries, plans = self.selected_plans(ids)
        paths, assets = {}, {}
        for entry in entries:
            for name in entry["override_paths"]:
                paths.setdefault(name, []).append(entry["id"])
            for name in entry["assets_fields"]:
                assets.setdefault(name, []).append(entry["id"])
        result = {"selected": ids, "shared_output_paths": {k: v for k, v in paths.items() if len(v) > 1},
                  "shared_assets": {k: v for k, v in assets.items() if len(v) > 1},
                  "compiled_packages_can_be_stacked": False, "requires_recompile": len(ids) > 1,
                  "combined_plan_compiled": False,
                  **UNVERIFIED}
        try:
            combined = merge_plans(plans)
            result.update(plans_mergeable=True, merged_assets=len(combined["assets"]),
                          merged_edits=sum(len(a["edits"]) for a in combined["assets"]))
        except ValueError as exc:
            result.update(plans_mergeable=False, conflict=str(exc))
        return result

    def compose(self, entry_id: str, title: str, ids: list[str]) -> dict:
        identifier(entry_id)
        if entry_id in ids or (self.root / "library" / (entry_id + ".json")).exists():
            raise FileExistsError("合并结果须使用新的模组标识")
        entries, plans = self.selected_plans(ids)
        emit("merge")
        plan = merge_plans(plans)
        config = load_config(self.config_path)
        output = self.local(self.root / "builds" / entry_id, exists=False)
        if output.exists():
            raise FileExistsError("合并输出已存在")
        output.mkdir(parents=True)
        plan_path = output / "merged-plan.json"
        write_new(plan_path, json_bytes(plan))
        emit("build")
        fixed_build(self.game, config["fc27_export"], config["sdk"], config["shared_types"], plan_path, output / "built")
        emit("package")
        package_build(self.game, output / "built", config["fc27_export"], output / "packaged", "FC27")
        emit("loader-stage")
        loader_stage(self.game, output / "built", config["fc27_export"], output / "packaged", output / "loader-stage")
        emit("register")
        return self.register(entry_id, title, {"bundle": output / "built", "export": config["fc27_export"],
                                              "package": output / "packaged", "stage": output / "loader-stage"},
                             {"kind": "composed", "parents": [entry["id"] for entry in entries]})

    def rehearsal_path(self, run_id: str, exists: bool = True) -> Path:
        return self.local(self.root / "rehearsals" / identifier(run_id), exists=exists)

    def rehearsal_status(self, run_id: str) -> dict:
        run = self.rehearsal_path(run_id)
        record = inspect_rehearsal(run)
        entry, raw = self.load_entry(record["entry_id"])
        if (sha(raw) != record["entry_sha256"] or
                manifest_sha(record["baseline"]) != entry["binding"]["baseline_metadata_sha256"] or
                manifest_sha(record["applied"]) != entry["binding"]["staged_files_sha256"] or
                {c["path"] for c in record["changes"]} != set(entry["override_paths"])):
            raise ValueError("演练绑定的登记记录改变")
        return {"run": run_id, "entry": record["entry_id"], "state": record["state"],
                "backup_verified": True, "target_verified": True,
                "baseline_files": len(record["baseline"]), "applied_files": len(record["applied"]),
                "modified_files": len(record["changes"]), **UNVERIFIED}

    def rehearse(self, entry_id: str, run_id: str) -> dict:
        run = self.rehearsal_path(run_id, exists=False)
        if run.exists():
            raise FileExistsError("演练标识已存在，不覆盖旧副本或备份")
        entry, _ = self.check(entry_id)
        _, entry_raw = self.load_entry(entry_id)
        stage = self.local(entry["paths"]["stage"])
        report = decode_json(read_bytes(plain_path(stage, "loader-stage-report.json"), MAX_JSON))
        # 在创建副本前把全部原始元数据和本次覆盖载荷核对完。
        baseline_data = {}
        for item in report["source_inventory"]:
            if item["kind"] == "metadata":
                raw = read_bytes(plain_file(self.game, item["path"]))
                if len(raw) != item["bytes"] or sha(raw) != item["sha256"]:
                    raise ValueError("原始元数据在演练准备期间改变")
                baseline_data[item["path"]] = raw
        overlay = {name: read_bytes(plain_path(stage / "ModData", name)) for name in entry["override_paths"]}
        stage_files = {f["path"]: {"bytes": f["bytes"], "sha256": f["sha256"]} for f in report["staged_files"]}
        if any({"bytes": len(raw), "sha256": sha(raw)} != stage_files[name] for name, raw in overlay.items()):
            raise ValueError("候选在演练准备期间改变")
        baseline = {name: {"bytes": len(raw), "sha256": sha(raw)} for name, raw in baseline_data.items()}
        expected = dict(baseline)
        expected.update({name: {"bytes": len(raw), "sha256": sha(raw)} for name, raw in overlay.items()})
        if expected != stage_files:
            raise ValueError("演练原始副本加覆盖文件不能构成已验证加载副本")
        record = {"format": "fc27-local-rehearsal-v1", "id": identifier(run_id), "entry_id": entry_id,
                  "entry_sha256": sha(entry_raw), "baseline": baseline, "applied": expected,
                  "changes": [{"path": name, "before": baseline.get(name), "after": expected[name]}
                              for name in sorted(overlay)], "state": "prepared", "safety": dict(UNVERIFIED)}
        run.mkdir(parents=True)
        (run / "backup").mkdir()
        target = run / "target/ModData"
        for name, raw in baseline_data.items():
            write_new(target / name, raw)
        for name in overlay:
            if name in baseline_data:
                write_new(run / "backup" / name, baseline_data[name])
        write_new(run / "transaction.json", json_bytes(record))
        self.rehearsal_status(run_id)
        record["state"] = "applying"
        replace_bytes(plain_path(run, "transaction.json"), json_bytes(record))
        for name, raw in overlay.items():
            path = plain_path(run, "target/ModData/" + name, exists=False)
            if name in baseline:
                current = read_bytes(path)
                if {"bytes": len(current), "sha256": sha(current)} != baseline[name]:
                    raise ValueError("演练原版文件在应用期间改变")
            elif path.exists():
                raise ValueError("演练新增路径在应用期间被占用")
            replace_bytes(path, raw)
        mount = OfflineMount(self.game, target, set(expected),
                             {item["path"]: item for item in report["fallback_files"]})
        if audit_mount(mount, report["selected"]) != report["mount_audit"]:
            raise ValueError("演练目标的加载审查与已验证副本不同")
        record["state"] = "applied"
        replace_bytes(plain_path(run, "transaction.json"), json_bytes(record))
        return self.rehearsal_status(run_id)

    def restore(self, run_id: str) -> dict:
        self.rehearsal_status(run_id)
        result = restore_rehearsal(self.rehearsal_path(run_id))
        self.rehearsal_status(run_id)
        return result


def initialize(root: Path, config_path: Path) -> dict:
    config_path = project_local(config_path, PROJECT_ROOT)
    config = decode_json(read_bytes(config_path, MAX_JSON))
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
        raise ValueError("初始化需要完整流水线配置")
    game = Path(config["game_root"])
    game = (game if game.is_absolute() else PROJECT_ROOT / game).resolve()
    root = project_local(root, PROJECT_ROOT, game, exists=False)
    if root.exists():
        raise FileExistsError("管理目录已存在，请使用已有目录或选择新的目录")
    root.mkdir(parents=True)
    for name in ("library", "builds", "rehearsals"):
        (root / name).mkdir()
    write_new(root / "manager.json", json_bytes({"format": "fc27-offline-manager-v1",
              "config": config_path.relative_to(PROJECT_ROOT.resolve()).as_posix(), "safety": dict(UNVERIFIED)}))
    return {"initialized": True, "root": root.relative_to(PROJECT_ROOT.resolve()).as_posix(), **UNVERIFIED}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("local/mod-manager"))
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="初始化项目内管理目录")
    init.add_argument("--config", type=Path, required=True)
    register = commands.add_parser("register", help="预检并登记已有候选")
    for name in ("bundle", "export", "package", "stage"):
        register.add_argument("--" + name, type=Path, required=True)
    for name in ("register", "build", "compose"):
        command = register if name == "register" else commands.add_parser(name)
        command.add_argument("--id", required=True)
        command.add_argument("--title", required=True)
        if name == "build":
            command.add_argument("--module", default="all")
        elif name == "compose":
            command.add_argument("--ids", nargs="+", required=True)
    commands.add_parser("list", help="列出登记；此命令不宣称已重新预检")
    check = commands.add_parser("check", help="重新核对当前游戏版本和候选全部产物")
    check.add_argument("--id", required=True)
    preview = commands.add_parser("preview", help="预检多个登记，报告字段和输出路径冲突")
    preview.add_argument("--ids", nargs="+", required=True)
    rehearsal = commands.add_parser("rehearse", help="只在项目内新副本中备份并应用候选")
    rehearsal.add_argument("--id", required=True)
    rehearsal.add_argument("--run", required=True)
    for name in ("status", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--run", required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.root, args.config)
        else:
            manager = Manager(args.root)
            # 写操作及状态预检串行；操作系统在进程退出时释放锁。
            with operation_lock(manager.root):
                if args.command == "register":
                    result = manager.register(args.id, args.title, {key: getattr(args, key) for key in PATH_KEYS})
                elif args.command == "build":
                    result = manager.build(args.id, args.title, args.module)
                elif args.command == "compose":
                    result = manager.compose(args.id, args.title, args.ids)
                elif args.command == "list":
                    result = manager.listing()
                elif args.command == "preview":
                    result = manager.preview(args.ids)
                elif args.command == "check":
                    entry, checked = manager.check(args.id)
                    result = {"id": args.id, "preflight_passed": True, **entry["stats"],
                              "bundle_locations_checked": checked["bundle_locations_checked"], **UNVERIFIED}
                elif args.command == "rehearse":
                    result = manager.rehearse(args.id, args.run)
                elif args.command == "status":
                    result = manager.rehearsal_status(args.run)
                else:
                    result = manager.restore(args.run)
        print(json_bytes(result).decode("utf-8"), end="")
        return 0
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
