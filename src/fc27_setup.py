"""首次启动与依赖清单；只读游戏结构，不执行研究 DLL。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys
import uuid

from fc27_management import (MAX_JSON, UNVERIFIED, decode_json, json_bytes, plain_path,
                             project_local, read_bytes, replace_bytes, write_new)
from fc27_runtime import resource_root


FIELDS = ("game_root", "fc27_export", "sample", "sdk", "shared_types", "codec")
TITLES = {"game_root": "游戏目录（只读）", "fc27_export": "已校验的 FC27 资产导出",
          "sample": "前代玩法参考包", "sdk": "静态 SDK 元数据", "shared_types": "本机共享类型描述",
          "codec": "已校验的解压依赖", "recipe": "整场比赛研究方案"}
DEFAULTS = {"game_root": "", "fc27_export": "local/inputs/fc27-export", "sample": "local/inputs/reference.fifamod",
            "sdk": "local/inputs/FC26SDK.dll", "shared_types": "local/inputs/SharedTypeDescriptors.ebx",
            "codec": "local/inputs/oo2core_9_win64.dll", "recipe": "resources/whole-match-study.json"}


def no_links(path: Path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("工作区路径不能经过目录链接")


def create_workspace(path: Path) -> Path:
    """只接受新目录或空目录；不把游戏安装目录改成工作区。"""
    path = Path(os.path.abspath(path))
    no_links(path)
    if path.is_relative_to(resource_root()) and getattr(sys, "frozen", False):
        raise ValueError("不能在 EXE 解包目录创建工作区")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("新工作区必须使用空目录；已有项目请选择连接")
    # 防止用户把工作区放在游戏目录的下级目录。
    if any((parent / "FC27.exe").exists() or (parent / "Data/layout.toc").exists()
           for parent in (path, *path.parents)):
        raise ValueError("工作区不能位于游戏安装目录")
    templates = {}
    for name in ("whole-match-study.json", "pipeline-config.example.json"):
        templates[name] = read_bytes(plain_path(resource_root() / "resources", name), MAX_JSON)
    path.mkdir(parents=True, exist_ok=True)
    (path / "local/inputs").mkdir(parents=True)
    (path / "resources").mkdir()
    for name, raw in templates.items():
        write_new(path / "resources" / name, raw)
    write_new(path / ".gitignore", b"/local/\n")
    return path


def detect_games() -> list[str]:
    """仅查看固定 Steam 位置和库路径，不扫描账号或存档。"""
    steam_roots = [Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam"]
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam_roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except OSError:
            pass
    libraries = list(steam_roots)
    for root in steam_roots:
        try:
            raw = read_bytes(plain_path(root, "steamapps/libraryfolders.vdf"), 1024 * 1024).decode("utf-8")
            libraries.extend(Path(value.replace("\\\\", "\\")) for value in re.findall(r'"path"\s*"([^"\r\n]+)"', raw))
        except (OSError, ValueError, UnicodeError):
            pass
    if os.name == "nt":
        libraries.extend(Path(letter + ":/SteamLibrary") for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ")
    candidates = {str(root / "steamapps/common/EA SPORTS FC 27") for root in libraries}
    return sorted(path for path in candidates if (Path(path) / "Data/layout.toc").is_file()
                  and (Path(path) / "Patch/layout.toc").is_file())


def readiness(project: Path, config: dict) -> dict:
    from fc27_assets import OODLE_SHA256
    from fc27_study import validate_recipe
    checks = []
    for key in (*FIELDS, "recipe"):
        ok, message = False, "尚未提供"
        value = config.get(key, "")
        try:
            if not isinstance(value, str) or not value:
                raise ValueError("请选择或填写路径")
            path = Path(value)
            path = path if path.is_absolute() else project / path
            if key == "game_root":
                no_links(path)
                if not path.is_dir() or not all((path / name).is_file() for name in (
                        "Data/layout.toc", "Patch/layout.toc", "Data/Win32/fc/fcgame/fcgame.toc", "Patch/Win32/fc/fcgame/fcgame.toc")):
                    raise ValueError("缺少 Data/Patch 的 layout.toc 或 fcgame.toc")
                message = "目录结构可读取；版本绑定将在构建时核对"
            elif key == "recipe":
                no_links(path)
                raw = read_bytes(plain_path(project / "resources", path.resolve().relative_to((project / "resources").resolve()).as_posix()), MAX_JSON)
                validate_recipe(decode_json(raw))
                message = "研究方案格式通过"
            else:
                path = project_local(path, project)
                file = plain_path(path, "manifest.json") if key == "fc27_export" else path
                if not file.is_file() or file.stat().st_nlink != 1:
                    raise ValueError("输入不是独立普通文件")
                raw = read_bytes(file, 64 * 1024 * 1024) if key in ("codec", "fc27_export") else b""
                if key == "codec" and hashlib.sha256(raw).hexdigest() != OODLE_SHA256:
                    raise ValueError("依赖散列与已核对来源不符；不会加载该文件")
                if key == "fc27_export":
                    manifest = decode_json(raw)
                    if not isinstance(manifest, dict) or not manifest.get("all_cas_sha1_verified") or not manifest.get("all_ebx_roundtrips_verified"):
                        raise ValueError("导出清单未记录完整载荷与往返校验")
                message = "文件已找到；内容与基线将在构建时复核"
                if key == "codec":
                    message = "来源散列一致；本次未执行解压库"
            ok = True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            message = str(exc)
        checks.append({"id": key, "title": TITLES[key], "ready": ok, "message": message})
    return {"inputs_present": all(item["ready"] for item in checks), "checks": checks,
            "fully_verified": False, **UNVERIFIED}


def configure(project: Path, manager_root: Path, values: dict, initialize) -> dict:
    project = project.resolve(strict=True)
    config = {**{key: values[key].strip() for key in FIELDS}, "recipe": DEFAULTS["recipe"]}
    game = Path(config["game_root"])
    game = (game if game.is_absolute() else project / game).resolve(strict=True)
    no_links(game)
    if not (game / "Data/layout.toc").is_file() or not (game / "Patch/layout.toc").is_file():
        raise ValueError("游戏目录缺少 Data/Patch 的 layout.toc；请重新选择")
    manager_root = project_local(manager_root, project, game, exists=False)
    for key in FIELDS[1:]:
        project_local(Path(config[key]), project, game, exists=False)
    marker = plain_path(manager_root, "manager.json", exists=False)
    old = None
    if manager_root.exists():
        old = decode_json(read_bytes(marker, MAX_JSON))
        if (set(old) != {"format", "config", "safety"} or old["format"] != "fc27-offline-manager-v1"
                or old["safety"] != UNVERIFIED):
            raise ValueError("不能替换不受管理的工作区配置")
    path = project_local(project / "local/onboarding" / ("config-" + uuid.uuid4().hex + ".json"), project, game, exists=False)
    write_new(path, json_bytes(config))
    if old is None:
        initialize(manager_root, path)
    else:
        replace_bytes(marker, json_bytes({**old, "config": path.relative_to(project).as_posix()}))
    return {"configured": True, "readiness": readiness(project, config), **UNVERIFIED}
