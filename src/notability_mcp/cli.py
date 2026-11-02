"""Command-line entry point: `notability-mcp <command>`."""

import argparse
import getpass
import json
import logging
import os
import sys
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

KEYRING_SERVICE, KEYRING_USER = "notability-mcp", "nb-auth-token"
LOGIN_HELP = """\
notability-mcp reads your notes with your own Notability session token.
To copy it: sign in at https://notability.com/app, open the browser's developer tools,
then Application (Chrome) or Storage (Firefox/Safari) > Cookies > https://notability.com,
and copy the value of `nb-auth-token`. It is stored in your system keychain.
"""


def _token() -> str | None:
    return os.environ.get("NOTABILITY_TOKEN") or keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def claude_desktop_config() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ["APPDATA"])
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "Claude" / "claude_desktop_config.json"


def server_command() -> list[str]:
    """How a desktop app, which doesn't see the shell's PATH, should start the server."""
    return [str(Path(sys.argv[0]).resolve()), "serve"]


def setup() -> None:
    """Add the server to Claude Desktop's config, keeping everything else in it."""
    path = claude_desktop_config()
    if not path.parent.exists():
        sys.exit(f"Claude Desktop doesn't seem to be installed (no {path.parent}).")
    try:
        config = json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError as e:
        sys.exit(f"{path} isn't valid JSON ({e}). Fix it, then run setup again.")
    command, *args = server_command()
    config.setdefault("mcpServers", {})["notability"] = {"command": command, "args": args}
    # the config can hold other servers' API keys: keep its permissions (private if it's new),
    # and write a temp file first so a crash can't leave Claude Desktop with a broken config
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "w") as f:
        f.write(json.dumps(config, indent=2) + "\n")
    tmp.replace(path)
    print(f"Added notability to {path}, running: {' '.join([command, *args])}", file=sys.stderr)
    print("Restart Claude Desktop to load it.", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="notability-mcp")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the MCP server on stdio")
    sub.add_parser("login", help="store your Notability session token in the system keychain")
    sub.add_parser("logout", help="remove the stored token")
    sync_cmd = sub.add_parser("sync", help="download changed notes and update the search index")
    sync_cmd.add_argument(
        "--source",
        choices=["web", "local"],
        default=os.environ.get("NOTABILITY_MCP_SOURCE", "web"),
        help="web: Notability's web API, with your token (default). local: the Notability Mac "
        "app's own data, with no token or network; needs Full Disk Access.",
    )
    sync_cmd.add_argument(
        "--ocr",
        choices=["auto", "app", "vision"],
        default="auto",
        help="local source only. auto: the app's own handwriting recognition, and Vision for "
        "inked pages it found no text in (default). app: never run Vision. vision: Vision only.",
    )
    sub.add_parser("setup", help="add the server to Claude Desktop")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # stderr; stdout is MCP's

    from notability_mcp import api, store

    try:
        match args.command:
            case "serve":
                from notability_mcp.server import mcp

                mcp.run()
            case "login":
                print(LOGIN_HELP, file=sys.stderr)
                token = getpass.getpass("nb-auth-token (input hidden): ").strip()
                library = api.fetch_library(token)
                keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)
                count = len((library.get("privateSpace") or {}).get("notes", []))
                print(f"Logged in: {count} notes. Next: notability-mcp sync", file=sys.stderr)
            case "logout":
                try:
                    keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
                except PasswordDeleteError:
                    sys.exit("No token was stored.")
                print("Token removed from the system keychain.", file=sys.stderr)
            case "sync" if args.source == "local":
                from notability_mcp.sync import sync_local

                try:
                    indexed, failed = sync_local(store.connect(store.default_path()), args.ocr)
                except (FileNotFoundError, PermissionError) as e:
                    sys.exit(str(e))
                print(f"Done: {indexed} notes indexed, {failed} failed.", file=sys.stderr)
            case "sync":
                from notability_mcp.sync import sync

                if not (token := _token()):
                    sys.exit("Not logged in. Run `notability-mcp login` first.")
                indexed, failed = sync(store.connect(store.default_path()), token)
                print(f"Done: {indexed} notes indexed, {failed} failed.", file=sys.stderr)
            case "setup":
                setup()
                if not store.default_path().exists():
                    print("Next: notability-mcp login, then notability-mcp sync", file=sys.stderr)
    except api.ApiError as e:
        sys.exit(str(e))
    except KeyringError as e:  # e.g. Linux without a Secret Service keyring
        sys.exit(f"Couldn't use the system keychain: {e}. Set NOTABILITY_TOKEN instead.")
