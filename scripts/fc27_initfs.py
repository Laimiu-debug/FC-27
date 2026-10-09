"""读取本机 initfs，仅导出 FC27 共享类型描述及无敏感内容的校验清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fc27_assets import small_file
from fc27_initfs import extract_types
from fc27_sharedtypes import SharedTypes
from fc27_export import output_path


def run(game_root: Path, key_file: Path, output: Path) -> dict:
    destination = output_path(output, game_root)
    key_file = key_file.resolve(strict=True)
    if not key_file.is_relative_to((PROJECT_ROOT / "local").resolve()):
        raise ValueError("格式密钥依赖必须位于本项目 local 中，不读取账户凭据")
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        raise ValueError("此 AES 解码适配器需要 Windows PowerShell/.NET")

    def decrypt(cipher):
        process = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-File",
                                  str(PROJECT_ROOT / "scripts" / "decrypt_initfs.ps1"),
                                  "-KeyFile", str(key_file)], input=cipher,
                                 capture_output=True, timeout=60, check=False)
        if process.returncode:
            # 不回显依赖内容、解密结果或其他可能包含本地数据的错误输出。
            raise ValueError("initfs AES 解码失败")
        return process.stdout

    reports, exports = [], []
    for layer in ("Data", "Patch"):
        source = (game_root / layer / "initfs_Win32").resolve(strict=True)
        if not source.is_relative_to(game_root.resolve()):
            raise ValueError("initfs 路径超出游戏目录")
        files, report = extract_types(small_file(source), decrypt)
        report["layer"] = layer
        reports.append(report)
        for name, data in files.items():
            shared = SharedTypes(data)
            exports.append((layer, name, data))
            report.setdefault("files", []).append({"name": name, "bytes": len(data),
                                                  "sha256": shared.sha256, "types": len(shared.types),
                                                  "fields": len(shared.fields)})
    if not exports:
        raise ValueError("没有找到 FC27 共享类型描述")
    summary = {"files": reports, "format_key_source_sha256": hashlib.sha256(small_file(key_file)).hexdigest(),
               "game_started_by_tool": False, "game_files_written": False,
               "export_scope": "SharedTypeDescriptors only"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    for layer, name, data in exports:
        path = destination / layer / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("game-root", "key-file", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.game_root, args.key_file, args.output), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
