"""以受控输入构建 Windows x64 单文件 EXE，不收集 local 研究资源。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fc27_version import VERSION
WEBVIEW_SDK = "1.0.3856.49"
NUGet_URL = ("https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/" + WEBVIEW_SDK +
             "/microsoft.web.webview2." + WEBVIEW_SDK + ".nupkg")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def output_folder(path: Path) -> Path:
    lexical = Path(os.path.abspath(path if path.is_absolute() else ROOT / path))
    bases = [ROOT / name for name in ("dist", "release", "releases", "local")]
    if not any(lexical.is_relative_to(base) and lexical != base for base in bases):
        raise ValueError("打包输出只能新建在项目 dist、release、releases 或 local 下")
    current = ROOT
    for part in lexical.relative_to(ROOT).parts:
        current /= part
        if current.is_symlink() or current.is_junction():
            raise ValueError("打包输出不接受目录链接")
    if lexical.exists():
        raise ValueError("输出目录已存在；请指定新的输出位置")
    return lexical


def collect_licenses(destination: Path) -> dict:
    destination.mkdir(parents=True)
    versions = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        versions[name] = dist.version
        if name.replace("-", "_") == "proxy_tools":
            # PyPI 包未带完整许可，采用作者仓库 LICENSE.txt 的原文副本。
            shutil.copyfile(ROOT / "resources/packaging/licenses/proxy-tools.txt", destination / "proxy_tools.txt")
            continue
        texts = []
        for file in dist.files or []:
            if file.name.lower().startswith(("license", "copying")):
                path = dist.locate_file(file)
                if path.is_file() and path.stat().st_size < 2 * 1024 * 1024:
                    texts.append(path.read_text(encoding="utf-8", errors="replace"))
        if texts:
            (destination / (name + ".txt")).write_text("\n\n".join(texts), encoding="utf-8")
        elif dist.metadata.get("License"):
            (destination / (name + ".txt")).write_text(dist.metadata["License"], encoding="utf-8")
        else:
            raise ValueError("缺少构建依赖的许可证：" + name)
    for label, path in (("Python-and-Tcl", Path(sys.base_prefix) / "LICENSE.txt"),
                        ("Tk", Path(sys.base_prefix) / "tcl/tk8.6/license.terms")):
        shutil.copyfile(path, destination / (label + ".txt"))

    # pywebview 包含的 WebView2 SDK 适配 DLL，须与官方包匹配，携带原始许可。
    package = ROOT / "local/packaging" / ("webview2-" + WEBVIEW_SDK + ".nupkg")
    if not package.is_file():
        with urllib.request.urlopen(NUGet_URL, timeout=30) as response, package.open("xb") as stream:
            shutil.copyfileobj(response, stream)
    webview = Path(metadata.distribution("pywebview").locate_file("webview/lib"))
    with zipfile.ZipFile(package) as archive:
        pairs = {"Microsoft.Web.WebView2.Core.dll": "lib/net462/Microsoft.Web.WebView2.Core.dll",
                 "Microsoft.Web.WebView2.WinForms.dll": "lib/net462/Microsoft.Web.WebView2.WinForms.dll",
                 "runtimes/win-x64/native/WebView2Loader.dll": "runtimes/win-x64/native/WebView2Loader.dll"}
        for target, source in pairs.items():
            if hashlib.sha256(archive.read(source)).hexdigest() != sha(webview / target):
                raise ValueError("WebView2 SDK 与官方版本不一致：" + target)
        (destination / "Microsoft-WebView2.txt").write_bytes(archive.read("LICENSE.txt"))
    return versions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist/FC27Manager"))
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise ValueError("请在 Windows x64 上构建")
    if Path(sys.prefix).resolve() != (ROOT / "local/packaging/venv").resolve():
        raise ValueError("请使用项目 local/packaging/venv 的 Python")
    output = output_folder(args.output)
    archive_path = output.parent / ("FC27Manager-" + VERSION + "-win-x64.zip")
    if archive_path.exists():
        raise FileExistsError("发行 ZIP 已存在，不覆盖：" + str(archive_path))
    stamp = uuid.uuid4().hex
    work = ROOT / "build" / ("fc27-exe-" + stamp)
    licenses = ROOT / "local/packaging" / ("licenses-" + stamp)
    work.mkdir(parents=True)
    version_info = work / "version-info.txt"
    version_info.write_text((ROOT / "resources/packaging/version-info.txt").read_text(encoding="utf-8")
                            .replace("@VERSION@", VERSION).replace("@VERSION_TUPLE@", ", ".join(VERSION.split(".") + ["0"])), encoding="utf-8")
    versions = collect_licenses(licenses)
    locked = {}
    for line in (ROOT / "resources/packaging/requirements-exe.txt").read_text(encoding="utf-8").splitlines():
        if "==" in line:
            name, version = line.split("==")
            locked[name] = version
    for name, version in locked.items():
        if metadata.version(name) != version:
            raise ValueError("构建依赖与锁定文件不同：" + name)
    audit = work / "bundle-audit.json"
    environment = dict(os.environ, FC27_BUILD_LICENSES=str(licenses), FC27_BUILD_AUDIT=str(audit),
                       FC27_BUILD_VERSION_INFO=str(version_info))
    # 不继承其他桌面工具注入的 DLL 搜索目录，避免夹带无关运行库。
    windows = Path(os.environ["SystemRoot"])
    environment["PATH"] = os.pathsep.join(str(p) for p in (
        Path(sys.prefix) / "Scripts", Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
        windows / "System32", windows))
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONPATH", None)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--distpath", str(output),
                    "--workpath", str(work), str(ROOT / "resources/packaging/fc27-manager.spec")],
                   cwd=ROOT, env=environment, check=True)
    executable = output / "FC27Manager.exe"
    if executable.read_bytes()[:2] != b"MZ":
        raise ValueError("输出不是 Windows 可执行文件")
    shutil.copytree(licenses, output / "licenses")
    (output / "使用说明.txt").write_text(
        f"FC27 玩法模组管理器 {VERSION} · 离线研究版\n\n"
        "双击 FC27Manager.exe，自动识别游戏并显示玩法方案与当前状态。\n"
        "独立下载的 EXE 自动创建外部数据目录，普通用户无需填写 SDK、参考包或编译配置。\n"
        "当前加载适配尚未完成，不能启用玩法包；启动游戏仍会使用原版玩法。\n"
        "EXE 已包含 Python 与前端，运行无需安装 Python。\n"
        "系统需要 Microsoft Edge WebView2 和 .NET Framework 4.6.2 或更新版。\n"
        "本机候选、配置和备份仍在外部项目 local，EXE 不含游戏或研究素材。\n"
        "点击退出管理器或关闭窗口，会等待当前操作结束。\n"
        "工具不启动游戏、不安装模组；实际加载和比赛效果未验证。\n"
        "第三方许可见 licenses 文件夹，EXE 内也保留同一份许可。\n", encoding="utf-8")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
    manifest = {"format": "fc27-exe-release-v1", "version": VERSION, "architecture": "windows-x64",
                "source_commit": commit, "source_dirty": dirty,
                "created_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
                "build_packages": versions, "exe": {"file": executable.name, "bytes": executable.stat().st_size,
                "sha256": sha(executable)}, "bundled_input_count": len(json.loads(audit.read_text(encoding="utf-8"))),
                "webview_sdk": WEBVIEW_SDK, "game_files_bundled": False, "research_inputs_bundled": False,
                "game_files_written": False, "game_started_by_tool": False, "gameplay_effect_verified": False,
                "loadable_mod": False, "enabled": False, "preset_descriptions_bundled": True}
    (output / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                archive.write(path, "FC27Manager/" + path.relative_to(output).as_posix())
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None or hashlib.sha256(archive.read("FC27Manager/FC27Manager.exe")).hexdigest() != sha(executable):
            raise ValueError("发行 ZIP 校验失败")
    checksum = output.parent / (archive_path.name + ".sha256")
    with checksum.open("x", encoding="ascii") as stream:
        stream.write(sha(archive_path) + "  " + archive_path.name + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
