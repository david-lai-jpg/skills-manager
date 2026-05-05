"""skills-manager command line interface."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import adapters, backup, materializer, planner, resolver, scanner, store, transactions


def emit(data: Any, as_json: bool = True) -> int:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    return emit(scanner.scan(project=args.project), args.json)


def cmd_import(args: argparse.Namespace) -> int:
    return emit(planner.import_inbox(dry_run=args.dry_run), True)


def cmd_adopt(args: argparse.Namespace) -> int:
    data = planner.adopt_skill(args.path, dry_run=False)
    emit(data, True)
    return 0 if data.get("ok") else 1


def cmd_migrate(args: argparse.Namespace) -> int:
    if args.apply:
        return emit(planner.migrate_apply(), True)
    return emit({**planner.migrate_plan(), "dry_run": True}, True)


def cmd_state(args: argparse.Namespace) -> int:
    clients = adapters.expand_clients(args.client)
    data: Any = {client: resolver.resolve(client, project=args.project) for client in clients}
    if args.client != "all":
        data = data[args.client]
    return emit(data, args.json)


def cmd_enable_disable(args: argparse.Namespace, enabled: bool) -> int:
    skill_id = args.skill
    if not skill_id.startswith("skill."):
        matches = [
            sid
            for sid, meta in store.all_skills().items()
            if skill_id in set(meta.get("aliases", {}).values()) or skill_id == sid.split(".")[-1]
        ]
        if len(matches) == 1:
            skill_id = matches[0]
    path = resolver.set_skill(args.scope, skill_id, enabled, client=args.client, project=args.project)
    return emit(
        {
            "ok": True,
            "enabled": enabled,
            "skill_id": skill_id,
            "scope": args.scope,
            "client": args.client,
            "manifest": str(path),
            "note": "Codex skill visibility may require a new Codex session.",
        },
        True,
    )


def cmd_materialize(args: argparse.Namespace) -> int:
    results = {client: materializer.materialize(client, project=args.project, dry_run=args.dry_run) for client in adapters.expand_clients(args.client)}
    ok = all(result.get("ok") for result in results.values())
    emit(results, True)
    return 0 if ok else 1


def cmd_diff(args: argparse.Namespace) -> int:
    data: Any = {client: materializer.diff(client, project=args.project) for client in adapters.expand_clients(args.client)}
    if args.client != "all":
        data = data[args.client]
    return emit(data, True)


def cmd_doctor(args: argparse.Namespace) -> int:
    scan = scanner.scan(project=args.project)
    issues = []
    for loc_name, loc in scan["locations"].items():
        for entry in loc.get("entries", []):
            if entry.get("type") in {"broken_symlink", "missing_skill_md", "error"}:
                issues.append({"location": loc_name, **entry})
    for client in adapters.CLIENTS:
        d = materializer.diff(client, project=args.project)
        for conflict in d["conflicts"]:
            issues.append({"location": f"{client}_rendered", "type": "conflict", **conflict})
    data = {"ok": not issues, "issues": issues, "store": str(store.store_root()), "inbox": str(store.inbox_dir())}
    emit(data, True)
    return 0 if data["ok"] else 1


def cmd_rollback(args: argparse.Namespace) -> int:
    data = transactions.rollback(args.transaction_id)
    emit(data, True)
    return 0 if data.get("ok") else 1


def cmd_backup(args: argparse.Namespace) -> int:
    if args.dry_run or not args.export:
        return emit(backup.dry_run_export(args.export), True)
    return emit(backup.export(args.export), True)


def cmd_restore(args: argparse.Namespace) -> int:
    data = backup.restore(args.from_path, dry_run=args.dry_run or not args.apply)
    emit(data, True)
    return 0 if data.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skills-manager", description="CLI-first Agent Skill manager")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--json", action="store_true")
    s.add_argument("--project")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("import")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("adopt")
    s.add_argument("path")
    s.set_defaults(func=cmd_adopt)

    s = sub.add_parser("migrate")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_migrate)

    s = sub.add_parser("state")
    s.add_argument("--client", choices=["claude", "codex", "all"], default="all")
    s.add_argument("--project")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_state)

    for name, enabled in (("enable", True), ("disable", False)):
        s = sub.add_parser(name)
        s.add_argument("skill")
        s.add_argument("--scope", choices=["global", "project", "session", "profile"], required=True)
        s.add_argument("--client", choices=["claude", "codex", "all"], default="all")
        s.add_argument("--project")
        s.set_defaults(func=lambda args, enabled=enabled: cmd_enable_disable(args, enabled))

    s = sub.add_parser("materialize")
    s.add_argument("--client", choices=["claude", "codex", "all"], default="all")
    s.add_argument("--project")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_materialize)

    s = sub.add_parser("diff")
    s.add_argument("--client", choices=["claude", "codex", "all"], default="all")
    s.add_argument("--project")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("doctor")
    s.add_argument("--project")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("rollback")
    s.add_argument("transaction_id")
    s.set_defaults(func=cmd_rollback)

    s = sub.add_parser("backup")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--export")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("restore")
    s.add_argument("--from", dest="from_path", required=True)
    group = s.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_restore)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
