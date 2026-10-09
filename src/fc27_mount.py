"""只读解析项目内 ModData 副本与显式原版 CAS 回退；不是游戏加载器。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import re

from fc27_assets import (Location, TOCS, field, input_path, read_bundle, read_toc,
                         safe_relative, small_file)
from fc27_dbobject import Document, FormatError, HEADER_SIZE, MAX_FILE_BYTES
from fc27_index import layout_cas_ids


def mount_relative(name: str) -> str:
    path = safe_relative(name)
    if (path.as_posix() != name or len(path.parts) < 2 or path.parts[0] not in ("Data", "Patch") or
            any(re.search(r'[<>"|?*\x00-\x1f]', part) or part.endswith((".", " ")) or
                re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                for part in path.parts)):
        raise FormatError("加载目录路径不是规范的 Data/Patch 相对路径")
    return name


def plain_file(root: Path, relative: str) -> Path:
    """读取前拒绝目录链接和文件链接，避免回退到未声明的目录。"""
    mount_relative(relative)
    if root.is_symlink() or root.is_junction():
        raise FormatError("加载数据根目录不接受链接")
    current = root
    for part in safe_relative(relative).parts:
        current /= part
        if current.is_symlink() or current.is_junction():
            raise FormatError("加载目录不接受符号链接或目录联接")
    path = input_path(root, relative)
    if not path.is_file():
        raise FormatError("加载输入不是普通文件")
    return path


def header_impact(original: bytes, candidate: bytes) -> dict:
    if (len(original) < HEADER_SIZE or len(candidate) < HEADER_SIZE or
            original[:4] != b"\x00\xd1\xce\x01" or candidate[:4] != original[:4]):
        raise FormatError("不能审查未知或截断的封装头")
    return {"header_bytes": HEADER_SIZE,
            "header_preserved": original[:HEADER_SIZE] == candidate[:HEADER_SIZE],
            "payload_changed": original[HEADER_SIZE:] != candidate[HEADER_SIZE:],
            "signature_region_nonzero": any(original[8:264]),
            "signature_region_preserved": original[8:264] == candidate[8:264],
            "signature_validity_verified": False}


class OfflineMount:
    """仅用于离线读取的两来源解析器；回退清单不等于引擎会自动回退。"""

    def __init__(self, game_root: Path, moddata: Path, staged_names: set[str], fallback: dict[str, dict]):
        self.game_root = game_root.resolve(strict=True)
        self.moddata = moddata.resolve(strict=True)
        self.staged_names = {mount_relative(name) for name in staged_names}
        self.fallback = {mount_relative(name): value for name, value in fallback.items()}
        if self.staged_names & self.fallback.keys():
            raise FormatError("同一路径不能同时声明副本和原版回退")
        self.packages = {}
        self.registered = set()
        self.range_files = {}
        for layer, folder in enumerate(("Data", "Patch")):
            raw = self.read_file(folder + "/layout.toc")
            self.registered.update(layout_cas_ids(raw))
            chunks = field(field(Document.parse(raw).root, "installManifest"), "installChunks")
            for chunk in chunks.children:
                index = field(chunk, "persistentIndex").number_value() & 0xFFFFFFFF
                name = field(chunk, "installBundle").string_value()
                if not name:
                    continue
                mount_relative(folder + "/" + name)
                key = layer, index
                if key in self.packages and self.packages[key] != name:
                    raise FormatError("加载副本存在冲突的安装包映射")
                self.packages[key] = name

    def resolve(self, relative: str) -> tuple[Path, str]:
        mount_relative(relative)
        if relative in self.staged_names:
            return plain_file(self.moddata, relative), "staged"
        if relative not in self.fallback:
            raise FormatError("加载请求没有副本或明确原版回退")
        path = plain_file(self.game_root, relative)
        record, stat = self.fallback[relative], path.stat()
        if stat.st_size != record["bytes"] or stat.st_mtime_ns != record["mtime_ns"]:
            raise FormatError("原版 CAS 回退的尺寸或修改时间已改变")
        return path, "original"

    def read_file(self, relative: str) -> bytes:
        return small_file(self.resolve(relative)[0])

    def relative_cas(self, location: Location) -> str:
        if (location.cas_id not in self.registered or location.cas_id >> 48 not in (0, 1) or
                not location.cas_id & 0xFFFF):
            raise FormatError("CAS 标识不在加载副本的分层注册表中")
        layer, index = location.cas_id >> 48, (location.cas_id >> 16) & 0xFFFFFFFF
        if (layer, index) not in self.packages:
            raise FormatError("CAS 标识没有实际安装包映射")
        return (f"{('Data', 'Patch')[layer]}/{self.packages[layer, index]}/"
                f"cas_{location.cas_id & 0xFFFF:02d}.cas")

    def range_path(self, location: Location) -> tuple[Path, str]:
        if location.offset < 0 or location.size <= 0:
            raise FormatError("CAS 位置范围无效")
        if location.cas_id not in self.range_files:
            path, origin = self.resolve(self.relative_cas(location))
            self.range_files[location.cas_id] = path, origin, path.stat().st_size
        path, origin, size = self.range_files[location.cas_id]
        if location.offset + location.size > size:
            raise FormatError("CAS 引用超出副本或原版文件末尾")
        return path, origin

    def read(self, location: Location) -> bytes:
        if location.size > MAX_FILE_BYTES:
            raise FormatError("CAS 离线读取超过大小上限")
        path, _ = self.range_path(location)
        with path.open("rb") as stream:
            stream.seek(location.offset)
            raw = stream.read(location.size)
        if len(raw) != location.size:
            raise FormatError("CAS 读取期间被截断")
        return raw


def audit_mount(mount: OfflineMount, selected: dict[str, dict]) -> dict:
    origins, paths, verified = Counter(), set(), Counter()
    bundles_count = 0
    for relative in TOCS:
        for bundle in read_toc(mount.read_file(relative)):
            bundles_count += 1
            for location in bundle.locations:
                _, origin = mount.range_path(location)
                origins[origin] += 1
                paths.add(mount.relative_cas(location))
            records = read_bundle(mount.read(bundle.locations[0]), bundle, relative)
            for asset in records:
                if asset.name not in selected:
                    continue
                item = selected[asset.name]
                raw = mount.read(asset.location)
                if (asset.sha1 != item["sha1"] or asset.original_size != item["original_size"] or
                        hashlib.sha1(raw).hexdigest() != item["sha1"] or
                        hashlib.sha256(raw).hexdigest() != item["sha256"]):
                    raise FormatError("加载副本中的选定 EBX 载荷或元数据不符")
                if mount.resolve(mount.relative_cas(asset.location))[1] != "staged":
                    raise FormatError("选定资产仍从原版回退读取")
                verified[asset.name] += 1
    if set(verified) != set(selected):
        raise FormatError("加载目录缺少部分选定资产")
    return {"tocs_audited": list(TOCS), "bundles_audited": bundles_count,
            "bundle_locations_checked": sum(origins.values()), "range_origins": dict(sorted(origins.items())),
            "cas_paths_checked": sorted(paths), "selected_assets_verified": len(verified),
            "selected_memberships_verified": sum(verified.values()),
            "selected_memberships": dict(sorted(verified.items())),
            "selected_payload_hashes_verified": True, "unselected_payload_hashes_verified": False,
            "toc_chunk_tables_audited": False, "other_tocs_parsed": False}
