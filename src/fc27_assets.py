"""只读定位 FC27 当前格式的 Bundle 与玩法 EBX；不处理部署或补丁合成。"""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
import ctypes
import hashlib
from pathlib import Path, PurePosixPath
import struct
import zlib

from fc27_dbobject import Document, FormatError, HEADER_SIZE, MAX_FILE_BYTES


ATTRIB_PREFIX = "fifa/attribulator/"
TOCS = ("Data/Win32/fc/fcgame/fcgame.toc", "Patch/Win32/fc/fcgame/fcgame.toc")
MAX_ENTRIES = 1_000_000
# 来自已检查 ZIP CRC 的公开 FMT 发行包；只加载显式指定的这一份解压库。
OODLE_SHA256 = "ca9015662ac0a9a29be4ce5f0d6eacd239ebc893619f39e9972559d73bcf2c0a"


def checked_range(data: bytes, start: int, count: int) -> bytes:
    if start < 0 or count < 0 or start + count > len(data):
        raise FormatError("资源索引截断或范围越界")
    return data[start:start + count]


def safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise FormatError("资源路径不安全")
    return path


def input_path(root: Path, relative: str) -> Path:
    path = (root / safe_relative(relative)).resolve(strict=True)
    if not path.is_relative_to(root.resolve(strict=True)):
        raise FormatError("输入文件解析后超出游戏目录")
    return path


def small_file(path: Path) -> bytes:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise FormatError("元数据超过读取上限")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise FormatError("读取期间元数据超过上限")
    return data


def field(node, name: str):
    matches = [c for c in node.children if c.name == name.encode("ascii")]
    if len(matches) != 1:
        raise FormatError(f"资源目录缺少唯一字段：{name}")
    return matches[0]


@dataclass(frozen=True)
class Location:
    cas_id: int
    offset: int
    size: int


@dataclass(frozen=True)
class Bundle:
    index: int
    locations: tuple[Location, ...]


def read_toc(data: bytes) -> list[Bundle]:
    if checked_range(data, 0, 4) != b"\x00\xd1\xce\x01":
        raise FormatError("不支持的 TOC 封装")
    header = struct.unpack(">15I", checked_range(data, HEADER_SIZE, 60))
    if header[0] != 60 or header[2] > MAX_ENTRIES:
        raise FormatError("不支持的 TOC 头或过多 Bundle")
    table = checked_range(data, HEADER_SIZE + header[1], header[2] * 16)
    result = []
    for i in range(header[2]):
        _, packed_size, relative = struct.unpack_from(">IIQ", table, i * 16)
        if packed_size >> 30 != 1:
            raise FormatError("不支持的 Bundle 标记")
        block = checked_range(data, HEADER_SIZE + relative, packed_size & 0x3FFFFFFF)
        head = struct.unpack(">9I", checked_range(block, 0, 36))
        _, _, flags_offset, count, entries_offset, size, _, _, repeated_count = head
        if size != 36 or entries_offset != 36 or count != repeated_count or not 1 <= count <= MAX_ENTRIES:
            raise FormatError("不支持的 FC27 CAS Bundle 头")
        flags = checked_range(block, flags_offset, count)
        if flags_offset + count != len(block):
            raise FormatError("Bundle 长度与标记表不一致")
        position = entries_offset
        cas_id = None
        locations = []
        for flag in flags:
            if flag in (0x80, 0x84):
                cas_id = int.from_bytes(checked_range(block, position, 8), "big")
                position += 8
            elif flag != 0:
                raise FormatError(f"不支持的 CAS 位置标记：{flag:#x}")
            if cas_id is None or cas_id >> 48 not in (0, 1) or not cas_id & 0xFFFF:
                raise FormatError("缺少有效 CAS 标识")
            offset, length = struct.unpack(">II", checked_range(block, position, 8))
            position += 8
            if position > flags_offset or length == 0:
                raise FormatError("CAS 位置表越界或空范围")
            locations.append(Location(cas_id, offset, length))
        if position != flags_offset:
            raise FormatError("CAS 位置表没有完整消费")
        result.append(Bundle(i, tuple(locations)))
    return result


@dataclass(frozen=True)
class Asset:
    name: str
    original_size: int
    sha1: str
    location: Location
    toc: str
    bundle_index: int

    def record(self, catalog: Catalog) -> dict:
        return {
            "name": self.name, "original_size": self.original_size, "sha1": self.sha1,
            "toc": self.toc, "bundle_index": self.bundle_index,
            "cas": catalog.relative_cas(self.location),
            "offset": self.location.offset, "size": self.location.size,
        }


