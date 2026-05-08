"""Hydra CLI — thin client for the Hydra REST API."""

from __future__ import annotations

import argparse
import json
import os
import sys

from hydra_cli import api
from hydra_cli.remote import cmd_capture_remote_url
from hydra_cli.sync import cmd_sync


def _die(status: int, body: str) -> None:
    """Print error details to stderr and exit."""
    try:
        detail = json.loads(body).get("detail", body)
    except (json.JSONDecodeError, AttributeError):
        detail = body
    print(f"Error ({status}): {detail}", file=sys.stderr)
    sys.exit(1)


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2))


def _read_body(args: argparse.Namespace) -> str:
    """Read body content from --body-file or stdin."""
    if hasattr(args, "body_file") and args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            return f.read()
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


# --- memory commands ---


def cmd_memory_list(args: argparse.Namespace) -> None:
    status, body = api.get("/api/memory")
    if status != 200:
        _die(status, body)
    _print_json(json.loads(body))


def cmd_memory_get(args: argparse.Namespace) -> None:
    status, body = api.get(f"/api/memory/{args.id}")
    if status != 200:
        _die(status, body)
    _print_json(json.loads(body))


def cmd_memory_create(args: argparse.Namespace) -> None:
    payload: dict[str, str] = {
        "name": args.name,
        "type": args.type,
    }
    if args.desc:
        payload["description"] = args.desc
    if args.project:
        payload["project_slug"] = args.project
    body_text = _read_body(args)
    if body_text:
        payload["body"] = body_text
    status, body = api.post("/api/memory", payload)
    if status != 200:
        _die(status, body)
    _print_json(json.loads(body))


def cmd_memory_update(args: argparse.Namespace) -> None:
    payload: dict[str, str] = {}
    if args.name:
        payload["name"] = args.name
    if args.type:
        payload["type"] = args.type
    if args.desc is not None:
        payload["description"] = args.desc
    body_text = _read_body(args)
    if body_text:
        payload["body"] = body_text
    if not payload:
        print("Nothing to update", file=sys.stderr)
        sys.exit(1)
    status, body = api.put_json(f"/api/memory/{args.id}", payload)
    if status != 200:
        _die(status, body)
    _print_json(json.loads(body))


def cmd_memory_delete(args: argparse.Namespace) -> None:
    status, body = api.delete(f"/api/memory/{args.id}")
    if status != 204:
        _die(status, body)


# --- project commands ---


def cmd_project_list(args: argparse.Namespace) -> None:
    status, body = api.get("/api/projects")
    if status != 200:
        _die(status, body)
    _print_json(json.loads(body))


def cmd_project_get(args: argparse.Namespace) -> None:
    status, body = api.get(f"/api/projects/{args.slug}")
    if status != 200:
        _die(status, body)
    _print_json(json.loads(body))


def cmd_project_create(args: argparse.Namespace) -> None:
    payload: dict[str, str] = {
        "slug": args.slug,
        "path": args.path,
    }
    if args.desc:
        payload["description"] = args.desc
    status, body = api.post("/api/projects", payload)
    if status != 201:
        _die(status, body)
    _print_json(json.loads(body))


def cmd_project_update(args: argparse.Namespace) -> None:
    payload: dict[str, str] = {}
    if args.path:
        payload["path"] = args.path
    if args.desc is not None:
        payload["description"] = args.desc
    if not payload:
        print("Nothing to update", file=sys.stderr)
        sys.exit(1)
    status, body = api.put_json(f"/api/projects/{args.slug}", payload)
    if status != 200:
        _die(status, body)
    _print_json(json.loads(body))


def cmd_project_delete(args: argparse.Namespace) -> None:
    status, body = api.delete(f"/api/projects/{args.slug}")
    if status != 204:
        _die(status, body)


def cmd_project_attach(args: argparse.Namespace) -> None:
    """Register the current cwd to a slug. Uses cwd basename if --slug is
    omitted. Creates the project if the slug is new, otherwise adds/updates
    this machine's path row for that slug."""
    cwd = os.path.abspath(args.cwd or os.getcwd())
    slug = args.slug or os.path.basename(cwd)
    if not slug:
        print("Cannot derive slug from empty basename", file=sys.stderr)
        sys.exit(1)
    payload = {"slug": slug, "path": cwd}
    status, body = api.post("/api/projects", payload)
    if status != 201:
        _die(status, body)
    _print_json(json.loads(body))


# --- config commands ---


def cmd_config_get_claude_md(args: argparse.Namespace) -> None:
    status, body = api.get("/api/config/claude-md")
    if status != 200:
        _die(status, body)
    print(body, end="")


