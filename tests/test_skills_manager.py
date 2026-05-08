from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from skills_manager import adapters, backup, cli, materializer, planner, presets, resolver, scanner, store, transactions


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "skills-manager"


def make_skill(path: Path, body: str = "# Skill\n\nDo the thing.\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(body, encoding="utf-8")
    return path


class TempHomeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.old_env = os.environ.copy()
        os.environ["SKILLS_MANAGER_HOME"] = str(self.home)
        os.environ.pop("SKILLS_MANAGER_STORE", None)
        os.environ.pop("CODEX_HOME", None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def test_scan_detects_skill_symlink_broken_and_missing_skill_md(self) -> None:
        inbox = store.inbox_dir()
        make_skill(inbox / "normal")
        (inbox / "missing").mkdir(parents=True)
        (inbox / "link").symlink_to(inbox / "normal", target_is_directory=True)
        (inbox / "broken").symlink_to(inbox / "nope", target_is_directory=True)

        entries = {entry["name"]: entry["type"] for entry in scanner.scan_dir(inbox)["entries"]}

        self.assertEqual(entries["normal"], "skill_dir")
        self.assertEqual(entries["missing"], "missing_skill_md")
        self.assertEqual(entries["link"], "symlink_skill")
        self.assertEqual(entries["broken"], "broken_symlink")

    def test_import_dry_run_detects_unmanaged_inbox_skill(self) -> None:
        make_skill(store.inbox_dir() / "eli5")

        result = planner.import_inbox(dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual([c["name"] for c in result["candidates"]], ["eli5"])

    def test_adopt_copies_skill_and_does_not_enable(self) -> None:
        src = make_skill(self.home / "source" / "eli5")

        result = planner.adopt_skill(src)
        state = resolver.resolve("codex")

        self.assertTrue(result["ok"])
        self.assertTrue((store.skills_root() / "skill.davidl.eli5" / "SKILL.md").exists())
        meta = json.loads((store.skills_root() / "skill.davidl.eli5" / "skill.json").read_text())
        self.assertEqual(meta["aliases"]["codex"], "eli5")
        self.assertEqual(state["desired"], {})

    def test_migrate_dry_run_merges_identical_skills(self) -> None:
        make_skill(self.home / ".claude" / "skills" / "same", "identical\n")
        make_skill(self.home / ".codex" / "skills" / "same", "identical\n")

        plan = planner.migrate_plan()

        self.assertEqual(len(plan["actions"]), 1)
        self.assertEqual(plan["actions"][0]["kind"], "merge")
        self.assertTrue(plan["actions"][0]["compatibility"]["claude"])
        self.assertTrue(plan["actions"][0]["compatibility"]["codex"])

    def test_migrate_dry_run_forks_same_name_different_content(self) -> None:
        make_skill(self.home / ".claude" / "skills" / "same", "claude\n")
        make_skill(self.home / ".codex" / "skills" / "same", "codex\n")

        plan = planner.migrate_plan()

        self.assertEqual([a["kind"] for a in plan["actions"]], ["fork", "fork"])
        self.assertEqual({a["skill_id"] for a in plan["actions"]}, {"skill.davidl.same.claude", "skill.davidl.same.codex"})

    def test_migrate_apply_copies_without_moving_originals(self) -> None:
        src = make_skill(self.home / ".claude" / "skills" / "one")

        result = planner.migrate_apply()

        self.assertTrue(result["applied"][0]["ok"])
        self.assertTrue(src.exists())
        self.assertTrue((store.skills_root() / "skill.davidl.one" / "SKILL.md").exists())

    def test_resolver_global_enable_and_project_disable(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        project = self.home / "project"
        resolver.set_skill("project", "skill.davidl.eli5", False, project=project)

        global_state = resolver.resolve("claude")
        project_state = resolver.resolve("claude", project=project)

        self.assertIn("skill.davidl.eli5", global_state["desired"])
        self.assertNotIn("skill.davidl.eli5", project_state["desired"])
        self.assertIn("project:disable", project_state["skills"]["skill.davidl.eli5"]["reasons"])

    def test_resolver_ignores_stale_profile_manifest(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        profile_path = store.manifests_root() / "profiles" / "default.json"
        store.write_json(profile_path, store.manifest_template(enable=["skill.davidl.eli5"]))

        state = resolver.resolve("claude")

        self.assertNotIn("skill.davidl.eli5", state["desired"])
        self.assertEqual([], state["skills"]["skill.davidl.eli5"]["reasons"])

    def test_core_rejects_profile_scope(self) -> None:
        with self.assertRaises(ValueError):
            store.manifest_path("profile")

        with self.assertRaises(ValueError):
            resolver.set_skill("profile", "skill.davidl.eli5", True)

    def test_session_disable_overrides_global_enable(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        resolver.set_skill("session", "skill.davidl.eli5", False)

        state = resolver.resolve("codex")

        self.assertNotIn("skill.davidl.eli5", state["desired"])
        self.assertIn("session:disable", state["skills"]["skill.davidl.eli5"]["reasons"])

    def test_client_specific_masks_affect_only_one_client(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        resolver.set_skill("global", "skill.davidl.eli5", False, client="codex")

        self.assertIn("skill.davidl.eli5", resolver.resolve("claude")["desired"])
        self.assertNotIn("skill.davidl.eli5", resolver.resolve("codex")["desired"])

    def test_materialize_dry_run_reports_changes_without_writing(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)

        result = materializer.materialize("claude", dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["actions"][0]["op"], "create_symlink")
        self.assertFalse((self.home / ".claude" / "skills" / "eli5").exists())

    def test_materialize_refuses_unmanaged_real_dir_conflict(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        make_skill(self.home / ".claude" / "skills" / "eli5", "unmanaged\n")

        result = materializer.materialize("claude")

        self.assertFalse(result["ok"])
        self.assertIn("conflicts", result["diff"])

    def test_materialize_writes_transaction_before_mutation_and_rollback_preserves_originals(self) -> None:
        src = make_skill(self.home / "src" / "eli5")
        planner.adopt_skill(src)
        resolver.set_skill("global", "skill.davidl.eli5", True)

        result = materializer.materialize("claude")
        rendered = self.home / ".claude" / "skills" / "eli5"
        tx_path = transactions.transactions_path(result["transaction_id"])

        self.assertTrue(result["ok"])
        self.assertTrue(tx_path.exists())
        self.assertTrue(rendered.is_symlink())

        rolled = transactions.rollback(result["transaction_id"])

        self.assertTrue(rolled["ok"])
        self.assertFalse(rendered.exists())
        self.assertTrue(src.exists())

    def test_codex_adapter_uses_codex_home_when_set(self) -> None:
        os.environ["CODEX_HOME"] = str(self.home / "custom-codex")

        self.assertEqual(adapters.adapter("codex").global_dir(), self.home / "custom-codex" / "skills")

    def test_backup_export_includes_store_and_inbox_not_rendered_state(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        make_skill(store.inbox_dir() / "inbox-skill")

        result = backup.export(self.home / "exports")
        root = Path(result["backup"])

        self.assertTrue((root / "skills-store" / "skills" / "skill.davidl.eli5").exists())
        self.assertTrue((root / "inbox" / "agents-skills" / "inbox-skill").exists())
        self.assertTrue((root / "rendered" / "claude-skills-list.json").exists())
        self.assertFalse((root / "rendered" / "claude" / "skills").exists())

    def test_backup_restore_preserves_presets_as_store_state(self) -> None:
        store.write_json(store.presets_root() / "vue.json", {"version": 1, "name": "vue", "enable": []})

        exported = backup.export(self.home / "exports")
        backup_root = Path(exported["backup"])
        os.environ["SKILLS_MANAGER_HOME"] = str(self.home / "restore-home")
        restored = backup.restore(backup_root, dry_run=False)

        self.assertTrue((backup_root / "skills-store" / "presets" / "vue.json").exists())
        self.assertTrue(restored["ok"])
        self.assertTrue((store.presets_root() / "vue.json").exists())

    def test_cli_preset_list_returns_names_only(self) -> None:
        store.write_json(store.store_root() / "presets" / "vue.json", {"version": 1, "name": "vue"})
        store.write_json(store.store_root() / "presets" / "default.json", {"version": 1, "name": "default"})
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run([sys.executable, str(BIN), "preset", "list"], env=env, cwd=ROOT, text=True, capture_output=True)

        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), ["default", "vue"])

    def test_cli_preset_create_dry_run_does_not_write(self) -> None:
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "create", "vue", "--description", "Vue stack", "--tag", "frontend", "--dry-run"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads(run.stdout)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["preset"]["name"], "vue")
        self.assertEqual(data["preset"]["tags"], ["frontend"])
        self.assertFalse((store.presets_root() / "vue.json").exists())

    def test_cli_preset_create_writes_by_default(self) -> None:
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "create", "vue", "--description", "Vue stack"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads((store.presets_root() / "vue.json").read_text())
        self.assertEqual(data["name"], "vue")
        self.assertEqual(data["description"], "Vue stack")
        self.assertEqual(data["clients"]["claude"], {"enable": [], "disable": []})

    def test_cli_preset_create_from_scope_captures_direct_entries_only(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        project = self.home / "project"
        resolver.set_skill("project", "skill.davidl.eli5", False, project=project)
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "create", "project-mask", "--from-scope", "project", "--project", str(project)],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads((store.presets_root() / "project-mask.json").read_text())
        self.assertEqual(data["enable"], [])
        self.assertEqual(data["disable"], [{"alias": "eli5", "id": "skill.davidl.eli5"}])

    def test_cli_preset_add_accepts_multiple_skill_refs(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        planner.adopt_skill(make_skill(self.home / "src" / "review"))
        store.write_json(store.presets_root() / "vue.json", presets.empty_preset("vue"))
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "add", "vue", "eli5", "review"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads((store.presets_root() / "vue.json").read_text())
        self.assertEqual(
            data["enable"],
            [
                {"alias": "eli5", "id": "skill.davidl.eli5"},
                {"alias": "review", "id": "skill.davidl.review"},
            ],
        )

    def test_cli_preset_add_fails_atomically_for_unknown_or_ambiguous_refs(self) -> None:
        planner.adopt_skill(make_skill(self.home / "a" / "same", "one\n"))
        planner.adopt_skill(make_skill(self.home / "b" / "same", "two\n"))
        original = presets.empty_preset("vue")
        store.write_json(store.presets_root() / "vue.json", original)
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "add", "vue", "same", "missing"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 1)
        data = json.loads(run.stdout)
        self.assertEqual({error["error"] for error in data["errors"]}, {"ambiguous", "unknown"})
        ambiguous = [error for error in data["errors"] if error["error"] == "ambiguous"][0]
        self.assertEqual(len(ambiguous["candidates"]), 2)
        self.assertEqual(json.loads((store.presets_root() / "vue.json").read_text()), original)

    def test_cli_preset_add_dry_run_does_not_write(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        original = presets.empty_preset("vue")
        store.write_json(store.presets_root() / "vue.json", original)
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "add", "vue", "eli5", "--dry-run"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(json.loads(run.stdout)["dry_run"])
        self.assertEqual(json.loads((store.presets_root() / "vue.json").read_text()), original)

    def test_cli_preset_remove_defaults_to_enable_and_supports_disable_mode(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        preset = presets.empty_preset("vue")
        entry = {"id": "skill.davidl.eli5", "alias": "eli5"}
        preset["enable"] = [entry]
        preset["disable"] = [entry]
        store.write_json(store.presets_root() / "vue.json", preset)
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        remove_enable = subprocess.run(
            [sys.executable, str(BIN), "preset", "remove", "vue", "eli5"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        after_enable = json.loads((store.presets_root() / "vue.json").read_text())
        remove_disable = subprocess.run(
            [sys.executable, str(BIN), "preset", "remove", "vue", "eli5", "--mode", "disable"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        after_disable = json.loads((store.presets_root() / "vue.json").read_text())

        self.assertEqual(remove_enable.returncode, 0, remove_enable.stderr)
        self.assertEqual(after_enable["enable"], [])
        self.assertEqual(after_enable["disable"], [entry])
        self.assertEqual(remove_disable.returncode, 0, remove_disable.stderr)
        self.assertEqual(after_disable["disable"], [])

    def test_cli_preset_rename_and_delete_require_apply(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.write_json(store.presets_root() / "vue.json", presets.empty_preset("vue"))
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        rename_dry = subprocess.run(
            [sys.executable, str(BIN), "preset", "rename", "vue", "frontend"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(rename_dry.returncode, 0, rename_dry.stderr)
        self.assertTrue((store.presets_root() / "vue.json").exists())
        self.assertFalse((store.presets_root() / "frontend.json").exists())

        rename_apply = subprocess.run(
            [sys.executable, str(BIN), "preset", "rename", "vue", "frontend", "--apply"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(rename_apply.returncode, 0, rename_apply.stderr)
        self.assertFalse((store.presets_root() / "vue.json").exists())
        self.assertTrue((store.presets_root() / "frontend.json").exists())

        delete_dry = subprocess.run(
            [sys.executable, str(BIN), "preset", "delete", "frontend"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(delete_dry.returncode, 0, delete_dry.stderr)
        self.assertTrue((store.presets_root() / "frontend.json").exists())

        delete_apply = subprocess.run(
            [sys.executable, str(BIN), "preset", "delete", "frontend", "--apply"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(delete_apply.returncode, 0, delete_apply.stderr)
        self.assertFalse((store.presets_root() / "frontend.json").exists())
        self.assertTrue(store.id_to_dir("skill.davidl.eli5").exists())

    def test_cli_preset_apply_global_merge_stamps_entries_and_removes_opposites(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        planner.adopt_skill(make_skill(self.home / "src" / "review"))
        planner.adopt_skill(make_skill(self.home / "src" / "keep"))
        resolver.set_skill("global", "skill.davidl.keep", True)
        resolver.set_skill("global", "skill.davidl.eli5", False)
        resolver.set_skill("global", "skill.davidl.review", True)
        store.write_json(
            store.presets_root() / "vue.json",
            {
                "version": 1,
                "name": "vue",
                "enable": [{"id": "skill.davidl.eli5", "alias": "eli5"}],
                "disable": [{"id": "skill.davidl.review", "alias": "review"}],
            },
        )
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "apply", "vue", "--scope", "global"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        manifest = store.load_manifest("global")
        self.assertEqual(manifest["enable"], ["skill.davidl.keep", "skill.davidl.eli5"])
        self.assertEqual(manifest["disable"], ["skill.davidl.review"])
        self.assertNotIn("preset", manifest)
        self.assertFalse((self.home / ".claude" / "skills" / "eli5").exists())

    def test_cli_preset_apply_dry_run_reports_before_after_without_writing(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.write_json(
            store.presets_root() / "vue.json",
            {"version": 1, "name": "vue", "enable": [{"id": "skill.davidl.eli5", "alias": "eli5"}]},
        )
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "apply", "vue", "--scope", "global", "--dry-run"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads(run.stdout)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["before"]["enable"], [])
        self.assertEqual(data["after"]["enable"], ["skill.davidl.eli5"])
        self.assertTrue(data["changes"])
        self.assertEqual(store.load_manifest("global")["enable"], [])

    def test_cli_preset_apply_project_defaults_to_cwd_and_reports_project(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.write_json(
            store.presets_root() / "vue.json",
            {"version": 1, "name": "vue", "enable": [{"id": "skill.davidl.eli5", "alias": "eli5"}]},
        )
        project = self.home / "project"
        project.mkdir()
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "apply", "vue", "--scope", "project"],
            env=env,
            cwd=project,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads(run.stdout)
        self.assertEqual(data["project"], str(project.resolve()))
        self.assertEqual(store.load_manifest("project", project=project)["enable"], ["skill.davidl.eli5"])

    def test_cli_preset_apply_replace_all_clears_all_selected_buckets(self) -> None:
        for name in ("eli5", "review", "keep"):
            planner.adopt_skill(make_skill(self.home / "src" / name))
        manifest = store.manifest_template(
            enable=["skill.davidl.keep"],
            disable=["skill.davidl.review"],
            clients={
                "claude": {"enable": ["skill.davidl.keep"], "disable": ["skill.davidl.review"]},
                "codex": {"enable": ["skill.davidl.keep"], "disable": ["skill.davidl.review"]},
            },
        )
        store.save_manifest("global", manifest)
        store.write_json(
            store.presets_root() / "vue.json",
            {
                "version": 1,
                "name": "vue",
                "enable": [{"id": "skill.davidl.eli5", "alias": "eli5"}],
                "clients": {"codex": {"disable": [{"id": "skill.davidl.review", "alias": "review"}]}},
            },
        )
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "apply", "vue", "--scope", "global", "--replace"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        manifest = store.load_manifest("global")
        self.assertEqual(manifest["enable"], ["skill.davidl.eli5"])
        self.assertEqual(manifest["disable"], [])
        self.assertEqual(manifest["clients"]["claude"], {"enable": [], "disable": []})
        self.assertEqual(manifest["clients"]["codex"], {"enable": [], "disable": ["skill.davidl.review"]})

    def test_cli_preset_apply_client_target_stamps_top_and_client_entries_only_to_client_bucket(self) -> None:
        for name in ("eli5", "review", "keep"):
            planner.adopt_skill(make_skill(self.home / "src" / name))
        manifest = store.manifest_template(
            enable=["skill.davidl.keep"],
            clients={
                "claude": {"enable": ["skill.davidl.keep"], "disable": []},
                "codex": {"enable": ["skill.davidl.keep"], "disable": ["skill.davidl.review"]},
            },
        )
        store.save_manifest("global", manifest)
        store.write_json(
            store.presets_root() / "vue.json",
            {
                "version": 1,
                "name": "vue",
                "enable": [{"id": "skill.davidl.eli5", "alias": "eli5"}],
                "clients": {"codex": {"enable": [{"id": "skill.davidl.review", "alias": "review"}]}},
            },
        )
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "apply", "vue", "--scope", "global", "--client", "codex", "--replace"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        manifest = store.load_manifest("global")
        self.assertEqual(manifest["enable"], ["skill.davidl.keep"])
        self.assertEqual(manifest["clients"]["claude"], {"enable": ["skill.davidl.keep"], "disable": []})
        self.assertEqual(manifest["clients"]["codex"], {"enable": ["skill.davidl.eli5", "skill.davidl.review"], "disable": []})

    def test_cli_preset_apply_unknown_ids_fail_atomically(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "keep"))
        resolver.set_skill("global", "skill.davidl.keep", True)
        before = store.load_manifest("global")
        store.write_json(
            store.presets_root() / "broken.json",
            {"version": 1, "name": "broken", "enable": [{"id": "skill.davidl.missing", "alias": "missing"}]},
        )
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "preset", "apply", "broken", "--scope", "global"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 1)
        data = json.loads(run.stdout)
        self.assertEqual(data["errors"], [{"skill_id": "skill.davidl.missing", "type": "unknown_id"}])
        self.assertEqual(store.load_manifest("global"), before)

    def test_cli_enable_writes_shared_action_log(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "enable", "eli5", "--scope", "global", "--client", "all"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        log_path = store.store_root() / "logs" / "actions.jsonl"
        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        self.assertEqual(entries[-1]["surface"], "cli")
        self.assertEqual(entries[-1]["action"], "enable")
        self.assertEqual(entries[-1]["scope"], "global")
        self.assertEqual(entries[-1]["client"], "all")
        self.assertEqual(entries[-1]["manifest_path"], str(store.manifest_path("global")))

    def test_core_preset_mutation_logs_and_dry_run_does_not(self) -> None:
        presets.create_preset("dry", dry_run=True)
        self.assertFalse((store.logs_root() / "actions.jsonl").exists())

        result = presets.create_preset("vue")

        self.assertTrue(result["ok"])
        entries = [json.loads(line) for line in (store.logs_root() / "actions.jsonl").read_text().splitlines()]
        self.assertEqual(entries[-1]["surface"], "core")
        self.assertEqual(entries[-1]["action"], "preset_create")
        self.assertEqual(entries[-1]["preset_name"], "vue")
        self.assertEqual(entries[-1]["target_path"], str(store.presets_root() / "vue.json"))

    def test_materialize_logs_transaction_id_and_dry_run_does_not_log(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        before_count = len((store.logs_root() / "actions.jsonl").read_text().splitlines())

        dry = materializer.materialize("claude", dry_run=True)
        applied = materializer.materialize("claude")

        entries = [json.loads(line) for line in (store.logs_root() / "actions.jsonl").read_text().splitlines()]
        self.assertTrue(dry["ok"])
        self.assertEqual(len(entries), before_count + 1)
        self.assertEqual(entries[-1]["action"], "materialize")
        self.assertEqual(entries[-1]["client"], "claude")
        self.assertEqual(entries[-1]["transaction_id"], applied["transaction_id"])

    def test_cli_preset_show_returns_enriched_skill_entries(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.write_json(
            store.presets_root() / "vue.json",
            {
                "version": 1,
                "name": "vue",
                "description": "Vue stack",
                "tags": ["frontend"],
                "enable": [{"id": "skill.davidl.eli5", "alias": "old-eli5"}],
            },
        )
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run([sys.executable, str(BIN), "preset", "show", "vue"], env=env, cwd=ROOT, text=True, capture_output=True)

        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads(run.stdout)
        entry = data["enable"][0]
        self.assertEqual(data["name"], "vue")
        self.assertEqual(entry["id"], "skill.davidl.eli5")
        self.assertEqual(entry["stored_alias"], "old-eli5")
        self.assertTrue(entry["exists"])
        self.assertEqual(entry["current_aliases"]["claude"], "eli5")
        self.assertIn("alias_drift", entry["issues"])

    def test_doctor_validates_preset_files(self) -> None:
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.presets_root().mkdir(parents=True, exist_ok=True)
        (store.presets_root() / "malformed.json").write_text("{nope", encoding="utf-8")
        store.write_json(store.presets_root() / "unknown.json", {"version": 1, "name": "unknown", "enable": [{"id": "skill.davidl.missing", "alias": "missing"}]})
        store.write_json(
            store.presets_root() / "duplicate.json",
            {
                "version": 1,
                "name": "duplicate",
                "enable": [
                    {"id": "skill.davidl.eli5", "alias": "eli5"},
                    {"id": "skill.davidl.eli5", "alias": "eli5"},
                ],
            },
        )
        store.write_json(store.presets_root() / "drift.json", {"version": 1, "name": "drift", "enable": [{"id": "skill.davidl.eli5", "alias": "old"}]})
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run([sys.executable, str(BIN), "doctor"], env=env, cwd=ROOT, text=True, capture_output=True)

        self.assertNotEqual(run.returncode, 0)
        issue_types = {issue["type"] for issue in json.loads(run.stdout)["issues"]}
        self.assertIn("preset_malformed", issue_types)
        self.assertIn("preset_unknown_id", issue_types)
        self.assertIn("preset_duplicate_entry", issue_types)
        self.assertIn("preset_alias_drift", issue_types)

    def test_restore_dry_run_reports_changes_without_writing(self) -> None:
        backup_root = self.home / "backup" / "agent-skills-backup"
        (backup_root / "skills-store" / "skills" / "skill.davidl.eli5").mkdir(parents=True)
        (backup_root / "manifest.json").write_text(json.dumps({"version": 1}), encoding="utf-8")

        result = backup.restore(backup_root, dry_run=True)

        self.assertTrue(result["ok"])
        self.assertFalse((store.skills_root() / "skill.davidl.eli5").exists())

    def test_restore_apply_restores_store_and_requires_materialization_afterward(self) -> None:
        source_home = self.home / "source-home"
        os.environ["SKILLS_MANAGER_HOME"] = str(source_home)
        planner.adopt_skill(make_skill(source_home / "src" / "eli5"))
        exported = backup.export(source_home / "exports")

        os.environ["SKILLS_MANAGER_HOME"] = str(self.home / "restore-home")
        result = backup.restore(exported["backup"], dry_run=False)

        self.assertTrue(result["ok"])
        self.assertTrue((store.skills_root() / "skill.davidl.eli5").exists())
        self.assertIn("materialize", " ".join(result["plan"]["after"]))

    def test_cli_smoke_uses_temp_home(self) -> None:
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        help_run = subprocess.run([sys.executable, str(BIN), "--help"], env=env, cwd=ROOT, text=True, capture_output=True)
        backup_run = subprocess.run([sys.executable, str(BIN), "backup", "--dry-run"], env=env, cwd=ROOT, text=True, capture_output=True)
        doctor_run = subprocess.run([sys.executable, str(BIN), "doctor"], env=env, cwd=ROOT, text=True, capture_output=True)

        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertEqual(backup_run.returncode, 0, backup_run.stderr)
        self.assertEqual(doctor_run.returncode, 0, doctor_run.stderr)
        self.assertIn("skills-manager", help_run.stdout)

    def test_cli_rejects_profile_scope(self) -> None:
        env = os.environ.copy()
        env["SKILLS_MANAGER_HOME"] = str(self.home)

        run = subprocess.run(
            [sys.executable, str(BIN), "enable", "skill.davidl.eli5", "--scope", "profile"],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(run.returncode, 0)
        self.assertIn("invalid choice", run.stderr)
        self.assertFalse((store.manifests_root() / "profiles").exists())

    def test_tui_state_helpers_support_navigation_filter_and_client_modes(self) -> None:
        from skills_manager import tui

        state = tui.TuiState(items=["Managed skills", "Presets", "Doctor"])
        state = tui.move_selection(state, 1)
        state = tui.apply_filter(state, "pre")
        state = tui.cycle_client_mode(state)

        self.assertEqual(state.filtered_items(), ["Presets"])
        self.assertEqual(state.selected_item(), "Presets")
        self.assertEqual(state.client_mode, "claude")
        self.assertIn("skills-manager preset list", tui.command_hint("Presets"))
        self.assertTrue(tui.is_enter_key(10))
        self.assertEqual(tui._rgb_to_curses((0x27, 0x29, 0x32)), (153, 161, 196))

        opened = tui.open_selected_item(state)
        self.assertEqual(opened.mode, "detail")
        self.assertEqual(opened.detail_item, "Presets")
        self.assertIn("Equivalent CLI:", "\n".join(tui.render_lines(opened)))

        closed = tui.close_detail(opened)
        self.assertEqual(closed.mode, "dashboard")
        self.assertIsNone(closed.detail_item)

    def test_bare_main_dispatches_to_tui(self) -> None:
        with mock.patch("skills_manager.tui.run", return_value=0) as run:
            self.assertEqual(cli.main([]), 0)

        run.assert_called_once()

    def test_tui_project_view_separates_effective_state_direct_entries_and_incompatible_toggle(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        planner.adopt_skill(make_skill(self.home / "src" / "claude-only"), compatibility={"claude": True, "codex": False})
        resolver.set_skill("global", "skill.davidl.eli5", True)
        resolver.set_skill("global", "skill.davidl.claude-only", True)
        project = self.home / "project"
        resolver.set_skill("project", "skill.davidl.eli5", False, project=project)

        hidden = tui.build_desired_state_view("project", "codex", project=project)
        shown = tui.build_desired_state_view("project", "codex", project=project, show_incompatible=True)

        eli5 = [row for row in hidden["rows"] if row["id"] == "skill.davidl.eli5"][0]
        self.assertFalse(eli5["enabled"])
        self.assertEqual(eli5["direct"], "disable")
        self.assertIn("global:enable", eli5["reasons"])
        self.assertNotIn("skill.davidl.claude-only", {row["id"] for row in hidden["rows"]})
        self.assertIn("skill.davidl.claude-only", {row["id"] for row in shown["rows"]})
        self.assertEqual(hidden["direct_entries"]["disable"], ["skill.davidl.eli5"])
        self.assertIn("materialize --client codex --project", hidden["materialize_hint"])

    def test_tui_project_edit_mutates_only_project_overrides_and_sets_needs_materialize(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        project = self.home / "project"

        disabled = tui.apply_desired_state_edit("project", "skill.davidl.eli5", "disable", client="all", project=project)
        removed = tui.apply_desired_state_edit("project", "skill.davidl.eli5", "remove_override", client="all", project=project)

        self.assertTrue(disabled["needs_materialize"])
        self.assertEqual(store.load_manifest("global")["enable"], ["skill.davidl.eli5"])
        self.assertEqual(disabled["direct_entries"]["disable"], ["skill.davidl.eli5"])
        self.assertTrue(removed["needs_materialize"])
        self.assertEqual(store.load_manifest("project", project=project)["enable"], [])
        self.assertEqual(store.load_manifest("project", project=project)["disable"], [])

    def test_tui_preset_manager_lists_filters_and_shows_enriched_details(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.write_json(
            store.presets_root() / "vue.json",
            {"version": 1, "name": "vue", "enable": [{"id": "skill.davidl.eli5", "alias": "old"}]},
        )
        store.write_json(store.presets_root() / "angular.json", presets.empty_preset("angular"))

        view = tui.build_preset_manager_view(filter_text="vu", selected_name="vue")

        self.assertEqual(view["names"], ["vue"])
        self.assertEqual(view["selected"]["name"], "vue")
        self.assertIn("alias_drift", view["selected"]["issues"])
        self.assertEqual(view["cli_command"], "skills-manager preset show vue")

    def test_tui_preset_create_and_capture_previews_do_not_write(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        project = self.home / "project"
        resolver.set_skill("project", "skill.davidl.eli5", True, project=project)

        manual = tui.preview_preset_create("vue", description="Vue stack", tags=["frontend"])
        captured = tui.preview_preset_capture("project-vue", "project", project=project)

        self.assertEqual(manual["confirmation"], "single_key")
        self.assertTrue(manual["result"]["dry_run"])
        self.assertEqual(manual["cli_command"], "skills-manager preset create vue --description 'Vue stack' --tag frontend --dry-run")
        self.assertEqual(captured["result"]["preset"]["enable"], [{"id": "skill.davidl.eli5", "alias": "eli5"}])
        self.assertIn("--from-scope project", captured["cli_command"])
        self.assertFalse((store.presets_root() / "vue.json").exists())
        self.assertFalse((store.presets_root() / "project-vue.json").exists())

    def test_tui_preset_add_remove_previews_surface_atomic_ref_errors(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "a" / "same", "one\n"))
        planner.adopt_skill(make_skill(self.home / "b" / "same", "two\n"))
        original = presets.empty_preset("vue")
        store.write_json(store.presets_root() / "vue.json", original)

        preview = tui.preview_preset_edit("add", "vue", ["same", "missing"])

        self.assertEqual(preview["confirmation"], "single_key")
        self.assertFalse(preview["result"]["ok"])
        self.assertEqual({error["error"] for error in preview["result"]["errors"]}, {"ambiguous", "unknown"})
        self.assertIn("skills-manager preset add vue same missing --dry-run", preview["cli_command"])
        self.assertEqual(json.loads((store.presets_root() / "vue.json").read_text()), original)

    def test_tui_preset_apply_preview_shows_before_after_and_confirmation_policy(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.write_json(
            store.presets_root() / "vue.json",
            {"version": 1, "name": "vue", "enable": [{"id": "skill.davidl.eli5", "alias": "eli5"}]},
        )

        merge = tui.preview_preset_apply("vue", "global")
        replace = tui.preview_preset_apply("vue", "global", replace=True)

        self.assertEqual(merge["confirmation"], "single_key")
        self.assertEqual(replace["confirmation"], "typed")
        self.assertEqual(merge["result"]["before"]["enable"], [])
        self.assertEqual(merge["result"]["after"]["enable"], ["skill.davidl.eli5"])
        self.assertTrue(merge["result"]["changes"])
        self.assertIn("skills-manager preset apply vue --scope global --dry-run", merge["cli_command"])
        self.assertIn("--replace", replace["cli_command"])
        self.assertEqual(store.load_manifest("global")["enable"], [])

    def test_tui_preset_delete_preview_requires_typed_confirmation_and_does_not_write(self) -> None:
        from skills_manager import tui

        store.write_json(store.presets_root() / "vue.json", presets.empty_preset("vue"))

        preview = tui.preview_preset_delete("vue")

        self.assertEqual(preview["confirmation"], "typed")
        self.assertTrue(preview["result"]["dry_run"])
        self.assertEqual(preview["cli_command"], "skills-manager preset delete vue")
        self.assertTrue((store.presets_root() / "vue.json").exists())

    def test_tui_preset_rename_preview_uses_single_key_confirmation(self) -> None:
        from skills_manager import tui

        store.write_json(store.presets_root() / "vue.json", presets.empty_preset("vue"))

        preview = tui.preview_preset_rename("vue", "frontend")

        self.assertEqual(preview["confirmation"], "single_key")
        self.assertTrue(preview["result"]["dry_run"])
        self.assertEqual(preview["cli_command"], "skills-manager preset rename vue frontend")
        self.assertTrue((store.presets_root() / "vue.json").exists())
        self.assertFalse((store.presets_root() / "frontend.json").exists())

    def test_tui_render_view_shows_diff_preview_target_and_cli_command(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        project = self.home / "project"

        view = tui.build_render_view("claude", project=project)

        self.assertEqual(view["client"], "claude")
        self.assertEqual(view["project"], str(project.resolve()))
        self.assertEqual(view["creates"], [{"alias": "eli5", "skill_id": "skill.davidl.eli5"}])
        self.assertEqual(view["removes"], [])
        self.assertEqual(view["conflicts"], [])
        self.assertIn(".claude/skills", view["rendered_dir"])
        self.assertEqual(view["confirmation"], "single_key")
        self.assertIn("skills-manager materialize --client claude --project", view["cli_command"])
        self.assertFalse((self.home / ".claude" / "skills" / "eli5").exists())

    def test_tui_materialize_apply_uses_preview_reports_transaction_and_auto_doctor(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)

        preview = tui.build_render_view("codex")
        result = tui.apply_materialize_preview(preview)

        self.assertTrue(result["ok"])
        self.assertTrue(result["transaction_id"])
        self.assertTrue(result["doctor"]["ok"])
        self.assertIn("restart", result["note"].lower())
        self.assertTrue((self.home / ".codex" / "skills" / "eli5").exists())

    def test_tui_materialize_apply_refuses_preview_conflicts(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        make_skill(self.home / ".claude" / "skills" / "eli5", "unmanaged\n")

        preview = tui.build_render_view("claude")
        result = tui.apply_materialize_preview(preview)

        self.assertFalse(result["ok"])
        self.assertTrue(preview["conflicts"])
        self.assertIn("conflicts", result["error"])
        self.assertFalse((self.home / ".claude" / "skills" / "eli5").is_symlink())

    def test_tui_doctor_view_surfaces_scan_conflicts_and_preset_issues(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        make_skill(self.home / ".claude" / "skills" / "eli5", "unmanaged\n")
        (store.inbox_dir() / "missing").mkdir(parents=True)
        store.write_json(
            store.presets_root() / "broken.json",
            {"version": 1, "name": "broken", "enable": [{"id": "skill.davidl.missing", "alias": "missing"}]},
        )

        view = tui.build_doctor_view()

        issue_types = {issue["type"] for issue in view["issues"]}
        self.assertFalse(view["ok"])
        self.assertIn("missing_skill_md", issue_types)
        self.assertIn("conflict", issue_types)
        self.assertIn("preset_unknown_id", issue_types)
        self.assertEqual(view["cli_command"], "skills-manager doctor")

    def test_tui_rollback_view_lists_transactions_and_requires_typed_confirmation(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        resolver.set_skill("global", "skill.davidl.eli5", True)
        materialized = materializer.materialize("claude")

        view = tui.build_rollback_view()
        preview = tui.preview_rollback(materialized["transaction_id"])
        result = tui.apply_rollback_preview(preview)

        self.assertEqual(view["transactions"][0]["id"], materialized["transaction_id"])
        self.assertEqual(preview["confirmation"], "typed")
        self.assertIn("skills-manager rollback", preview["cli_command"])
        self.assertTrue(result["ok"])
        self.assertFalse((self.home / ".claude" / "skills" / "eli5").exists())

    def test_tui_scan_view_shows_skill_locations_and_issues(self) -> None:
        from skills_manager import tui

        make_skill(store.inbox_dir() / "inbox-skill")
        (self.home / ".claude" / "skills" / "missing").mkdir(parents=True)
        project = self.home / "project"
        make_skill(project / ".codex" / "skills" / "project-skill")

        view = tui.build_scan_view(project=project)

        self.assertIn("inbox", view["locations"])
        self.assertIn("claude_global", view["locations"])
        self.assertIn("codex_project", view["locations"])
        self.assertIn("inbox-skill", {entry["name"] for entry in view["locations"]["inbox"]["entries"]})
        self.assertIn("missing_skill_md", {issue["type"] for issue in view["issues"]})
        self.assertIn("skills-manager scan --json --project", view["cli_command"])

    def test_tui_import_flow_previews_candidates_then_applies_and_runs_doctor(self) -> None:
        from skills_manager import tui

        make_skill(store.inbox_dir() / "eli5")

        preview = tui.preview_import_inbox()
        result = tui.apply_import_preview(preview)

        self.assertEqual(preview["confirmation"], "single_key")
        self.assertEqual([candidate["name"] for candidate in preview["result"]["candidates"]], ["eli5"])
        self.assertIn("skills-manager import --dry-run", preview["cli_command"])
        self.assertIn("skills-manager backup --dry-run", preview["backup_hint"])
        self.assertFalse(preview["doctor_after"])
        self.assertTrue(result["doctor"]["ok"])
        self.assertTrue((store.skills_root() / "skill.davidl.eli5" / "SKILL.md").exists())

    def test_tui_adopt_preview_reports_validation_errors_for_non_skill_paths(self) -> None:
        from skills_manager import tui

        not_skill = self.home / "not-skill"
        not_skill.mkdir()

        preview = tui.preview_adopt_path(not_skill)

        self.assertEqual(preview["confirmation"], "single_key")
        self.assertFalse(preview["result"]["ok"])
        self.assertIn("not a skill directory", preview["result"]["error"])
        self.assertIn("skills-manager adopt", preview["cli_command"])

    def test_tui_migrate_flow_previews_actions_requires_typed_confirmation_and_runs_doctor(self) -> None:
        from skills_manager import tui

        make_skill(self.home / ".claude" / "skills" / "same", "same\n")
        make_skill(self.home / ".codex" / "skills" / "same", "same\n")

        preview = tui.preview_migrate()
        result = tui.apply_migrate_preview(preview)

        self.assertEqual(preview["confirmation"], "typed")
        self.assertEqual(preview["result"]["actions"][0]["kind"], "merge")
        self.assertIn("skills-manager migrate --dry-run", preview["cli_command"])
        self.assertIn("skills-manager backup --dry-run", preview["backup_hint"])
        self.assertTrue(result["doctor"]["ok"])
        self.assertTrue((store.skills_root() / "skill.davidl.same" / "SKILL.md").exists())

    def test_tui_backup_preview_shows_includes_and_rendered_metadata_without_writing(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.write_json(store.presets_root() / "vue.json", presets.empty_preset("vue"))
        export_dir = self.home / "exports"

        preview = tui.preview_backup(export_dir)

        self.assertEqual(preview["confirmation"], "single_key")
        self.assertIn(str(store.skills_root()), preview["result"]["include"])
        self.assertIn(str(store.presets_root()), preview["result"]["include"])
        self.assertIn("claude", preview["result"]["rendered_metadata_only"])
        self.assertIn("skills-manager backup --export", preview["cli_command"])
        self.assertFalse((export_dir / "agent-skills-backup").exists())

    def test_tui_backup_apply_creates_backup_with_presets_and_rendered_metadata_only(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.write_json(store.presets_root() / "vue.json", presets.empty_preset("vue"))
        make_skill(self.home / ".claude" / "skills" / "unmanaged")

        preview = tui.preview_backup(self.home / "exports")
        result = tui.apply_backup_preview(preview)
        root = Path(result["backup"])

        self.assertTrue(result["ok"])
        self.assertTrue((root / "skills-store" / "skills" / "skill.davidl.eli5").exists())
        self.assertTrue((root / "skills-store" / "presets" / "vue.json").exists())
        self.assertTrue((root / "rendered" / "claude-skills-list.json").exists())
        self.assertFalse((root / "rendered" / "claude" / "skills" / "unmanaged").exists())

    def test_tui_restore_preview_and_apply_requires_typed_confirmation_and_runs_doctor(self) -> None:
        from skills_manager import tui

        os.environ["SKILLS_MANAGER_HOME"] = str(self.home / "source")
        planner.adopt_skill(make_skill(self.home / "source" / "src" / "eli5"))
        store.write_json(store.presets_root() / "vue.json", presets.empty_preset("vue"))
        make_skill(store.inbox_dir() / "inbox-skill")
        make_skill(self.home / "source" / ".claude" / "skills" / "rendered-only")
        exported = backup.export(self.home / "exports")

        os.environ["SKILLS_MANAGER_HOME"] = str(self.home / "restore")
        preview = tui.preview_restore(exported["backup"])
        result = tui.apply_restore_preview(preview)

        self.assertEqual(preview["confirmation"], "typed")
        self.assertTrue(preview["result"]["dry_run"])
        self.assertIn("skills-manager restore --from", preview["cli_command"])
        self.assertTrue(result["ok"])
        self.assertTrue((store.skills_root() / "skill.davidl.eli5").exists())
        self.assertTrue((store.presets_root() / "vue.json").exists())
        self.assertTrue((store.inbox_dir() / "inbox-skill" / "SKILL.md").exists())
        self.assertFalse((self.home / "restore" / ".claude" / "skills" / "rendered-only").exists())
        self.assertTrue(result["doctor"]["ok"])
        self.assertIn("materialize", " ".join(result["after"]))

    def test_first_run_detection_starts_wizard_for_empty_store_then_dashboard_after_state_exists(self) -> None:
        from skills_manager import tui

        self.assertTrue(tui.is_first_run())
        empty_state = tui.initial_tui_state()
        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        dashboard_state = tui.initial_tui_state()

        self.assertFalse(tui.is_first_run())
        self.assertEqual(empty_state.mode, "first_run")
        self.assertIn("First-run wizard", empty_state.items[0])
        self.assertEqual(dashboard_state.mode, "dashboard")

    def test_first_run_wizard_scans_candidates_and_recommends_backup(self) -> None:
        from skills_manager import tui

        make_skill(store.inbox_dir() / "inbox-skill")
        make_skill(self.home / ".claude" / "skills" / "legacy")

        wizard = tui.build_first_run_wizard_state()

        self.assertEqual(wizard["step"], "scan")
        self.assertEqual([c["name"] for c in wizard["import_preview"]["result"]["candidates"]], ["inbox-skill"])
        self.assertEqual(wizard["migrate_preview"]["result"]["actions"][0]["alias"], "legacy")
        self.assertIn("skills-manager backup --dry-run", wizard["backup_hint"])
        self.assertTrue(wizard["needs_backup"])

    def test_first_run_wizard_selects_initial_global_skills_or_preset_and_sets_needs_materialize(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        store.write_json(
            store.presets_root() / "starter.json",
            {"version": 1, "name": "starter", "enable": [{"id": "skill.davidl.eli5", "alias": "eli5"}]},
        )

        selected = tui.first_run_select_global_skills(["skill.davidl.eli5"])
        preset_preview = tui.first_run_preview_preset("starter")

        self.assertTrue(selected["needs_materialize"])
        self.assertEqual(store.load_manifest("global")["enable"], ["skill.davidl.eli5"])
        self.assertEqual(preset_preview["result"]["after"]["enable"], ["skill.davidl.eli5"])
        self.assertEqual(preset_preview["confirmation"], "single_key")

    def test_first_run_wizard_materializes_runs_doctor_and_lands_on_dashboard(self) -> None:
        from skills_manager import tui

        planner.adopt_skill(make_skill(self.home / "src" / "eli5"))
        selected = tui.first_run_select_global_skills(["skill.davidl.eli5"])

        materialize_preview = tui.first_run_preview_materialize("claude")
        completed = tui.first_run_complete_materialize(materialize_preview)

        self.assertTrue(selected["needs_materialize"])
        self.assertEqual(materialize_preview["creates"], [{"alias": "eli5", "skill_id": "skill.davidl.eli5"}])
        self.assertTrue(completed["materialize"]["ok"])
        self.assertTrue(completed["doctor"]["ok"])
        self.assertEqual(completed["next_state"].mode, "dashboard")
        self.assertFalse(completed["needs_materialize"])


if __name__ == "__main__":
    unittest.main()
