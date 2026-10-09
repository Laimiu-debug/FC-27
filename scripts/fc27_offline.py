"""只读检查 FC27 索引并验证离线读写；报告只能创建在项目 local 目录。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fc27_runtime import project_root
PROJECT_ROOT = project_root()

from fc27_dbobject import FormatError, MAX_FILE_BYTES, verify_roundtrip


FILES = ("Data/layout.toc", "Patch/layout.toc", "Data/initfs_Win32", "Patch/initfs_Win32")


def read_baseline(game_root: Path) -> dict:
    game_root = game_root.resolve(strict=True)
    results = []
    for relative in FILES:
        path = (game_root / relative).resolve(strict=True)
        if not path.is_relative_to(game_root):
            raise ValueError("输入文件解析后超出游戏目录")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("输入文件过大")
        record = {"path": relative}
        try:
            record.update(verify_roundtrip(path.read_bytes()))
            record["status"] = "verified" if record["byte_identical"] else "mismatch"
        except FormatError as exc:
            record.update(status="unsupported", reason=str(exc))
        results.append(record)
    return {
        "tool": "fc27-dbobject-offline-prototype",
        "game_started_by_tool": False,
        "game_files_written": False,
        "gameplay_mod_generated": False,
        "all_roundtrips_verified": all(r["status"] == "verified" for r in results),
        "files": results,
    }


def write_report(path: Path, game_root: Path, result: dict) -> None:
    local = (PROJECT_ROOT / "local").resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(local) or resolved.is_relative_to(game_root.resolve()):
        raise ValueError("报告必须位于项目 local 目录，并且不能位于游戏目录")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    # 创建新报告，禁止覆盖任何现有文件。
    with resolved.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", type=Path, required=True, help="本地 FC27 安装目录，仅只读")
    parser.add_argument("--report", type=Path, help="新建 JSON 报告的路径；只能位于项目 local 目录")
    args = parser.parse_args()
    try:
        result = read_baseline(args.game_root)
        if args.report:
            write_report(args.report, args.game_root, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["all_roundtrips_verified"] else 2
    except (ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