def read_bundle(data: bytes, bundle: Bundle, toc: str) -> list[Asset]:
    if int.from_bytes(checked_range(data, 0, 4), "big") + 4 != len(data):
        raise FormatError("BinaryBundle 元数据长度不一致")
    magic = checked_range(data, 4, 4)
    endian = next((e for e in ("<", ">")
                   if struct.unpack(e + "I", magic)[0] ^ 0x7065636E == 0xED1CEDB8), None)
    if endian is None:
        raise FormatError("仅支持带 SHA1 的明文 Standard BinaryBundle")
    count, ebx, res, chunks, strings, _, _ = struct.unpack(
        endian + "7I", checked_range(data, 8, 28))
    if count > MAX_ENTRIES or count != ebx + res + chunks or count + 1 != len(bundle.locations):
        raise FormatError("Bundle 资源数与 CAS 位置数不一致")
    table_start = 36 + 20 * count
    table_end = table_start + 8 * ebx + 36 * res + 24 * chunks
    checked_range(data, 36, table_end - 36)
    strings += 4
    if not table_end <= strings <= len(data):
        raise FormatError("Bundle 字符串表越界")
    result = []
    for i in range(ebx):
        name_offset, original_size = struct.unpack_from(endian + "2I", data, table_start + 8 * i)
        start = strings + name_offset
        checked_range(data, start, 1)
        end = data.find(b"\0", start, min(len(data), start + 4097))
        if end < 0:
            raise FormatError("资源名截断或过长")
        try:
            name = data[start:end].decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise FormatError("资源名不是 UTF-8") from exc
        safe_relative(name)
        if not 0 < original_size <= MAX_FILE_BYTES:
            # 非目标资产不解压，但其尺寸仍记录为真实整数。
            if name.startswith(ATTRIB_PREFIX):
                raise FormatError("玩法资产超过解压上限")
        result.append(Asset(name, original_size, data[36 + 20*i:56 + 20*i].hex(),
                            bundle.locations[i + 1], toc, bundle.index))
    return result


class Catalog:
    def __init__(self, game_root: Path):
        self.root = game_root.resolve(strict=True)
        self.packages: dict[tuple[int, int], str] = {}
        self.source_hashes: dict[str, str] = {}
        for layer, folder in enumerate(("Data", "Patch")):
            relative = f"{folder}/layout.toc"
            raw = small_file(input_path(self.root, relative))
            self.source_hashes[relative] = hashlib.sha256(raw).hexdigest()
            chunks = field(field(Document.parse(raw).root, "installManifest"), "installChunks")
            for chunk in chunks.children:
                index = field(chunk, "persistentIndex").number_value() & 0xFFFFFFFF
                name = field(chunk, "installBundle").string_value()
                # layout 也列出没有磁盘安装目录的逻辑分块；它们不能提供 CAS 路径。
                if not name:
                    continue
                safe_relative(name)
                key = layer, index
                if key in self.packages and self.packages[key] != name:
                    raise FormatError("CAS 目录标识冲突")
                self.packages[key] = name

    def relative_cas(self, location: Location) -> str:
        layer = location.cas_id >> 48
        index = (location.cas_id >> 16) & 0xFFFFFFFF
        try:
            package = self.packages[layer, index]
        except KeyError as exc:
            raise FormatError("未知 CAS 安装目录标识") from exc
        return f"{('Data', 'Patch')[layer]}/{package}/cas_{location.cas_id & 0xFFFF:02d}.cas"

    def read(self, location: Location) -> bytes:
        if not 0 < location.size <= MAX_FILE_BYTES or location.offset < 0:
            raise FormatError("CAS 读取范围不合法或过大")
        path = input_path(self.root, self.relative_cas(location))
        with path.open("rb") as stream:
            stream.seek(0, 2)
            if location.offset + location.size > stream.tell():
                raise FormatError("CAS 范围超过文件末尾")
            stream.seek(location.offset)
            raw = stream.read(location.size)
        if len(raw) != location.size:
            raise FormatError("读取期间 CAS 文件被截断")
        return raw

    def scan_attrib(self) -> tuple[list[Asset], dict]:
        effective: dict[str, Asset] = {}
        stats = {"bundles": 0, "ebx_records": 0, "attrib_records": 0, "patch_changed_assets": 0}
        for relative in TOCS:
            raw = small_file(input_path(self.root, relative))
            self.source_hashes[relative] = hashlib.sha256(raw).hexdigest()
            per_layer = {}
            for bundle in read_toc(raw):
                stats["bundles"] += 1
                records = read_bundle(self.read(bundle.locations[0]), bundle, relative)
                stats["ebx_records"] += len(records)
                for asset in records:
                    if not asset.name.startswith(ATTRIB_PREFIX):
                        continue
                    stats["attrib_records"] += 1
                    if asset.name in per_layer and per_layer[asset.name].sha1 != asset.sha1:
                        raise FormatError("同层玩法资产有不同内容，拒绝猜测生效版本")
                    per_layer[asset.name] = asset
            if relative.startswith("Patch/"):
                stats["patch_changed_assets"] = sum(
                    n in effective and effective[n].sha1 != a.sha1 for n, a in per_layer.items())
            effective.update(per_layer)
        stats["unique_attrib_assets"] = len(effective)
        stats["asset_domains"] = dict(Counter(n.split("/")[2] for n in sorted(effective)))
        return [effective[n] for n in sorted(effective)], stats


