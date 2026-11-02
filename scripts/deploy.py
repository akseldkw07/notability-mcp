"""Deploy this checkout to Claude Desktop and the sync job. Run from the post-merge hook on main
(.pre-commit-config.yaml), or by hand: `uv run python scripts/deploy.py`.

Claude Desktop runs this checkout's `notability-mcp serve` through mcp-compat, which stays
connected to Desktop and starts the server again when it's stopped. So deploying installs the
locked dependencies, stops running server processes (the next tool call starts the new code; no
Desktop restart) and runs the sync job once, so a changed index format is rebuilt right away.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / ".venv" / "bin" / "notability-mcp"
CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
COMPAT = Path.home() / "coding" / "mcp" / "mcp-utils" / "dist" / "compat" / "cli.js"
AGENT = "com.akseldkw.notability-mcp-sync"


def main() -> None:
    subprocess.run(["uv", "sync", "--locked"], cwd=ROOT, check=True)

    config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    entries = [
        name
        for name, entry in config.get("mcpServers", {}).items()
        if str(SERVER) in entry.get("args", [])
    ]
    if not entries:
        print(
            f'{CONFIG} has no server running this checkout. Add this under "mcpServers", then '
            "restart Claude Desktop:\n"
            + json.dumps(
                {
                    "notability": {
                        "command": shutil.which("node") or "node",  # Desktop has no PATH
                        "args": [str(COMPAT), "--name", "notability", "--", str(SERVER), "serve"],
                    }
                },
                indent=2,
            ),
            file=sys.stderr,
        )

    # only the server (argv: <this venv>/bin/python <SERVER> serve), never mcp-compat itself
    ps = subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True, check=True)
    stopped = 0
    for line in ps.stdout.splitlines():
        pid, _, args = line.strip().partition(" ")
        if args.strip().endswith(f"{SERVER} serve") and "compat/cli.js" not in args:
            os.kill(int(pid), signal.SIGTERM)
            stopped += 1

    agent = subprocess.run(
        ["launchctl", "kickstart", f"gui/{os.getuid()}/{AGENT}"], capture_output=True, text=True
    )
    print(
        f"Deployed to {', '.join(entries) or 'no Desktop entry'}: stopped {stopped} server "
        "process(es); mcp-compat starts the new code on the next tool call. "
        + ("Sync job started." if agent.returncode == 0 else f"Sync job {AGENT} isn't loaded."),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
