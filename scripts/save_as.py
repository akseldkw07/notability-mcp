"""Save notes with Notability's own File > Export > PDF…, as references for the parity test.

A dev tool, not part of the server: it takes over the screen. For each note it opens the note
in the app (notability://), clicks File > Export > PDF… and saves into the parity folder
(~/Library/Caches/notability-mcp/parity/<note id>.pdf; outside the repo, as these are your
notes). It needs Accessibility permission for the app that runs it (your terminal), and asks
before it starts.

    uv run scripts/save_as.py --sample 12         # notes picked to cover what we render
    uv run scripts/save_as.py --note <id> ...     # particular notes
    uv run scripts/save_as.py --inspect           # print the export sheet's controls, then cancel
    NOTABILITY_MCP_PARITY=1 uv run pytest tests/test_parity.py -v

It never writes inside Notability's container: the app does the export, into the parity folder.
"""

import argparse
import subprocess
import sys
import time
import uuid
from pathlib import Path

from notability_mcp import local, parity
from notability_mcp.bundle import LINE, POLYGON, TEXT_BLOCK, Note, decode_local

WINDOW_TIMEOUT = 20.0  # seconds for the note's window to come up
SAVE_TIMEOUT = 120.0  # seconds for the app to write a long note's PDF
SETTLE = 5  # seconds to let the note load its images before exporting: too soon, and the
# app's PDF shows placeholders for them

# AppleScript run with the arguments after `--`; System Events needs Accessibility permission.
_CLICK_EXPORT = """
on run argv
  set noteTitle to item 1 of argv
  tell application "System Events" to tell process "Notability"
    set frontmost to true
    set deadline to (current date) + %(timeout)d
    repeat until (exists window 1) and (name of window 1 is noteTitle)
      if (current date) > deadline then error "the note's window didn't open: " & noteTitle
      delay 0.25
    end repeat
    delay %(settle)d
    set fileMenu to menu 1 of menu bar item "File" of menu bar 1
    click menu item "PDF..." of menu 1 of menu item "Export" of fileMenu
    set deadline to (current date) + 10
    repeat until exists sheet 1 of window 1
      if (current date) > deadline then error "no export sheet"
      delay 0.25
    end repeat
  end tell
end run
"""

# In the save sheet: go to the folder (Cmd-Shift-G), name the file, save, replace if asked.
_SAVE_TO = """
on run argv
  set folderPath to item 1 of argv
  set fileName to item 2 of argv
  tell application "System Events" to tell process "Notability"
    set frontmost to true
    keystroke "g" using {command down, shift down}
    delay 0.6
    keystroke folderPath
    delay 0.3
    key code 36
    delay 0.6
    keystroke "a" using {command down}
    keystroke fileName
    delay 0.3
    key code 36
    delay 1
    if exists sheet 1 of sheet 1 of window 1 then
      click button "Replace" of sheet 1 of sheet 1 of window 1
    end if
  end tell
end run
"""

_INSPECT = """
on describe(e, depth)
  set out to ""
  tell application "System Events" to try
    repeat with c in UI elements of e
      set line_ to ""
      repeat depth times
        set line_ to line_ & "  "
      end repeat
      set r to role of c
      set t to ""
      try
        set t to title of c as text
      end try
      set d to ""
      try
        set d to description of c as text
      end try
      set out to out & line_ & r & " | " & t & " | " & d & linefeed
      if depth < 6 then set out to out & my describe(c, depth + 1)
    end repeat
  end try
  return out
end describe

tell application "System Events" to tell process "Notability"
  set frontmost to true
  set fileMenu to menu 1 of menu bar item "File" of menu bar 1
  click menu item "PDF..." of menu 1 of menu item "Export" of fileMenu
  delay 1.5
  set out to my describe(sheet 1 of window 1, 0)
  key code 53
  return out
end tell
"""


def _osascript(script: str, *args: str) -> str:
    done = subprocess.run(
        ["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=180
    )
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "osascript failed")
    return done.stdout


def _open(note_id: str) -> None:
    subprocess.run(["open", f"notability://note/{str(uuid.UUID(note_id)).upper()}"], check=True)


def _wait_for(path: Path, timeout: float) -> bool:
    """True once `path` exists and its size has stopped changing."""
    deadline, last = time.monotonic() + timeout, -1
    while time.monotonic() < deadline:
        size = path.stat().st_size if path.exists() else -1
        if size > 0 and size == last:
            return True
        last = size
        time.sleep(0.5)
    return False