def cmd_config_put_claude_md(args: argparse.Namespace) -> None:
    with open(args.file, encoding="utf-8") as f:
        content = f.read()
    status, body = api.put_text("/api/config/claude-md", content)
    if status != 200:
        _die(status, body)
    _print_json(json.loads(body))


# --- argument parser ---


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hydra", description="Hydra CLI")
    sub = parser.add_subparsers(dest="group")

    # --- memory ---
    mem = sub.add_parser("memory")
    mem_sub = mem.add_subparsers(dest="command")

    mem_sub.add_parser("list")

    mg = mem_sub.add_parser("get")
    mg.add_argument("id", type=int)

    mc = mem_sub.add_parser("create")
    mc.add_argument("--name", required=True)
    mc.add_argument("--type", required=True, choices=["user", "feedback", "project", "reference"])
    mc.add_argument("--desc", default="")
    mc.add_argument("--body-file")
    mc.add_argument("--project", help="project slug to pin this memory to (omit for global)")

    mu = mem_sub.add_parser("update")
    mu.add_argument("id", type=int)
    mu.add_argument("--name")
    mu.add_argument("--type", choices=["user", "feedback", "project", "reference"])
    mu.add_argument("--desc")
    mu.add_argument("--body-file")

    md = mem_sub.add_parser("delete")
    md.add_argument("id", type=int)

    # --- project ---
    proj = sub.add_parser("project")
    proj_sub = proj.add_subparsers(dest="command")

    proj_sub.add_parser("list")

    pg = proj_sub.add_parser("get")
    pg.add_argument("slug")

    pc = proj_sub.add_parser("create")
    pc.add_argument("--slug", required=True)
    pc.add_argument("--path", required=True)
    pc.add_argument("--desc", default="")

    pu = proj_sub.add_parser("update")
    pu.add_argument("slug")
    pu.add_argument("--path")
    pu.add_argument("--desc")

    pd = proj_sub.add_parser("delete")
    pd.add_argument("slug")

    pa = proj_sub.add_parser(
        "attach",
        help="register cwd to a slug (idempotent; creates the slug if new)",
    )
    pa.add_argument("--slug", help="target slug (defaults to cwd basename)")
    pa.add_argument("--cwd", help="path to attach (defaults to current dir)")

    # --- config ---
    cfg = sub.add_parser("config")
    cfg_sub = cfg.add_subparsers(dest="command")

    cfg_sub.add_parser("get-claude-md")

    cp = cfg_sub.add_parser("put-claude-md")
    cp.add_argument("file")

    # --- sync (no subcommand; flags drive direction) ---
    sync = sub.add_parser("sync", help="reconcile memories between local dir and hydra")
    sync.add_argument("--pull", action="store_true", help="download only")
    sync.add_argument("--push", action="store_true", help="upload only")
    sync.add_argument("--cwd", help="override cwd (hooks pass $PWD)")
    sync.add_argument("--dry-run", action="store_true")

    # --- capture-remote-url (Stop hook entry; reads payload from stdin) ---
    sub.add_parser(
        "capture-remote-url",
        help="hook: scan transcript for /remote-control URL and PUT it to Hydra",
    )

    return parser


DISPATCH = {
    ("memory", "list"): cmd_memory_list,
    ("memory", "get"): cmd_memory_get,
    ("memory", "create"): cmd_memory_create,
    ("memory", "update"): cmd_memory_update,
    ("memory", "delete"): cmd_memory_delete,
    ("project", "list"): cmd_project_list,
    ("project", "get"): cmd_project_get,
    ("project", "create"): cmd_project_create,
    ("project", "update"): cmd_project_update,
    ("project", "delete"): cmd_project_delete,
    ("project", "attach"): cmd_project_attach,
    ("config", "get-claude-md"): cmd_config_get_claude_md,
    ("config", "put-claude-md"): cmd_config_put_claude_md,
    ("sync", None): cmd_sync,
    ("capture-remote-url", None): cmd_capture_remote_url,
}


def main() -> None:
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    parser = build_parser()
    args = parser.parse_args()

    if not args.group:
        parser.print_help()
        sys.exit(1)

    # `sync` and `capture-remote-url` are leaf commands; others need a subcommand.
    command = getattr(args, "command", None)
    leaf_groups = {"sync", "capture-remote-url"}
    if args.group not in leaf_groups and not command:
        parser.print_help()
        sys.exit(1)

    handler = DISPATCH.get((args.group, command))
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
