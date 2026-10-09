"""模组登记、合并冲突及仅在项目内的可恢复文件事务。"""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import fc27_management as CORE
import fc27_manager as MANAGER
import fc27_build as BUILD
import fc27_stage_loader as STAGE
import test_loader_stage as stage_fixtures


def plan(field="Value[0]", value=0.8):
    return {"format": "fc27-fixed-edit-plan-v1", "export_manifest_sha256": "1" * 64,
            "sdk_sha256": "2" * 64, "shared_types_sha256": "3" * 64, "assets": [{
                "name": "fifa/attribulator/gameplay/test", "expected_sha256": "4" * 64,
                "root_identity": {"guid": "00000000-0000-0000-0000-000000000001", "signature": "00000001"},
                "edits": [{"field": field, "expected_hex": "0000803f", "value": value}]}]}


class MergeTests(unittest.TestCase):
    def test_disjoint_fields_merge_and_identical_edits_deduplicate(self):
        first, second = plan(), plan("Value[1]")
        saved = copy.deepcopy(first)
        result = CORE.merge_plans([first, second, first])
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual([e["field"] for e in result["assets"][0]["edits"]], ["Value[0]", "Value[1]"])
        self.assertEqual(first, saved)

    def test_same_field_with_different_value_or_expectation_rejected(self):
        for second in (plan(value=0.9), plan()):
            if second["assets"][0]["edits"][0]["value"] == 0.8:
                second["assets"][0]["edits"][0]["expected_hex"] = "00000000"
            with self.assertRaises(ValueError):
                CORE.merge_plans([plan(), second])

    def test_wrong_versions_type_identity_or_duplicate_asset_rejected(self):
        for mutate in (lambda p: p.update(sdk_sha256="5" * 64),
                       lambda p: p["assets"][0]["root_identity"].update(signature="00000002"),
                       lambda p: p["assets"].append(copy.deepcopy(p["assets"][0]))):
            second = plan()
            mutate(second)
            with self.assertRaises(ValueError):
                CORE.merge_plans([plan(), second])

    def test_invalid_identifiers_paths_and_outside_project_rejected(self):
        for value in ("../bad", "A", "con", "com1", "x" * 49, "two words"):
            with self.assertRaises(ValueError):
                CORE.identifier(value)
        for value in ("Data//x", "Data/../x", "Data/CON", "Data/x ", "Data/x\0", "Data/x:y"):
            with self.assertRaises(ValueError):
                CORE.relative(value)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                CORE.project_local(root / "outside", root, exists=False)
            with self.assertRaises(ValueError):
                CORE.project_local(root / "local/game/sub", root, root / "local/game", exists=False)

    def test_operation_lock_serializes_mutation_and_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CORE.operation_lock(root):
                with self.assertRaises(ValueError):
                    with CORE.operation_lock(root):
                        self.fail("不能同时取得操作锁")
            with CORE.operation_lock(root):
                pass

    def test_linked_local_root_is_rejected_before_resolving_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = Path.is_symlink
            def linked(path):
                return path == root / "local" or original(path)
            with patch.object(Path, "is_symlink", new=linked), self.assertRaises(ValueError):
                CORE.project_local(root / "local/manager", root, exists=False)


