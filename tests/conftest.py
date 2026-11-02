import json
import sqlite3
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from notability_mcp.bundle import Note, _Table, build_note, read_ops

FIXTURES = Path(__file__).parent / "fixtures"


def event_ops(name: str):
    """Ops from a server-acknowledged AppendOpsEvent captured by notability-cli."""
    fixture = json.loads((FIXTURES / f"{name}.json").read_text())
    return read_ops(bytes.fromhex(fixture["append_ops_event_hex"]), ops_field=0)


@pytest.fixture
def ink_note() -> Note:
    """One pen stroke at (80, 300) on the implicit first page, streamed in two chunks."""
    return build_note("ink-note", event_ops("25_ink_pen") + event_ops("26_ink_add_path"))


def as_local_log(bundle: bytes) -> bytes:
    """A NoteBundle's ops laid out like the Mac app's per-note change log (`notes/<UUID>`):
    a root table whose field 0 is the ops vector. The bundle is appended unchanged and the
    new root points into it."""
    root = _Table(bundle, struct.unpack_from("<I", bundle, 0)[0])
    vector = root._deref(6)
    assert vector is not None
    base = 24  # header below, padded to 8
    header = struct.pack("<I", 12)  # root table at 12
    header += struct.pack("<HHH", 6, 8, 4) + b"\0\0"  # vtable at 4: one field, at +4
    header += struct.pack("<iI", 8, base + vector - 16)  # table: vtable 8 back; field 0
    return header.ljust(base, b"\0") + bundle


@pytest.fixture
def local_log():
    return as_local_log


CONTAINER_SCHEMA = """
CREATE TABLE organizers ("id" TEXT NOT NULL, "userId" TEXT NOT NULL, "title" TEXT NOT NULL,
    "parentId" TEXT, "siblingOrder" DOUBLE NOT NULL, "hexColor" TEXT NOT NULL, "emoji" TEXT,
    "isLocked" BOOLEAN NOT NULL, "updatedAt" INTEGER NOT NULL, PRIMARY KEY ("id", "userId"));
CREATE TABLE note_metadata ("id" TEXT NOT NULL, "userId" TEXT NOT NULL, "created" INTEGER NOT NULL,
    "modified" INTEGER NOT NULL, "lastOpened" INTEGER, "title" TEXT, "isFavorite" BOOLEAN NOT NULL,
    "hasRecordings" BOOLEAN NOT NULL, "isDownloaded" BOOLEAN NOT NULL, "deleted" DATE,
    PRIMARY KEY ("id", "userId"));
CREATE TABLE organizer_note_membership ("noteId" TEXT NOT NULL, "userId" TEXT NOT NULL,
    "organizerId" TEXT NOT NULL, PRIMARY KEY ("noteId", "userId"));
CREATE TABLE asset_note_associations ("assetHash" TEXT NOT NULL, "noteId" TEXT NOT NULL,
    "userId" TEXT NOT NULL, PRIMARY KEY ("assetHash", "noteId", "userId"));
"""
LECTURE = "6a1d9e4c-0b7f-4c1e-9a52-3f0d2b8e7c11"  # the synthetic bundle's typed text
TRASHED = "0b5e7c2a-4d1f-4e8b-8c3a-9f2e1d0c4b7a"
NOT_DOWNLOADED = "c3f8a1d2-7e4b-4a9c-b5d6-e1f2a3b4c5d6"


@dataclass
class Container:
    library: Path
    store: Path  # local-persistence-collab-production
    database: Path

    def edit(self, sql: str, *args) -> sqlite3.Connection:
        """Write as the running app does: WAL mode, nothing checkpointed. The connection is
        returned open, like the app's, since closing the last one checkpoints the WAL."""
        con = sqlite3.connect(self.database)
        con.execute("PRAGMA wal_autocheckpoint = 0")
        with con:
            con.execute(sql, args)
        return con

    def listing(self) -> list[str]:
        return sorted(str(p.relative_to(self.library)) for p in self.library.rglob("*"))


def _blob(note_id: str) -> bytes:
    return uuid.UUID(note_id).bytes


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    """A synthetic Notability container with the app's schema: two folders, a note with a change
    log, a trashed note and a note whose content isn't downloaded."""
    library = tmp_path / "Library"
    root = library / "Application Support" / "local-persistence-collab-production"
    (root / "notes").mkdir(parents=True)
    (root / "assets").mkdir()
    con = sqlite3.connect(root / "local_persistence")
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(CONTAINER_SCHEMA)
    u = "user-1"
    con.executemany(
        "INSERT INTO organizers VALUES (?, ?, ?, ?, ?, 'ffffff', NULL, 0, 0)",
        [("F1", u, "Fall 2026", None, 1.0), ("S1", u, "MATH 300", "F1", 1.0)],
    )
    notes = [
        (LECTURE, "Lecture 1", "2026-09-01 10:00:00.000", None, 1),
        (TRASHED, "Old", "2026-08-01 10:00:00.000", "2026-08-02 10:00:00.000", 1),
        (NOT_DOWNLOADED, "Elsewhere", "2026-09-02 10:00:00.000", None, 0),
    ]
    con.executemany(
        "INSERT INTO note_metadata"
        " VALUES (?, ?, '2026-08-01 09:00:00.000', ?, NULL, ?, 0, 0, ?, ?)",
        [(_blob(i), u, mod, title, dl, deleted) for i, title, mod, deleted, dl in notes],
    )
    con.execute("INSERT INTO organizer_note_membership VALUES (?, ?, 'S1')", (_blob(LECTURE), u))
    con.commit()
    con.close()
    bundle = (FIXTURES / "synthetic_bundle.bin").read_bytes()
    (root / "notes" / LECTURE.upper()).write_bytes(as_local_log(bundle))
    (root / "notes" / TRASHED.upper()).write_bytes(as_local_log(bundle))
    monkeypatch.setenv("NOTABILITY_LIBRARY_DIR", str(library))
    monkeypatch.setenv("NOTABILITY_MCP_CACHE_DIR", str(tmp_path / "cache"))
    return Container(library, root, root / "local_persistence")
