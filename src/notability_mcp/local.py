"""Read the library from the Notability Mac app's own data, with no token and no network.

The app keeps everything in its sandbox container: an SQLite database of folders and note
metadata, one change log per note (`notes/<UUID>`, the same ops as a NoteBundle, see
`bundle.decode_local`) and the attachments by SHA-512 (`assets/`). Reading it needs Full Disk
Access for the process running `sync`.

Nothing in the container is ever written. The app keeps its database open in WAL mode, and even
a read-only SQLite connection takes locks and writes read marks into the `-shm` file, so the
database is never opened in place: it and its WAL are cloned into a temporary directory and the
clone is opened.
"""

import os
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from notability_mcp.store import Folder, NoteMeta


def library_dir() -> Path:
    if env := os.environ.get("NOTABILITY_LIBRARY_DIR"):
        return Path(env)
    return Path.home() / "Library" / "Containers" / "com.gingerlabs.Notability" / "Data" / "Library"


def data_dir() -> Path:
    return library_dir() / "Application Support" / "local-persistence-collab-production"


def cache_dir() -> Path:
    """This package's scratch space; never inside the app's container."""
    if env := os.environ.get("NOTABILITY_MCP_CACHE_DIR"):
        return Path(env)
    return Path.home() / "Library" / "Caches" / "notability-mcp"


def _signature(database: Path) -> tuple:
    """Size and mtime of the database and its WAL: they change whenever the app writes."""
    out = []
    for p in (database, database.with_name(database.name + "-wal")):
        st = p.stat() if p.exists() else None
        out.append((st.st_size, st.st_mtime_ns) if st else None)
    return tuple(out)


def _clone(src: Path, dst: Path) -> None:
    """An APFS clone (`cp -c`, instant and no extra space); a plain copy elsewhere."""
    try:
        subprocess.run(["/bin/cp", "-c", src, dst], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        shutil.copyfile(src, dst)


def _uuid_text(blob: object) -> str | None:
    return (
        str(uuid.UUID(bytes=blob)).upper() if isinstance(blob, bytes) and len(blob) == 16 else None
    )


@contextmanager
def snapshot() -> Generator[sqlite3.Connection]:
    """The app's database as of now, opened read-only from a private copy."""
    database = data_dir() / "local_persistence"
    if not database.exists():
        raise FileNotFoundError(
            f"No Notability database at {database}. Is the Mac app installed and signed in? "
            "Reading it needs Full Disk Access for this terminal or Python."
        )
    cache_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=cache_dir()) as tmp:
        copy = Path(tmp) / database.name
        for _ in range(3):  # a checkpoint between the two copies would pair mismatched files
            before = _signature(database)
            for suffix in ("", "-wal", "-shm"):
                Path(f"{copy}{suffix}").unlink(missing_ok=True)
            _clone(database, copy)
            wal = database.with_name(database.name + "-wal")
            if wal.exists():
                _clone(wal, Path(f"{copy}-wal"))
            if _signature(database) == before:
                break
        con = sqlite3.connect(copy)  # the copy is ours: reading the WAL needs a writable -shm
        try:
            con.execute("PRAGMA query_only = ON")
            # the app defines this function and an index on note_metadata uses it
            con.create_function("uuid_to_text", 1, _uuid_text, deterministic=True)
            yield con
        finally:
            con.close()


def _iso(value: object) -> str | None:
    """Database dates are UTC text such as '2026-10-30 18:22:32.261'."""
    if not isinstance(value, str) or not value:
        return None
    dt = datetime.fromisoformat(value)
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat(timespec="milliseconds")


def note_id(blob: bytes) -> str:
    """The app's 16-byte id as the lowercase UUID the web API uses."""
    return str(uuid.UUID(bytes=blob))


def read_library(con: sqlite3.Connection) -> tuple[list[Folder], list[NoteMeta]]:
    """Folders and notes; trashed notes are left out, as the web library leaves them out."""
    folders = [
        Folder(fid, title or "Untitled", "folder", parent)
        for fid, title, parent in con.execute(
            "SELECT id, title, parentId FROM organizers ORDER BY siblingOrder"
        )
    ]
    rows = con.execute(
        "SELECT n.id, n.title, n.created, n.modified, m.organizerId FROM note_metadata n"
        " LEFT JOIN organizer_note_membership m ON m.noteId = n.id AND m.userId = n.userId"
        " WHERE n.deleted IS NULL"
    )
    notes = [
        NoteMeta(
            id=note_id(blob),
            title=title or "Untitled",
            folder_id=folder,
            created_at=_iso(created),
            modified_at=_iso(modified) or "",
        )
        for blob, title, created, modified, folder in rows
        if isinstance(blob, bytes) and len(blob) == 16
    ]
    return folders, notes


def change_log(note: str) -> bytes | None:
    """A note's change log, or None if the app hasn't downloaded the note's content."""
    path = data_dir() / "notes" / note.upper()
    return path.read_bytes() if path.is_file() else None


def asset(sha512: str) -> bytes | None:
    """An attachment by SHA-512, or None if the app hasn't downloaded it."""
    if len(sha512) != 128 or not all(c in "0123456789abcdef" for c in sha512):
        raise ValueError(f"not an asset hash: {sha512!r}")
    path = data_dir() / "assets" / sha512
    return path.read_bytes() if path.is_file() else None