def save_as(note_id: str, title: str, out: Path) -> Path:
    """Have the app export one note as a PDF into `out`, named by note id."""
    target = out / f"{note_id}.pdf"
    fresh = out / f"{note_id}.new.pdf"  # the old reference stays until the new one is in
    fresh.unlink(missing_ok=True)
    _open(note_id)
    _osascript(_CLICK_EXPORT % {"timeout": WINDOW_TIMEOUT, "settle": SETTLE}, title)
    _osascript(_SAVE_TO, str(out), fresh.stem)
    if not _wait_for(fresh, SAVE_TIMEOUT):
        raise RuntimeError(f"no PDF at {fresh} after {SAVE_TIMEOUT:.0f} s")
    fresh.replace(target)
    return target


def _features(note: Note) -> set[str]:
    """What a note would exercise in our renderer."""
    out: set[str] = set()
    if note.body and any(r.text.strip() for r in note.body):
        out.add("body text")
        if any(r.style.indent or r.style.align for r in note.body):
            out.add("paragraph styles")
        if any(r.style.bold or r.style.italic or r.style.color for r in note.body):
            out.add("character styles")
        if note.align_to_lines:
            out.add("aligned to lines")
    for page in note.pages:
        if page.paper and page.paper.flair is not None:
            out.add(f"paper flair {page.paper.flair}")
        if page.pdf:
            out.add("pdf page")
        if page.shapes:
            out.add("shapes")
            out.update(
                {LINE: "lines", POLYGON: "polygons"}.get(s.kind, "ellipses") for s in page.shapes
            )
        if any(b.kind == TEXT_BLOCK for b in page.blocks):
            out.add("text boxes")
        if any(b.kind == 1 for b in page.blocks):
            out.add("images")
        if any(i.tool == 2 for i in page.inks):
            out.add("highlighter")
    return out


def sample(count: int, max_pages: int = 12) -> list[tuple[str, str]]:
    """(note id, title) of up to `count` short notes that between them cover the most
    features: greedily, the note adding the most features not yet covered."""
    with local.snapshot() as con:
        _, notes = local.read_library(con)
    candidates = []
    for meta in notes:
        log = local.change_log(meta.id)
        if not log or not meta.title:
            continue
        try:
            note = decode_local(meta.id, log)
        except Exception:  # an undecodable note is a bug for elsewhere; skip it here
            continue
        if len(note.pages) <= max_pages:
            candidates.append((meta.id, meta.title, _features(note)))
    chosen: list[tuple[str, str]] = []
    covered: set[str] = set()
    while candidates and len(chosen) < count:
        best = max(candidates, key=lambda c: (len(c[2] - covered), len(c[2])))
        candidates.remove(best)
        chosen.append((best[0], best[1]))
        covered |= best[2]
        print(f"  {best[1]!r}: {', '.join(sorted(best[2])) or 'plain ink'}")
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--note", action="append", default=[], help="a note id (repeatable)")
    ap.add_argument("--sample", type=int, help="pick this many notes covering the most features")
    ap.add_argument("--inspect", action="store_true", help="print the export sheet's controls")
    ap.add_argument("--yes", action="store_true", help="don't ask before taking over the screen")
    args = ap.parse_args()

    if args.inspect:
        print(_osascript(_INSPECT))
        return 0
    with local.snapshot() as con:
        _, notes = local.read_library(con)
    titles = {n.id: n.title or "" for n in notes}
    picked = [(n, titles.get(n.lower(), "")) for n in args.note]
    if args.sample:
        print("Sample:")
        picked += sample(args.sample)
    if not picked:
        ap.error("give --note or --sample")
    out = parity.parity_dir()
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    if not args.yes:
        answer = input(
            f"This brings Notability to the front and clicks through File > Export > PDF for "
            f"{len(picked)} note(s); don't type or click until it's done. Continue? [y/N] "
        )
        if answer.strip().lower() != "y":
            return 1
    failed = 0
    for note_id, title in picked:
        try:
            path = save_as(note_id, title, out)
            print(f"saved {title!r} -> {path}")
        except (RuntimeError, subprocess.SubprocessError, ValueError) as e:
            failed += 1
            print(f"FAILED {title!r}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
