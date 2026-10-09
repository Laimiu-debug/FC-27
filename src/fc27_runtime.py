"""区分只读程序资源与外部研究工作区，兼容单文件 EXE。"""

from __future__ import annotations

from pathlib import Path
import sys
from fc27_version import VERSION


_workspace: Path | None = None


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parents[1]


def validate_workspace(path: Path) -> Path:
    for current in (path, *path.parents):
        if current.is_symlink() or current.is_junction():
            raise ValueError("工作区路径不能经过目录链接")
    path = path.resolve(strict=True)
    if any((parent / "FC27.exe").exists() or (parent / "Data/layout.toc").exists()
           for parent in (path, *path.parents)):
        raise ValueError("研究工作区不能位于游戏安装目录")
    if not path.is_dir() or not (path / "resources/whole-match-study.json").is_file():
        raise ValueError("请选择包含 resources/whole-match-study.json 的 FC27 研究项目目录")
    local = path / "local"
    if not local.is_dir() or local.is_symlink() or local.is_junction():
        raise ValueError("工作区须已有普通 local 目录，且不能使用目录链接")
    if getattr(sys, "frozen", False) and path.is_relative_to(resource_root()):
        raise ValueError("EXE 临时解包目录不能作为研究工作区")
    return path


def discover_workspace(executable: Path) -> Path | None:
    parent = executable.resolve().parent
    for path in (parent, *parent.parents):
        try:
            return validate_workspace(path)
        except (OSError, ValueError):
            continue
    return None


def configure_workspace(path: Path) -> Path:
    global _workspace
    _workspace = validate_workspace(path)
    return _workspace


def project_root() -> Path:
    if _workspace is not None:
        return _workspace
    if getattr(sys, "frozen", False):
        raise ValueError("请先选择外部研究工作区，再加载离线管理核心")
    return resource_root()
