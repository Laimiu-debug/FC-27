"""公开容器布局、FC27 索引重定位、注册表与本地打包边界测试。"""

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from fc27_assets import Bundle, Location, read_bundle, read_toc
from fc27_candidate import encode_uncompressed_blocks
from fc27_dbobject import Document, FormatError, Node
from fc27_index import add_layout_cas, layout_cas_ids, patch_binary_bundle, patch_toc
from fc27_modcontainer import EbxResource, MAGIC, META_KEYS, read_mod, write_mod
import fc27_package as PACKAGE
import fc27_verify_package as VERIFY_PACKAGE
import fc27_build as BUILD
from test_assets import bundle_fixture, toc_fixture
from test_dbobject import HEADER
import test_edit_build as edit_fixtures


METADATA = dict(zip(META_KEYS, ("中文测试", "author", "Gameplay", "0.1", "离线实验", "")))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.resource = EbxResource("fifa/attribulator/gameplay/test", 3, encode_uncompressed_blocks(b"abc"))
        self.raw = write_mod("FC27", 12345, METADATA, [self.resource])

    def test_exact_public_format_bytes_and_unicode_offsets(self):
        # 独立按公开字段顺序构造黄金字节，不通过被测 writer 计算段位置。
        strings = b"FC27\0" + struct.pack("<I", 12345)
        strings += b"".join(METADATA[k].encode("utf-8") + b"\0" for k in META_KEYS)
        resource = (struct.pack("<iBi", 1, 1, 0) + self.resource.name.encode() + bytes(2)
                    + hashlib.sha1(self.resource.payload).digest() + struct.pack("<qi", 3, 0) + bytes(9))
        expected = (struct.pack("<QIqi", MAGIC, 6, 24 + len(strings) + 20 + len(resource), 1)
                    + strings + hashlib.sha1(resource).digest() + resource
                    + struct.pack("<qi", 0, 11) + self.resource.payload)
        self.assertEqual(self.raw, expected)
        decoded = read_mod(expected)
        self.assertEqual(decoded.metadata, METADATA)
        self.assertEqual(decoded.resources, (self.resource,))

    def test_payload_and_resource_section_corruption_rejected(self):
        section_start = self.raw.index(b"fifa/attribulator/")
        for offset in (len(self.raw) - 1, section_start + len("fifa/attribulator/")):
            raw = bytearray(self.raw)
            raw[offset] ^= 1
            with self.assertRaises(FormatError):
                read_mod(bytes(raw))

    def test_data_table_overlap_negative_size_gap_and_trailing_data_rejected(self):
        data_offset = struct.unpack_from("<q", self.raw, 12)[0]
        for offset, size in ((-1, 11), (1, 11), (0, -1), (0, 10), (0, 12)):
            raw = bytearray(self.raw)
            struct.pack_into("<qi", raw, data_offset, offset, size)
            with self.assertRaises(FormatError):
                read_mod(bytes(raw))
        for raw in (self.raw + b"\0", self.raw[:-1], self.raw[:22]):
            with self.assertRaises(FormatError):
                read_mod(raw)

    def test_wrong_format_and_count_rejected(self):
        for offset, fmt, value in ((0, "<Q", 0), (8, "<I", 5), (20, "<i", -1), (12, "<q", 25)):
            raw = bytearray(self.raw)
            struct.pack_into(fmt, raw, offset, value)
            with self.assertRaises(FormatError):
                read_mod(bytes(raw))

    def test_non_ebx_handlers_and_bundle_operations_rejected_even_with_updated_checksum(self):
        data_offset = struct.unpack_from("<q", self.raw, 12)[0]
        # 资源段以唯一 count 标记开始；SHA1 紧邻其前 20 字节。
        name_start = self.raw.index(self.resource.name.encode())
        section_start = name_start - 9
        flags_offset = name_start + len(self.resource.name) + 1
        for offset, fmt, value in ((section_start + 4, "<B", 2), (flags_offset, "<B", 8),
                                    (flags_offset + 29, "<i", 123), (flags_offset + 34, "<i", 1)):
            raw = bytearray(self.raw)
            struct.pack_into(fmt, raw, offset, value)
            raw[section_start-20:section_start] = hashlib.sha1(raw[section_start:data_offset]).digest()
            with self.assertRaises(FormatError):
                read_mod(bytes(raw))

    def test_unsafe_names_duplicates_and_invalid_metadata_rejected(self):
        for name in ("fifa/attribulator/../test", "fifa/attribulator/test\0x", "fifa/attribulator//test", "other/test"):
            with self.assertRaises(FormatError):
                write_mod("FC27", 1, METADATA, [EbxResource(name, 3, self.resource.payload)])
        for resources in ([], [self.resource, self.resource]):
            with self.assertRaises(FormatError):
                write_mod("FC27", 1, METADATA, resources)
        for profile, head, metadata in (("", 1, METADATA), ("FC27", -1, METADATA),
                                        ("FC27", 1, {}), ("FC27", 1, {**METADATA, "title": "x\0y"})):
            with self.assertRaises(FormatError):
                write_mod(profile, head, metadata, [self.resource])


