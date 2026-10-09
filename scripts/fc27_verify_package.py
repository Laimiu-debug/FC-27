"""只读重建并核对磁盘上的容器、全部索引副本及新 CAS；不运行加载器。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from fc27_build import digest, local_input, strict_json
from fc27_assets import input_path, small_file
from fc27_package import prepare


def run(game_root: Path, bundle: Path, export_root: Path, package: Path) -> dict:
    game_root = game_root.resolve(strict=True)
    bundle, export_root, package = map(local_input, (bundle, export_root, package))
    report = strict_json(small_file(input_path(package, "package-report.json")))
    if not isinstance(report, dict) or set(report) != {"summary", "bundles", "files"}:
        raise ValueError("打包报告格式不同")
    files, expected = prepare(game_root, bundle, export_root, report["summary"]["target_profile_label"])
    expected["files"] = [{"path": name, "bytes": len(raw), "sha256": digest(raw)}
                         for name, raw in sorted(files.items())]
    if report != expected:
        raise ValueError("打包报告与从绑定基线重建的结果不同")
    actual_names = {path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_file()}
    if actual_names != set(files) | {"package-report.json"}:
        raise ValueError("打包目录包含缺失或计划外文件")
    for name, raw in files.items():
        if small_file(input_path(package, name)) != raw:
            raise ValueError(f"磁盘打包文件与重建结果不同：{name}")
    summary = expected["summary"]
    return {"files_verified": len(files), "assets_verified": summary["assets_packaged"],
            "bundles_verified": summary["bundles_compiled"], "cas_archives_verified": summary["cas_archives_created"],
            "container_roundtrip_verified": True, "all_outputs_match_rebuilt_baseline": True,
            "game_started_by_tool": False, "game_files_written": False,
            "loader_compatibility_verified": False, "gameplay_effect_verified": False, "loadable_mod": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("game-root", "bundle", "fc27-export", "package"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.game_root, args.bundle, args.fc27_export, args.package), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