class ManagerTests(unittest.TestCase):
    def context(self, root):
        game, export, built, package = stage_fixtures.LoaderStageTests().context(root)
        stage = root / "local/staged"
        STAGE.run(game, built, export, package, stage)
        config = root / "local/config.json"
        config.write_text(json.dumps({key: str(game if key == "game_root" else export)
                                      for key in MANAGER.CONFIG_KEYS}), encoding="utf-8")
        manager_root = root / "local/manager"
        MANAGER.initialize(manager_root, config)
        manager = MANAGER.Manager(manager_root)
        paths = {"bundle": built, "export": export, "package": package, "stage": stage}
        manager.register("sample", "合成诊断候选", paths)
        return manager, game, paths

    def test_register_preflight_rehearse_and_restore_preserve_game(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(MANAGER, "PROJECT_ROOT", root), patch.object(BUILD, "PROJECT_ROOT", root), \
                    patch("fc27_export.PROJECT_ROOT", root):
                manager, game, paths = self.context(root)
                before = CORE.snapshot_tree(game)
                self.assertEqual(manager.listing()["count"], 1)
                with self.assertRaises(FileExistsError):
                    manager.register("sample", "重复", paths)
                entry, checked = manager.check("sample")
                self.assertEqual(checked["selected_assets_verified"], 2)
                applied = manager.rehearse("sample", "trial")
                self.assertEqual(applied["state"], "applied")
                self.assertEqual(applied["modified_files"], 6)
                with self.assertRaises(FileExistsError):
                    manager.rehearse("sample", "trial")
                restored = manager.restore("trial")
                self.assertTrue(restored["baseline_verified"])
                self.assertEqual(manager.rehearsal_status("trial")["state"], "restored")
                self.assertEqual(manager.restore("trial"), restored)
                self.assertEqual(before, CORE.snapshot_tree(game))
                self.assertFalse(restored["game_files_written"])
                self.assertFalse(entry["safety"]["loadable_mod"])

    def test_shared_index_files_require_recompile_even_for_identical_plans(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(MANAGER, "PROJECT_ROOT", root), patch.object(BUILD, "PROJECT_ROOT", root), \
                    patch("fc27_export.PROJECT_ROOT", root):
                manager, _, paths = self.context(root)
                manager.register("sample-two", "同一候选的第二登记", paths)
                preview = manager.preview(["sample", "sample-two"])
                self.assertTrue(preview["plans_mergeable"])
                self.assertTrue(preview["requires_recompile"])
                self.assertFalse(preview["compiled_packages_can_be_stacked"])
                self.assertEqual(len(preview["shared_output_paths"]), 6)
                self.assertEqual(preview["merged_edits"], 2)
                with self.assertRaises(ValueError):
                    manager.preview(["sample", "sample"])

    def test_current_version_or_registration_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(MANAGER, "PROJECT_ROOT", root), patch.object(BUILD, "PROJECT_ROOT", root), \
                    patch("fc27_export.PROJECT_ROOT", root):
                manager, game, _ = self.context(root)
                entry_path = manager.root / "library/sample.json"
                original = entry_path.read_bytes()
                wrapper = json.loads(original)
                wrapper["content"]["stats"]["changed_values"] = 999
                entry_path.write_text(json.dumps(wrapper), encoding="utf-8")
                with self.assertRaises(ValueError):
                    manager.listing()
                entry_path.write_bytes(original)
                (game / "Patch/layout.toc").write_bytes(b"version changed")
                with self.assertRaises(ValueError):
                    manager.check("sample")
                output = manager.root / "rehearsals/blocked"
                with self.assertRaises(ValueError):
                    manager.rehearse("sample", "blocked")
                self.assertFalse(output.exists())

    def test_changed_backup_or_target_is_never_overwritten_on_restore(self):
        for choice in ("backup", "target/ModData"):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with patch.object(MANAGER, "PROJECT_ROOT", root), patch.object(BUILD, "PROJECT_ROOT", root), \
                        patch("fc27_export.PROJECT_ROOT", root):
                    manager, _, _ = self.context(root)
                    manager.rehearse("sample", "trial")
                    run = manager.rehearsal_path("trial")
                    (run / choice / "Data/layout.toc").write_bytes(b"manual edit")
                    current = CORE.snapshot_tree(run / "target/ModData")
                    with self.assertRaises(ValueError):
                        manager.restore("trial")
                    self.assertEqual(CORE.snapshot_tree(run / "target/ModData"), current)

    def test_interrupted_apply_can_restore_and_keep_complete_backups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(MANAGER, "PROJECT_ROOT", root), patch.object(BUILD, "PROJECT_ROOT", root), \
                    patch("fc27_export.PROJECT_ROOT", root):
                manager, _, _ = self.context(root)
                original = MANAGER.replace_bytes
                calls = 0
                def interrupted(path, raw):
                    nonlocal calls
                    if path.name != "transaction.json":
                        calls += 1
                        if calls == 2:
                            raise OSError("simulated interruption")
                    original(path, raw)
                with patch.object(MANAGER, "replace_bytes", side_effect=interrupted), self.assertRaises(OSError):
                    manager.rehearse("sample", "trial")
                self.assertEqual(manager.rehearsal_status("trial")["state"], "applying")
                self.assertTrue(manager.restore("trial")["baseline_verified"])

    def test_interrupted_restore_is_idempotently_resumable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(MANAGER, "PROJECT_ROOT", root), patch.object(BUILD, "PROJECT_ROOT", root), \
                    patch("fc27_export.PROJECT_ROOT", root):
                manager, _, _ = self.context(root)
                manager.rehearse("sample", "trial")
                original = CORE.replace_bytes
                calls = 0
                def interrupted(path, raw):
                    nonlocal calls
                    if path.name != "transaction.json":
                        calls += 1
                        if calls == 2:
                            raise OSError("simulated interruption")
                    original(path, raw)
                with patch.object(CORE, "replace_bytes", side_effect=interrupted), self.assertRaises(OSError):
                    manager.restore("trial")
                self.assertEqual(manager.rehearsal_status("trial")["state"], "restoring")
                self.assertTrue(manager.restore("trial")["baseline_verified"])

    def test_forged_journal_even_with_matching_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(MANAGER, "PROJECT_ROOT", root), patch.object(BUILD, "PROJECT_ROOT", root), \
                    patch("fc27_export.PROJECT_ROOT", root):
                manager, _, _ = self.context(root)
                manager.rehearse("sample", "trial")
                run = manager.rehearsal_path("trial")
                target = run / "target/ModData/Data/locale.ini"
                target.write_bytes(b"forged data")
                record_path = run / "transaction.json"
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["baseline"]["Data/locale.ini"] = record["applied"]["Data/locale.ini"] = {
                    "bytes": 11, "sha256": CORE.sha(b"forged data")}
                record_path.write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaises(ValueError):
                    manager.restore("trial")
                self.assertEqual(target.read_bytes(), b"forged data")

    def test_linked_target_parent_is_rejected_before_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(MANAGER, "PROJECT_ROOT", root), patch.object(BUILD, "PROJECT_ROOT", root), \
                    patch("fc27_export.PROJECT_ROOT", root):
                manager, _, _ = self.context(root)
                manager.rehearse("sample", "trial")
                run = manager.rehearsal_path("trial")
                before = CORE.snapshot_tree(run / "target/ModData")
                original = Path.is_junction
                def linked(path):
                    return path == run / "target" or original(path)
                with patch.object(Path, "is_junction", new=linked), self.assertRaises(ValueError):
                    manager.restore("trial")
                self.assertEqual(CORE.snapshot_tree(run / "target/ModData"), before)


if __name__ == "__main__":
    unittest.main()