class IndexTests(unittest.TestCase):
    def test_metadata_changes_only_selected_sha1_and_keeps_other_asset(self):
        raw, bundle = bundle_fixture()
        records = read_bundle(raw, bundle, "Data/test.toc")
        first = records[0]
        changed, indexes = patch_binary_bundle(raw, bundle, "Data/test.toc", {
            first.name: {"expected_sha1": first.sha1, "sha1": "ff" * 20, "original_size": first.original_size}})
        self.assertEqual(indexes, {first.name: 1})
        self.assertEqual(changed[:36], raw[:36])
        self.assertEqual(changed[56:], raw[56:])
        self.assertEqual(read_bundle(changed, bundle, "Data/test.toc")[1], records[1])

    def test_wrong_bundle_baseline_and_empty_selection_rejected(self):
        raw, bundle = bundle_fixture()
        record = read_bundle(raw, bundle, "Data/test.toc")[0]
        for replacements in ({}, {record.name: {"expected_sha1": "00"*20, "sha1": "ff"*20, "original_size": record.original_size}},
                              {record.name: {"expected_sha1": record.sha1, "sha1": "ff"*20, "original_size": 99}}):
            with self.assertRaises(FormatError):
                patch_binary_bundle(raw, bundle, "Data/test.toc", replacements)

    def test_relocation_restores_inherited_id_of_unmodified_following_entry(self):
        raw = toc_fixture()
        before = read_toc(raw)[0]
        locations = list(before.locations)
        locations[0] = Location(0x1A3A00DE30005, 0, 80)
        changed = patch_toc(raw, {0: tuple(locations)})
        after = read_toc(changed)[0]
        self.assertEqual(after.locations, tuple(locations))
        self.assertEqual(after.locations[1:], before.locations[1:])
        # 原始块逐字节保留，只改 16 字节表项中的 size 和 offset。
        self.assertEqual(changed[:620], raw[:620])
        self.assertEqual(changed[632:len(raw)], raw[632:])
        relative = struct.unpack_from(">Q", changed, 624)[0]
        flag_offset = struct.unpack_from(">I", changed, 556 + relative + 8)[0]
        self.assertEqual(changed[556 + relative + flag_offset:][:3], bytes((0x84, 0x80, 0x80)))

    def test_toc_count_invalid_id_and_unknown_header_rejected(self):
        raw = toc_fixture()
        locations = read_toc(raw)[0].locations
        for replacements in ({}, {9: locations}, {0: locations[:-1]},
                              {0: (Location(0, 1, 1),) + locations[1:]}):
            with self.assertRaises(FormatError):
                patch_toc(raw, replacements)
        changed = bytearray(raw)
        struct.pack_into(">I", changed, 632, 1)
        with self.assertRaises(FormatError):
            patch_toc(bytes(changed), {0: locations})

    def test_layout_registry_only_adds_ids_and_retains_other_fields_and_header(self):
        old_id, new_id = 0xA3A00DE30001, 0x1A3A00DE30002
        raw = Document(HEADER, Node(0x82, children=[Node(8, b"head", struct.pack("<i", 123)),
                         Node(19, b"layeredInstallChunkFiles", struct.pack("<Q", old_id)),
                         Node(19, b"unknown", b"opaque")])).compile()
        changed = add_layout_cas(raw, {new_id})
        self.assertEqual(layout_cas_ids(changed), [old_id, new_id])
        doc = Document.parse(changed)
        doc.root.children[1].data = struct.pack("<Q", old_id)
        self.assertEqual(doc.compile(), raw)
        for identifiers in (set(), {old_id}, {0}, {1 << 49 | 1}):
            with self.assertRaises(FormatError):
                add_layout_cas(raw, identifiers)

    def test_malformed_registry_rejected(self):
        for blob in (b"x", bytes(8), struct.pack("<QQ", 1, 1)):
            raw = Document(HEADER, Node(0x82, children=[Node(19, b"layeredInstallChunkFiles", blob)])).compile()
            with self.assertRaises(FormatError):
                layout_cas_ids(raw)


