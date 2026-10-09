"""只读扫描或导出 FC27 玩法 EBX；输出只能新建在本项目 local 下。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fc27_runtime import project_root
PROJECT_ROOT = project_root()

from fc27_assets import Catalog, OodleDecoder, export_asset, safe_relative
from fc27_riff import verify_ebx
from fc27_offline import write_report


MAX_EXPORT_BYTES = 64 * 1024 * 1024


def output_path(path: Path, game_root: Path) -> Path:
    resolved = path.resolve()
    local = (PROJECT_ROOT / "local").resolve()
    if resolved == local or not resolved.is_relative_to(local) or resolved.is_relative_to(game_root.resolve()):
        raise ValueError("导出目录必须位于项目 local 内，且不能位于游戏目录")
    if resolved.exists():
        raise FileExistsError("导出目录已存在；请选择新目录，工具不覆盖已有内容")
    return resolved


def run(game_root: Path, output: Path | None = None, name: str | None = None,
        codec: Path | None = None) -> dict:
    destination = output_path(output, game_root) if output else None
    catalog = Catalog(game_root)
    assets, stats = catalog.scan_attrib()
    if name:
        assets = [a for a in assets if a.name == name]
        if not assets:
            raise ValueError("索引中没有指定玩法资产")
    result = {
        "tool": "fc27-offline-gameplay-export", "game_started_by_tool": False, "game_files_written": False,
        "gameplay_mod_generated": False, "source_hashes": catalog.source_hashes,
        "scan": stats, "selected_assets": len(assets), "exported_assets": 0,
    }
    if destination is None:
        result["assets"] = [a.record(catalog) for a in assets]
        return result
    oodle = OodleDecoder(codec, catalog.root) if codec else None
    decoded = []
    total_size = 0
    for asset in assets:
        total_size += asset.original_size
        if total_size > MAX_EXPORT_BYTES:
            raise ValueError("本次导出超过 64 MiB 上限")
        data = export_asset(catalog, asset, oodle)
        inspection = verify_ebx(data)
        if not inspection["byte_identical"]:
            raise ValueError("EBX 未修改读写结果不一致")
        record = asset.record(catalog)
        record.update(cas_sha1_verified=True, inspection=inspection,
                      output=asset.name + ".ebx", decoded_sha256=hashlib.sha256(data).hexdigest())
        decoded.append((record, data))
    # 全部读取、散列和结构验证成功后，再创建新的输出目录。
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    for record, data in decoded:
        target = (destination / safe_relative(record["output"])).resolve()
        if not target.is_relative_to(destination):
            raise ValueError("导出资源路径超出输出目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
    result.update(exported_assets=len(decoded), decoded_bytes=total_size,
                  all_cas_sha1_verified=True, all_ebx_roundtrips_verified=True,
                  assets=[r for r, _ in decoded])
    with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="新的本地导出目录；省略则只扫描索引")
    parser.add_argument("--name", help="只选择一个完整资源名")
    parser.add_argument("--codec", type=Path, help="可选、已校验的 FMT Oodle 解压库，不使用游戏 DLL")
    parser.add_argument("--report", type=Path, help="新的 local 内 JSON 报告")
    args = parser.parse_args()
    try:
        result = run(args.game_root, args.output, args.name, args.codec)
        if args.report:
            write_report(args.report, args.game_root, result)
        print(json.dumps({k: v for k, v in result.items() if k != "assets"}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
