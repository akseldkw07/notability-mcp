"""Text the Notability Mac app has already recognized: handwriting, image OCR and transcripts.

The app keeps search indexes per note in `NotesSearchIndex/<Indexer>/<UUID>/`, as binary plists
until about April 2026 and FlatBuffers files since; where both exist the newer file wins. There
is no published schema. The FlatBuffers layouts below were worked out from the files and checked
against the plists they replaced:

- Every file is an envelope `{0: u8, 1: u8, 2: payload table, 3: string}`. The two bytes look like
  (oldest compatible, current) format versions, and unknown ones are refused (FormatError), so a
  changed format fails loudly instead of returning garbage.
- HandwritingIndex.fb: `{0: [page {0: page id, 1: text, 2: character boxes, 3: sha256}]}`, pages
  with text only. The page id is the page's id in the note's change log (bundle.SeqId), which
  gives its page number: 2,628 of 2,628 pages matched on a real library.
- ImageIndex.fb: `{0: [image {0: id (2 x u32), 1: text, 2: character boxes, 3: string}]}`.
- RecordingIndex.fb: `{0: titles, 1: [job {0: audio SHA-512 (64 bytes), 1: language,
  2: [segment {0: text, 1: start (f64 s), 2: end}]}]}`.
"""

import logging
import plistlib
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from notability_mcp import local
from notability_mcp.bundle import Note, _Table
from notability_mcp.index import AppText
from notability_mcp.store import Transcript

type Kind = Literal["handwriting", "image", "recording"]

KNOWN_VERSIONS: dict[Kind, tuple[int, int]] = {
    "handwriting": (4, 5),
    "image": (1, 2),
    "recording": (2, 3),
}
INDEXERS: dict[Kind, tuple[str, str, str]] = {  # directory, plist name, FlatBuffers name
    "handwriting": ("NoteHandwritingIndexer", "index.plist", "HandwritingIndex.fb"),
    "image": ("NoteImageIndexer", "ImageTextIndex.plist", "ImageIndex.fb"),
    "recording": ("NoteRecordingIndexer", "RecordingIndex.plist", "RecordingIndex.fb"),
}


log = logging.getLogger(__name__)


class FormatError(ValueError):
    """An index file this module can't read, e.g. after an app update changed the format."""


def root() -> Path:
    return local.library_dir() / "NotesSearchIndex"


def _pick(kind: Kind, note_id: str) -> tuple[Literal["plist", "fb"], Path] | None:
    """The FlatBuffers file if it's at least as new as the plist, else the plist."""
    folder, plist, fb = INDEXERS[kind]
    base = root() / folder / note_id.upper()
    p, f = base / plist, base / fb
    pm = p.stat().st_mtime if p.is_file() else None
    fm = f.stat().st_mtime if f.is_file() else None
    if fm is not None and (pm is None or fm >= pm):
        return "fb", f
    return ("plist", p) if pm is not None else None


def envelope_versions(buf: bytes) -> tuple[int, int] | None:
    try:
        env = _Table(buf, struct.unpack_from("<I", buf, 0)[0])
        return env.scalar(0, "<B", 0), env.scalar(1, "<B", 0)
    except (struct.error, IndexError):
        return None


def _payload(path: Path, kind: Kind) -> _Table:
    buf = path.read_bytes()
    versions = envelope_versions(buf)
    if versions is None:
        raise FormatError(f"{path.name}: not a readable index file")
    if versions != KNOWN_VERSIONS[kind]:
        known = ".".join(map(str, KNOWN_VERSIONS[kind]))
        raise FormatError(
            f"unknown {kind} index format {versions[0]}.{versions[1]} (known: {known})"
        )
    body = _Table(buf, struct.unpack_from("<I", buf, 0)[0]).table(2)
    if body is None:
        raise FormatError(f"{path.name}: no payload")
    return body


def _plist(path: Path) -> dict:
    try:
        data = plistlib.loads(path.read_bytes())
    except Exception as e:
        raise FormatError(f"{path.name}: {e}") from e
    return data if isinstance(data, dict) else {}