class OodleDecoder:
    """可选本地解压依赖；不从游戏目录加载任何可执行文件。"""

    def __init__(self, library: Path, game_root: Path):
        path = library.resolve(strict=True)
        if path.is_relative_to(game_root.resolve()):
            raise ValueError("解压库必须来自游戏目录之外的已校验研究依赖")
        if hashlib.sha256(small_file(path)).hexdigest() != OODLE_SHA256:
            raise ValueError("解压库 SHA256 与已校验的 FMT 公开依赖不符")
        self.library = ctypes.CDLL(str(path))
        self.function = self.library.OodleLZ_Decompress
        self.function.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.c_int]
        self.function.restype = ctypes.c_size_t

    def __call__(self, compressed: bytes, size: int) -> bytes:
        if not 0 < size <= MAX_FILE_BYTES:
            raise FormatError("解压目标大小越界")
        source = ctypes.create_string_buffer(compressed)
        target = ctypes.create_string_buffer(size)
        written = self.function(source, len(compressed), target, size, 1, 1, 0,
                                None, 0, None, None, None, 0, 3)
        if written != size:
            raise FormatError("Oodle 解压返回长度不一致")
        return target.raw


def decompress_cas(data: bytes, original_size: int, oodle=None) -> bytes:
    if not 0 < original_size <= MAX_FILE_BYTES:
        raise FormatError("解压资产大小越界")
    output = bytearray()
    position = 0
    while position < len(data):
        packed = int.from_bytes(checked_range(data, position, 8), "big")
        position += 8
        flags, size, codec = packed >> 56, (packed >> 32) & 0xFFFFFF, (packed >> 24) & 0xFF
        compressed_size = packed & 0xFFFFF
        if flags or (packed >> 20) & 15 != 7 or size == 0:
            raise FormatError("不支持的 CAS 压缩块头、字典或混淆标记")
        if len(output) + size > original_size:
            raise FormatError("压缩块声明长度超过资产大小")
        if codec == 0:
            compressed_size = size
        compressed = checked_range(data, position, compressed_size)
        position += compressed_size
        if codec == 0:
            decoded = compressed
        elif codec == 2:
            inflater = zlib.decompressobj()
            try:
                decoded = inflater.decompress(compressed, size + 1)
            except zlib.error as exc:
                raise FormatError("ZLib 数据损坏") from exc
            if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
                raise FormatError("ZLib 数据长度或结束标记异常")
        elif codec in (0x11, 0x15, 0x19) and oodle is not None:
            decoded = oodle(compressed, size)
        else:
            raise FormatError(f"不支持的压缩方式或缺少解压依赖：{codec:#x}")
        if len(decoded) != size:
            raise FormatError("压缩块实际解压长度不符")
        output.extend(decoded)
    if len(output) != original_size:
        raise FormatError("资产实际解压长度不符")
    return bytes(output)


def export_asset(catalog: Catalog, asset: Asset, oodle=None) -> bytes:
    packed = catalog.read(asset.location)
    if hashlib.sha1(packed).hexdigest() != asset.sha1:
        raise FormatError("CAS 数据 SHA1 与 Bundle 索引不一致")
    return decompress_cas(packed, asset.original_size, oodle)
