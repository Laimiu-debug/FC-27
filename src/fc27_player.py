"""普通用户入口：识别版本与内置方案，加载未解决时明确拒绝启用。"""

from __future__ import annotations

from pathlib import Path

from fc27_management import (UNVERIFIED, decode_json, json_bytes, plain_path, project_local,
                             read_bytes, replace_bytes, write_new)
from fc27_presets import digest, load_preset, select_assets, version_check


def choose_game(project: Path, game: str):
    preset = load_preset()
    path = Path(game)
    if not path.is_absolute():
        raise ValueError("请选择完整的游戏目录")
    checked = version_check(path, preset)
    # 可以保存另一版本的位置，但不会允许套用旧预设。
    target = project_local(project / "local/player/game.json", project, exists=False)
    raw = json_bytes({"game_root": str(path.resolve(strict=True))})
    if target.exists():
        replace_bytes(target, raw)
    else:
        write_new(target, raw)
    return {"game_selected": True, "version_matched": checked["matched"], **UNVERIFIED}


def snapshot(project: Path, detected: list[str], preferred: str = "") -> dict:
    result = {"game_root": "", "detected_games": detected, "game_detected": False,
              "version_matched": False, "can_prepare": False, "can_enable": False,
              "enabled": False, "installed": False, "status": "unavailable",
              "message": "FC27 加载适配尚未完成，当前不能启用玩法包。启动游戏仍会使用原版玩法。",
              "error": "", "modules": [], "last_prepared": None, **UNVERIFIED}
    try:
        preset = load_preset()
        result["modules"] = [{"id": "all", "title": "整场比赛 · 全部六组"}] + [
            {"id": m["id"], "title": m["title"]} for m in preset["modules"]]
        result["preset_title"] = preset["title"]
        result["assets"] = len(preset["assets"])
        result["changes"] = sum(len(a["changes"]) for a in preset["assets"])
        target = project_local(project / "local/player/game.json", project, exists=False)
        if target.exists():
            value = decode_json(read_bytes(target, 8192))
            if set(value) != {"game_root"} or not isinstance(value["game_root"], str):
                raise ValueError("游戏位置记录格式不同")
            game = value["game_root"]
        else:
            game = preferred or (detected[0] if len(detected) == 1 else "")
        result["game_root"] = game
        if game:
            checked = version_check(Path(game), preset)
            result.update(game_detected=True, version_matched=checked["matched"],
                          can_prepare=checked["matched"], mismatched_files=checked["mismatched_files"])
            if not checked["matched"]:
                result["error"] = "游戏版本与内置预设不同；需要开发者更新预设，你无需准备 SDK。"
        else:
            result["error"] = "未识别到唯一游戏目录，可在下方选择安装位置。"
        folder = project_local(project / "local/preset-builds", project, exists=False)
        if folder.exists():
            # 仅显示完成记录，不把历史记录作为当前启用或重验证的证明。
            for directory in sorted(folder.iterdir(), key=lambda p: p.name):
                if not directory.name.startswith("prepared-"):
                    continue
                path = project_local(directory, project)
                report_path = plain_path(path, "preset-result.json", exists=False)
                if not report_path.exists():
                    continue
                value = decode_json(read_bytes(report_path, 16384))
                if value.get("preset_sha256") != digest(json_bytes(preset)):
                    # 旧预设完成记录保留在磁盘，不阻止使用更新后的内置预设。
                    continue
                if (value.get("format") != "fc27-preset-prepared-v1" or value.get("prepared") is not True
                        or value.get("source_hashes") != preset["source_hashes"]
                        or value.get("output") != path.relative_to(project.resolve()).as_posix()
                        or value.get("enabled") is not False or value.get("installed") is not False
                        or any(value.get(k) is not v for k, v in UNVERIFIED.items())):
                    raise ValueError("离线准备记录格式不同或宣称未验证的能力")
                select_assets(preset, value["module"])
                record = {k: value[k] for k in ("module", "assets_built", "changed_values", "changed_bytes", "output")}
                record["recorded_at_ns"] = report_path.stat().st_mtime_ns
                if result["last_prepared"] is None or record["recorded_at_ns"] > result["last_prepared"]["recorded_at_ns"]:
                    result["last_prepared"] = record
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["error"] = str(exc)
        result["can_prepare"] = False
    return result
