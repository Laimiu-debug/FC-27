"""一个命令串联离线研究、模块选择、编译、打包和磁盘验证；不部署。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from fc27_build import PROJECT_ROOT, local_input, run as build, strict_json
from fc27_assets import small_file
from fc27_export import output_path
from fc27_package import run as package
from fc27_study import recipe_input, run as study, validate_recipe
from fc27_verify_package import run as verify
from fc27_stage_loader import run as stage_loader, verify as verify_loader_stage


CONFIG_KEYS = {"game_root", "fc27_export", "sample", "sdk", "shared_types", "codec", "recipe"}


def load_config(path: Path) -> dict[str, Path]:
    config = strict_json(small_file(local_input(path)))
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
        raise ValueError("本机流水线配置字段不完整或包含未知字段")
    paths = {}
    for key, value in config.items():
        if not isinstance(value, str) or not value or "\0" in value:
            raise ValueError("本机配置路径必须是非空字符串")
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if key == "game_root":
            paths[key] = path.resolve(strict=True)
        elif key == "recipe":
            paths[key] = recipe_input(path)
        else:
            paths[key] = local_input(path)
    return paths


def run(config_path: Path, output: Path, module: str = "all", profile: str = "FC27",
        with_loader_stage: bool = False) -> dict:
    config = load_config(config_path)
    game, export = config["game_root"], config["fc27_export"]
    destination = output_path(output, game)
    recipe = strict_json(small_file(config["recipe"]))
    validate_recipe(recipe)
    allowed = {m["id"] for m in recipe["modules"]}
    if module != "all" and module not in allowed:
        raise ValueError("所选模块不在明确研究方案中")
    stage = "study"
    result = {"tool": "fc27-offline-pipeline", "module": module, "completed": False,
              "loadable_mod": False, "game_started_by_tool": False,
              "game_files_written": False, "gameplay_effect_verified": False}
    try:
        result["study"] = study(game, export, config["sample"], config["sdk"], config["shared_types"],
                                 config["codec"], config["recipe"], destination / "study")
        review = strict_json(small_file(destination / "study/study-report.json"))
        selected = review["modules"] if module == "all" else [m for m in review["modules"] if m["id"] == module]
        if any(m["assets_rejected"] or not m["assets_ready"] for m in selected):
            raise ValueError("所选模块存在不兼容资产或没有实际改动，已留研究报告，停止编译")
        stage = "build"
        plan = destination / "study/plans" / ("combined.json" if module == "all" else module + ".json")
        result["build"] = build(game, export, config["sdk"], config["shared_types"], plan, destination / "built")
        stage = "package"
        result["package"] = package(game, destination / "built", export, destination / "packaged", profile)
        stage = "verify"
        result["verification"] = verify(game, destination / "built", export, destination / "packaged")
        if with_loader_stage:
            stage = "loader-stage"
            result["loader_stage"] = stage_loader(game, destination / "built", export,
                                                   destination / "packaged", destination / "loader-stage")
            stage = "verify-loader-stage"
            result["loader_stage_verification"] = verify_loader_stage(
                game, destination / "built", export, destination / "packaged", destination / "loader-stage")
        result.update(completed=True, completed_stage=stage)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        result.update(failed_stage=stage, reason=str(exc))
        if destination.is_dir():
            write_report(destination, result)
        raise
    write_report(destination, result)
    return {"tool": result["tool"], "module": module, "completed": True,
            "assets_built": result["build"]["assets_built"], "changed_values": result["build"]["changed_values"],
            "changed_bytes": result["build"]["changed_bytes"],
            "files_verified": result["verification"]["files_verified"],
            "loader_staged": with_loader_stage,
            "loadable_mod": False, "game_started_by_tool": False,
            "game_files_written": False, "gameplay_effect_verified": False}


def write_report(destination: Path, value: dict):
    with (destination / "pipeline-report.json").open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="项目 local 内的本机路径配置")
    parser.add_argument("--output", required=True, type=Path, help="项目 local 内尚不存在的输出目录")
    parser.add_argument("--module", default="all", help="all 或研究方案中的一个明确模块 id")
    parser.add_argument("--profile", default="FC27", help="未验证加载器映射的研究标签")
    parser.add_argument("--stage-loader", action="store_true", help="同时准备并核对项目内 ModData 副本及原版 CAS 回退")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.config, args.output, args.module, args.profile, args.stage_loader),
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