class PackageTests(unittest.TestCase):
    def prepare(self, root):
        # 原始 EBX 和编辑计划沿用合成构建夹具；生成实际可读的 layout/TOC/CAS。
        game, export, sdk, shared, plan_path, schema = edit_fixtures.BuildTests().prepare(root)
        manifest = json.loads((export / "manifest.json").read_bytes())
        entries = manifest["assets"]
        original = (export / "asset0.ebx").read_bytes()
        block = encode_uncompressed_blocks(original)
        names = b"".join(a["name"].encode() + b"\0" for a in entries)
        meta = (struct.pack("<8I", 0xED1CEDB8 ^ 0x7065636E, 2, 2, 0, 0, 88, 0, 0)
                + hashlib.sha1(block).digest()*2 + struct.pack("<4I", 0, len(original),
                  len(entries[0]["name"]) + 1, len(original)) + names)
        meta = struct.pack(">I", len(meta)) + meta
        for layer, folder in enumerate(("Data", "Patch")):
            cas_id = layer << 48 | 0xA3A00DE3 << 16 | 1
            package = "Win32/superbundlelayout/installpackage_02"
            cas = game / folder / package / "cas_01.cas"
            cas.parent.mkdir(parents=True, exist_ok=True)
            cas.write_bytes(meta + block + block)
            chunk = Node(0x82, children=[Node(8, b"persistentIndex", struct.pack("<i", 0xA3A00DE3 - 2**32)),
                         Node(7, b"installBundle", package.encode()+b"\0")])
            doc = Document(HEADER, Node(0x82, children=[Node(8, b"head", struct.pack("<i", 123+layer)),
                         Node(19, b"layeredInstallChunkFiles", struct.pack("<Q", cas_id)),
                         Node(2, b"installManifest", children=[Node(1, b"installChunks", children=[chunk])])]))
            (game / folder / "layout.toc").write_bytes(doc.compile())
            flags = bytes((0x84, 0, 0))
            positions = (struct.pack(">QII", cas_id, 0, len(meta))
                         + struct.pack(">II", len(meta), len(block))
                         + struct.pack(">II", len(meta)+len(block), len(block)))
            location_block = struct.pack(">9I", 0, 0, 36+len(positions), 3, 36, 36, 36, 0, 3) + positions + flags
            toc = (HEADER + struct.pack(">15I", 60, 60, 1, *([0]*12))
                   + struct.pack(">IIQ", 0, 0x40000000|len(location_block), 76) + location_block)
            (game / folder / "Win32/fc/fcgame/fcgame.toc").write_bytes(toc)
        manifest["source_hashes"] = {rel: sha((game / rel).read_bytes()) for rel in BUILD.SOURCE_FILES}
        for entry in entries:
            entry.update(sha1=hashlib.sha1(block).hexdigest(), original_size=len(original))
        manifest_raw = json.dumps(manifest).encode()
        (export / "manifest.json").write_bytes(manifest_raw)
        plan = json.loads(plan_path.read_bytes())
        plan["export_manifest_sha256"] = sha(manifest_raw)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        built = root / "local/built"
        with patch.object(BUILD, "check_shared_source"), patch.object(BUILD, "SharedTypes") as factory:
            factory.return_value.adapt.return_value = schema
            BUILD.run(game, export, sdk, shared, plan_path, built)
        return game, export, built

    def test_real_format_end_to_end_and_disk_corruption_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built = self.prepare(root)
                before = {p.relative_to(game).as_posix(): p.read_bytes() for p in game.rglob("*") if p.is_file()}
                output = root / "local/packaged"
                summary = PACKAGE.run(game, built, export, output, "FC27")
                self.assertEqual((summary["assets_packaged"], summary["bundles_compiled"], summary["cas_archives_created"]), (2, 2, 2))
                self.assertFalse(summary["loadable_mod"])
                self.assertFalse(summary["profile_mapping_verified"])
                self.assertEqual(VERIFY_PACKAGE.run(game, built, export, output)["assets_verified"], 2)
                self.assertEqual(before, {p.relative_to(game).as_posix(): p.read_bytes() for p in game.rglob("*") if p.is_file()})
                with self.assertRaises(FileExistsError):
                    PACKAGE.run(game, built, export, output, "FC27")
                path = output / "candidate.fbmod"
                raw = path.read_bytes()
                path.write_bytes(raw[:-1] + b"x")
                with self.assertRaises(ValueError):
                    VERIFY_PACKAGE.run(game, built, export, output)

    def test_current_index_changed_and_output_inside_game_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built = self.prepare(root)
                with self.assertRaises(ValueError):
                    PACKAGE.run(game, built, export, game / "output", "FC27")
                (game / "Patch/layout.toc").write_bytes(b"updated")
                output = root / "local/package"
                with self.assertRaises(ValueError):
                    PACKAGE.run(game, built, export, output, "FC27")
                self.assertFalse(output.exists())

    def test_unused_cas_number_checks_disk_registry_and_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, _, _ = self.prepare(root)
                catalog = PACKAGE.Catalog(game)
                prefix = 1 << 48 | 0xA3A00DE3 << 16
                folder = game / "Patch/Win32/superbundlelayout/installpackage_02"
                (folder / "cas_05.cas").write_bytes(b"occupied")
                self.assertEqual(PACKAGE.allocate_cas(catalog, 1, 0xA3A00DE3, [prefix|6], {prefix|8}), prefix|9)

    def test_source_payload_corruption_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built = self.prepare(root)
                path = game / "Data/Win32/superbundlelayout/installpackage_02/cas_01.cas"
                raw = path.read_bytes()
                path.write_bytes(raw[:-1] + b"x")
                output = root / "local/package"
                with self.assertRaises(ValueError):
                    PACKAGE.run(game, built, export, output, "FC27")
                self.assertFalse(output.exists())

    def test_package_report_compatibility_claim_and_extra_file_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(BUILD, "PROJECT_ROOT", root), patch("fc27_export.PROJECT_ROOT", root):
                game, export, built = self.prepare(root)
                output = root / "local/package"
                PACKAGE.run(game, built, export, output, "FC27")
                report_path = output / "package-report.json"
                raw = report_path.read_bytes()
                report = json.loads(raw)
                report["summary"]["loadable_mod"] = True
                report_path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaises(ValueError):
                    VERIFY_PACKAGE.run(game, built, export, output)
                report_path.write_bytes(raw)
                (output / "unexpected.txt").write_text("extra")
                with self.assertRaises(ValueError):
                    VERIFY_PACKAGE.run(game, built, export, output)


if __name__ == "__main__":
    unittest.main()
