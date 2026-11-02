"""The local index: note metadata, raw bundles and searchable page text in one SQLite file."""

import json
import os
import re
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path
from typing import Literal

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS folders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,          -- subject, folder or divider
    parent_id TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    folder_id TEXT,
    created_at TEXT,             -- ISO 8601, UTC
    modified_at TEXT NOT NULL,
    page_count INTEGER,          -- NULL until the bundle is indexed
    bundle BLOB,                 -- raw NoteBundle, for rendering pages on demand
    indexed_at TEXT              -- modified_at of the bundle that was indexed
);
-- PDFs that note pages are drawn on, shared between notes. A PDF stays here after the notes
-- using it are deleted.
CREATE TABLE IF NOT EXISTS assets (sha512 TEXT PRIMARY KEY, data BLOB NOT NULL);
-- note_id is UNINDEXED (FTS5 can't index it), so reading one note's pages scans the table:
-- about 2 ms for 6,000 pages.
-- Timed transcripts of a note's recordings (local source: Notability's own transcription).
CREATE TABLE IF NOT EXISTS transcripts (
    note_id TEXT NOT NULL,
    asset TEXT NOT NULL,         -- the audio attachment's SHA-512
    language TEXT NOT NULL,
    segments TEXT NOT NULL       -- JSON [[start, end, text], ...], seconds
);
CREATE VIRTUAL TABLE IF NOT EXISTS page_text USING fts5(
    title, text,
    note_id UNINDEXED, page UNINDEXED, source UNINDEXED, confidence UNINDEXED,
    tokenize = 'trigram'
);
"""


type Source = Literal["handwriting", "typed", "pdf", "image", "recording"]


@dataclass
class Folder:
    id: str
    name: str
    kind: str
    parent_id: str | None


@dataclass
class NoteMeta:
    id: str
    title: str
    folder_id: str | None
    created_at: str | None
    modified_at: str


@dataclass
class PageText:
    page: int  # 1-based; 0 for note-level text (typed, image OCR, recordings)
    source: Source
    text: str
    confidence: float | None = None


@dataclass
class Transcript:
    asset: str  # the audio attachment's SHA-512
    language: str
    segments: list[tuple[float, float, str]]  # (start, end, text), seconds from the start


@dataclass
class Hit:
    note_id: str
    title: str
    page: int
    source: Source
    snippet: str
    score: float


def default_path() -> Path:
    if env := os.environ.get("NOTABILITY_MCP_DB"):
        return Path(env)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "notability-mcp" / "index.db"


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        return sqlite3.connect(uri, uri=True, check_same_thread=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    return con


def save_library(con: sqlite3.Connection, folders: list[Folder], notes: list[NoteMeta]) -> None:
    """Replace folder and note metadata; drop notes that no longer exist."""
    with con:
        con.execute("DELETE FROM folders")
        con.executemany(
            "INSERT INTO folders VALUES (?, ?, ?, ?)",
            [(f.id, f.name, f.kind, f.parent_id) for f in folders],
        )
        con.executemany(
            "INSERT INTO notes (id, title, folder_id, created_at, modified_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT (id) DO UPDATE SET title = excluded.title,"
            " folder_id = excluded.folder_id, modified_at = excluded.modified_at",
            [(n.id, n.title, n.folder_id, n.created_at, n.modified_at) for n in notes],
        )
        con.execute(  # keep renamed titles searchable without reindexing
            "UPDATE page_text SET title = (SELECT title FROM notes WHERE id = page_text.note_id)"
            " WHERE title IS NOT (SELECT title FROM notes WHERE id = page_text.note_id)"
        )
        live = [n.id for n in notes]
        marks = ",".join("?" * len(live))
        con.execute(f"DELETE FROM notes WHERE id NOT IN ({marks})", live)
        con.execute(f"DELETE FROM page_text WHERE note_id NOT IN ({marks})", live)
        con.execute(f"DELETE FROM transcripts WHERE note_id NOT IN ({marks})", live)
        con.execute(
            "INSERT OR REPLACE INTO meta VALUES ('synced_at', ?)",
            (datetime.now(UTC).isoformat(timespec="seconds"),),
        )


def require_index_version(con: sqlite3.Connection, version: str) -> None:
    """Mark every note stale if the index was built by a different version of the indexer."""
    if get_meta(con, "index_version") == version:
        return
    with con:
        con.execute("UPDATE notes SET indexed_at = NULL")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('index_version', ?)", (version,))


def mark_stale(con: sqlite3.Connection, note_ids: list[str]) -> None:
    with con:
        con.executemany("UPDATE notes SET indexed_at = NULL WHERE id = ?", [(i,) for i in note_ids])


def stale_notes(con: sqlite3.Connection) -> list[str]:
    """Notes whose content changed since they were last indexed, newest first."""
    rows = con.execute(
        "SELECT id FROM notes WHERE indexed_at IS NOT modified_at ORDER BY modified_at DESC"
    )
    return [r[0] for r in rows]


def save_note(
    con: sqlite3.Connection,
    note_id: str,
    bundle: bytes,
    page_count: int,
    texts: list[PageText],
    transcripts: list[Transcript] | None = None,
) -> None:
    with con:
        con.execute("DELETE FROM transcripts WHERE note_id = ?", (note_id,))
        con.executemany(
            "INSERT INTO transcripts VALUES (?, ?, ?, ?)",
            [(note_id, t.asset, t.language, json.dumps(t.segments)) for t in transcripts or []],
        )
        (title,) = con.execute("SELECT title FROM notes WHERE id = ?", (note_id,)).fetchone()
        con.execute(
            "UPDATE notes SET bundle = ?, page_count = ?, indexed_at = modified_at WHERE id = ?",
            (bundle, page_count, note_id),
        )
        con.execute("DELETE FROM page_text WHERE note_id = ?", (note_id,))
        con.executemany(
            "INSERT INTO page_text VALUES (?, ?, ?, ?, ?, ?)",
            [(title, t.text, note_id, t.page, t.source, t.confidence) for t in texts],
        )


class QueryError(ValueError):
    pass


WEAK_MATCH = 0.6  # pages scoring below this only share letters with the query
CANDIDATES = 300  # pages pre-ranked by FTS that the word-level scoring looks at


def _words(text: str) -> list[str]:
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3]
    if not words:
        raise QueryError("Search needs at least one word of 3 or more letters.")
    return words


def fuzzy_query(text: str) -> str:
    """FTS5 query matching any 3-letter piece of any query word, for noisy OCR text."""
    words = _words(text)
    grams = dict.fromkeys(w[i : i + 3] for w in words for i in range(len(w) - 2))
    return " OR ".join(f'"{g}"' for g in grams)


def match_score(words: list[str], text: str) -> float:
    """How closely the text contains the query words, 0..1.

    Each query word is compared with its most similar word in the text, so OCR misspellings
    still score well; the result is the mean similarity weighted by word length.
    """
    vocabulary = set(re.findall(r"\w+", text.lower()))
    total = 0.0
    for word in words:
        if close := get_close_matches(word, vocabulary, n=1, cutoff=0.5):
            total += len(word) * SequenceMatcher(None, word, close[0]).ratio()
    return total / sum(map(len, words))


def search(
    con: sqlite3.Connection, query: str, *, limit: int = 10, offset: int = 0
) -> tuple[list[Hit], bool]:
    """Best-matching pages and whether more results exist past this page.

    FTS5 finds pages sharing 3-letter pieces with the query, which is nearly every page; those
    are re-ranked by match_score and pages below WEAK_MATCH are dropped.
    """
    words = _words(query)
    rows = con.execute(
        "SELECT note_id, title, page, source, text, snippet(page_text, 1, '«', '»', '…', 48)"
        " FROM page_text WHERE page_text MATCH ? ORDER BY bm25(page_text, 2.0, 1.0) LIMIT ?",
        (fuzzy_query(query), CANDIDATES),
    ).fetchall()
    scored = [
        Hit(n, t, p, s, snip, match_score(words, f"{t}\n{text}")) for n, t, p, s, text, snip in rows
    ]
    hits = sorted((h for h in scored if h.score >= WEAK_MATCH), key=lambda h: -h.score)
    return hits[offset : offset + limit], len(hits) > offset + limit


@dataclass
class NoteRow:
    note_id: str
    title: str
    folder: str | None  # folder path, e.g. "Fall 2026 / MATH 300"
    created_at: str | None
    modified_at: str
    page_count: int | None  # None until the note's content is indexed


def get_meta(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    with con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))


def device_id(con: sqlite3.Connection) -> str:
    """A stable random id for this install, sent with bundle requests like the web app does."""
    if existing := get_meta(con, "device_id"):
        return existing
    new = str(uuid.uuid4())
    with con:
        con.execute("INSERT INTO meta VALUES ('device_id', ?)", (new,))
    return new


def folder_paths(con: sqlite3.Connection) -> dict[str, str]:
    rows = {
        fid: (name, parent)
        for fid, name, parent in con.execute("SELECT id, name, parent_id FROM folders")
    }

    def path(fid: str, depth: int = 0) -> str:
        name, parent = rows[fid]
        if parent in rows and depth < 32:  # depth guard: a corrupt parent chain can loop
            return f"{path(parent, depth + 1)} / {name}"
        return name

    return {fid: path(fid) for fid in rows}


def all_notes(con: sqlite3.Connection) -> list[NoteRow]:
    paths = folder_paths(con)
    rows = con.execute(
        "SELECT id, title, folder_id, created_at, modified_at, page_count FROM notes"
    )
    return [NoteRow(i, t, paths.get(f or ""), c, m, n) for i, t, f, c, m, n in rows]


def get_note(con: sqlite3.Connection, note_id: str) -> NoteRow | None:
    return next((n for n in all_notes(con) if n.note_id == note_id), None)


def note_pages(con: sqlite3.Connection, note_id: str) -> list[PageText]:
    rows = con.execute(
        "SELECT page, source, text, confidence FROM page_text"
        " WHERE note_id = ? ORDER BY page, source",
        (note_id,),
    )
    return [PageText(p, s, t, c) for p, s, t, c in rows]


def note_bundle(con: sqlite3.Connection, note_id: str) -> bytes | None:
    row = con.execute("SELECT bundle FROM notes WHERE id = ?", (note_id,)).fetchone()
    return row[0] if row else None


def note_transcripts(con: sqlite3.Connection, note_id: str) -> list[Transcript]:
    rows = con.execute(
        "SELECT asset, language, segments FROM transcripts WHERE note_id = ? ORDER BY rowid",
        (note_id,),
    )
    return [
        Transcript(a, lang, [(start, end, text) for start, end, text in json.loads(segs)])
        for a, lang, segs in rows
    ]


def asset(con: sqlite3.Connection, sha512: str) -> bytes | None:
    row = con.execute("SELECT data FROM assets WHERE sha512 = ?", (sha512,)).fetchone()
    return row[0] if row else None


def save_asset(con: sqlite3.Connection, sha512: str, data: bytes) -> None:
    with con:
        con.execute("INSERT OR REPLACE INTO assets VALUES (?, ?)", (sha512, data))
