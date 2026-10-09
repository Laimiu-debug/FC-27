"""资源位置、压缩长度、散列与新建输出边界的合成测试。"""

import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from fc27_assets import (Asset, Bundle, FormatError, Location, OodleDecoder,
                         decompress_cas, export_asset, read_bundle, read_toc, safe_relative)
from fc27_export import output_path


def cas_block(data: bytes, codec=0) -> bytes:
    compressed = zlib.compress(data) if codec == 2 else data
    return struct.pack(">Q", len(data) << 32 | codec << 24 | 7 << 20 | len(compressed)) + compressed


def toc_fixture(flags=(0x84, 0, 0x80)) -> bytes:
    identifiers = (0xA3A00DE30001, 0x1A3A00DE30002)
    entries = bytearray()
    next_identifier = iter(identifiers)
    for flag in flags:
        if flag & 0x80:
            entries.extend(struct.pack(">Q", next(next_identifier)))
        entries.extend(struct.pack(">2I", 100, 200))
    count = len(flags)
    block = struct.pack(">9I", 0, 0, 36 + len(entries), count, 36, 36, 36, 0, count)
    block += entries + bytes(flags)
    header = [60, 60, 1] + [0]*12
    return (b"\x00\xd1\xce\x01" + bytes(552) + struct.pack(">15I", *header)
            + struct.pack(">IIQ", 0, 0x40000000 | len(block), 76) + block)


def bundle_fixture() -> tuple[bytes, Bundle]:
    names = b"fifa/attribulator/gameplay/a\0fifa/attribulator/gameplay/b\0"
    strings = 36 + 40 + 16
    header = struct.pack("<8I", 0xED1CEDB8 ^ 0x7065636E, 2, 2, 0, 0, strings - 4, 0, 0)
    raw = header + bytes(range(40)) + struct.pack("<4I", 0, 12, names.index(b"fifa", 1), 16) + names
    raw = struct.pack(">I", len(raw)) + raw
    locations = tuple(Location(0xA3A00DE30001, i*100, 50) for i in range(3))
    return raw, Bundle(7, locations)


class TocTests(unittest.TestCase):
    def test_new_id_flags_are_both_eight_bytes_and_zero_inherits(self):
        bundles = read_toc(toc_fixture())
        self.assertEqual(len(bundles), 1)
        self.assertEqual([l.cas_id for l in bundles[0].locations],
                         [0xA3A00DE30001, 0xA3A00DE30001, 0x1A3A00DE30002])

    def test_missing_initial_id_unknown_flag_and_truncation_rejected(self):
        for data in (toc_fixture((0,)), toc_fixture((1,)), toc_fixture()[:-1]):
            with self.subTest(data=data[-5:]), self.assertRaises(FormatError):
                read_toc(data)

    def test_bundle_names_sizes_and_cas_entry_correspondence(self):
        data, bundle = bundle_fixture()
        assets = read_bundle(data, bundle, "Data/example.toc")
        self.assertEqual([a.name for a in assets],
                         ["fifa/attribulator/gameplay/a", "fifa/attribulator/gameplay/b"])
        self.assertEqual(assets[1].original_size, 16)
        self.assertEqual(assets[1].location, bundle.locations[2])
        self.assertEqual(assets[0].sha1, bytes(range(20)).hex())

    def test_bundle_count_and_string_range_must_match(self):
        data, bundle = bundle_fixture()
        bad = bytearray(data)
        struct.pack_into("<I", bad, 24, len(data) + 100)
        for raw, descriptor in ((bytes(bad), bundle), (data, Bundle(0, bundle.locations[:-1]))):
            with self.assertRaises(FormatError):
                read_bundle(raw, descriptor, "Data/example.toc")


class CompressionTests(unittest.TestCase):
    def test_multiple_blocks_and_zlib(self):
        data = cas_block(b"abc") + cas_block(b"defgh", 2)
        self.assertEqual(decompress_cas(data, 8), b"abcdefgh")

    def test_lengths_truncation_and_unknown_codec_rejected(self):
        for data, size in ((cas_block(b"abc"), 2), (cas_block(b"abc")[:-1], 3),
                           (cas_block(b"abc"), 4), (cas_block(b"abc", 0x19), 3)):
            with self.assertRaises(FormatError):
                decompress_cas(data, size)

    def test_zlib_expansion_limited_by_declared_size(self):
        raw = bytearray(cas_block(b"a"*10000, 2))
        raw[:4] = struct.pack(">I", 10)
        with self.assertRaises(FormatError):
            decompress_cas(bytes(raw), 10)

    def test_asset_sha1_checked_before_decompression(self):
        class FakeCatalog:
            def read(self, location):
                return cas_block(b"abc")
        location = Location(1, 0, 11)
        bad = Asset("example", 3, "00"*20, location, "toc", 0)
        with self.assertRaises(FormatError):
            export_asset(FakeCatalog(), bad)
        good = Asset("example", 3, hashlib.sha1(cas_block(b"abc")).hexdigest(), location, "toc", 0)
        self.assertEqual(export_asset(FakeCatalog(), good), b"abc")

    def test_unverified_codec_never_loaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "untrusted.dll"
            path.write_bytes(b"not a library")
            with patch("fc27_assets.ctypes.CDLL") as loader, self.assertRaises(ValueError):
                OodleDecoder(path, root / "game")
            loader.assert_not_called()


class ExportBoundaryTests(unittest.TestCase):
    def test_output_directory_game_boundary_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "local/game"
            game.mkdir(parents=True)
            with patch("fc27_export.PROJECT_ROOT", root):
                accepted = root / "local/new"
                self.assertEqual(output_path(accepted, game), accepted.resolve())
                for target in (root / "outside", root / "local", game / "dump"):
                    with self.assertRaises(ValueError):
                        output_path(target, game)
                accepted.mkdir()
                with self.assertRaises(FileExistsError):
                    output_path(accepted, game)

    def test_resource_names_cannot_escape_output(self):
        for name in ("../outside", "C:/outside", "/absolute", "a\\b", ""):
            with self.assertRaises(FormatError):
                safe_relative(name)


if __name__ == "__main__":
    unittest.main()