def _read[T](path: Path, fn: Callable[[], T]) -> T:
    """Run a decoder, turning stray errors from a corrupt file into FormatError."""
    try:
        return fn()
    except FormatError:
        raise
    except (struct.error, IndexError, UnicodeDecodeError, ValueError, KeyError) as e:
        raise FormatError(f"{path.name}: {e}") from e


def handwriting(note: Note) -> dict[int, str] | None:
    """Recognized handwriting by page number; None if the app hasn't indexed the note."""
    if (picked := _pick("handwriting", note.id)) is None:
        return None
    fmt, path = picked
    numbers = {page.id: page.number for page in note.pages}

    def fb() -> dict[int, str]:
        out = {}
        for page in _payload(path, "handwriting").tables(0):
            seq, text = page.seq(0), (page.string(1) or "").strip()
            if text and seq in numbers:  # pages deleted since indexing have no number
                out[numbers[seq]] = text
        return out

    def plist() -> dict[int, str]:
        pages = _plist(path).get("pages") or {}
        return {
            int(k) + 1: text
            for k, v in pages.items()
            if (text := str((v or {}).get("text", "")).strip()) and int(k) < len(note.pages)
        }

    return _read(path, fb if fmt == "fb" else plist)


def image_text(note_id: str) -> list[str]:
    """OCR of each image or photo in the note that has text."""
    if (picked := _pick("image", note_id)) is None:
        return []
    fmt, path = picked

    def fb() -> list[str]:
        return [(im.string(1) or "").strip() for im in _payload(path, "image").tables(0)]

    def plist() -> list[str]:
        return [str(v).strip() for v in (_plist(path).get("imageAssetText") or {}).values()]

    return [t for t in _read(path, fb if fmt == "fb" else plist) if t]


def transcripts(note_id: str) -> list[Transcript]:
    """Notability's transcripts of the note's recordings."""
    if (picked := _pick("recording", note_id)) is None:
        return []
    fmt, path = picked

    def fb() -> list[Transcript]:
        out = []
        for job in _payload(path, "recording").tables(1):
            digest = job._unpack(0, struct.Struct("<64s"))
            segments = [
                (s.scalar(1, "<d", 0.0), s.scalar(2, "<d", 0.0), (s.string(0) or "").strip())
                for s in job.tables(2)
            ]
            out.append(Transcript(digest[0].hex() if digest else "", job.string(1) or "", segments))
        return out

    def plist() -> list[Transcript]:
        jobs = _plist(path).get("transcriptionJobs") or {}
        return [
            Transcript(
                str(job.get("sha512Hash", "")).lower(),
                str(job.get("transcriptionLanguage", "")),
                [
                    (float(s["startTime"]), float(s["endTime"]), str(s.get("text", "")).strip())
                    for s in job.get("segments") or []
                ],
            )
            for _, job in sorted(jobs.items(), key=lambda kv: int(kv[0]))
        ]

    return _read(path, fb if fmt == "fb" else plist)


def signature(note_id: str) -> str:
    """Changes whenever the app rewrites any of the note's index files."""
    parts = []
    for folder, plist, fb in INDEXERS.values():
        for name in (plist, fb):
            p = root() / folder / note_id.upper() / name
            if p.is_file():
                st = p.stat()
                parts.append(f"{folder}/{name}:{st.st_size}:{st.st_mtime_ns}")
    return "|".join(parts)


def read(note: Note, *, handwriting_text: bool = True) -> AppText:
    """Everything the app recognized for a note. An unreadable file is logged and skipped:
    unreadable handwriting leaves the pages to Vision OCR."""
    out = AppText()
    for name, fn in (
        ("handwriting", lambda: handwriting(note) if handwriting_text else None),
        ("images", lambda: image_text(note.id)),
        ("transcripts", lambda: transcripts(note.id)),
    ):
        try:
            setattr(out, name, fn())
        except (FormatError, OSError) as e:
            log.warning("%s: %s index unreadable: %s", note.id, name, e)
    return out
