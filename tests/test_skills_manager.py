from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skills_manager import adapters, backup, materializer, planner, resolver, scanner, store, transactions


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


if __name__ == "__main__":
    unittest.main()
