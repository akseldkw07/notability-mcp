"""Pull the library and changed notes from Notability into the local index."""

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from notability_mcp import api, appindex, local, store
from notability_mcp.bundle import Note, decode_bundle, decode_local
from notability_mcp.index import AppText, index_note
from notability_mcp.store import Folder, NoteMeta

log = logging.getLogger(__name__)
INDEX_VERSION = "2"  # bump when indexing changes, so the next sync re-indexes every note
LOCAL_INDEX_VERSION = "1"  # the same, for what only the local source indexes

type OcrMode = Literal["auto", "app", "vision"]


def _iso(ms: float | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="seconds") if ms else None


def parse_library(library: dict) -> tuple[list[Folder], list[NoteMeta]]:
    """Folders and notes of the user's private space; notes shared by others are skipped."""
    space = library.get("privateSpace") or {}
    # `organizers` is the legacy view of the same rows; `folders` wins where they disagree
    rows = {r["id"]: r for r in space.get("organizers", []) + space.get("folders", [])}
    folders = [
        Folder(
            r["id"],
            r.get("title") or "Untitled",
            (r.get("organizerType") or "folder").lower(),
            r.get("parentId"),
        )
        for r in rows.values()
    ]
    notes = [
        NoteMeta(
            id=n["id"],
            title=n.get("title") or "Untitled",
            folder_id=n.get("folderId"),
            created_at=_iso(n.get("createdAt")),
            # content edits move mostRecentOpTime; updatedAt also moves on renames and moves
            modified_at=_iso(max(n.get("mostRecentOpTime") or 0, n.get("updatedAt") or 0)) or "",
        )
        for n in space.get("notes", [])
    ]
    return folders, notes


def sync(con: sqlite3.Connection, token: str) -> tuple[int, int]:
    """Refresh metadata, then download and index notes that changed. Returns (indexed, failed)."""
    folders, notes = parse_library(api.fetch_library(token))
    store.save_library(con, folders, notes)
    store.require_index_version(con, INDEX_VERSION)
    store.set_meta(con, "source", "web")
    device = store.device_id(con)
    return _index_stale(
        con,
        {n.id: n.title for n in notes},
        lambda note_id: api.fetch_bundle(token, note_id, device),
        lambda note_id, sha: api.fetch_asset(token, note_id, sha),
        lambda _, bundle: decode_bundle(bundle),
    )


def sync_local(con: sqlite3.Connection, ocr: OcrMode = "auto") -> tuple[int, int]:
    """Like sync, but from the Mac app's own data (see local.py): no token, no network.

    Handwriting comes from the app's own recognition (appindex.py), along with image OCR and
    recording transcripts. `ocr`: "auto" also runs Vision on inked pages the app found no text
    in, "app" never runs Vision, "vision" ignores the app's handwriting text.

    A note is re-indexed when it changes or when the app rewrites its index files, which it
    does a few minutes after an edit. Notes whose content the app hasn't downloaded stay listed
    and are indexed by a later sync, once the app has them. Returns (indexed, failed)."""
    with local.snapshot() as app:
        folders, notes = local.read_library(app)
    store.save_library(con, folders, notes)
    # the two sources store different formats in notes.bundle, so switching source re-indexes
    # every note; so does switching OCR mode
    store.require_index_version(con, f"{INDEX_VERSION}/local{LOCAL_INDEX_VERSION}/{ocr}")
    store.set_meta(con, "source", "local")
    store.set_meta(con, "ocr", ocr)
    signatures = {n.id: appindex.signature(n.id) for n in notes}
    store.mark_stale(
        con, [i for i, sig in signatures.items() if store.get_meta(con, f"appindex:{i}") != sig]
    )
    return _index_stale(
        con,
        {n.id: n.title for n in notes},
        local.change_log,
        lambda note_id, sha: local.asset(sha),
        decode_local,
        app_text=lambda note: appindex.read(note, handwriting_text=ocr != "vision"),
        vision=ocr != "app",
        on_indexed=lambda note_id: store.set_meta(con, f"appindex:{note_id}", signatures[note_id]),
    )


def _index_stale(
    con: sqlite3.Connection,
    titles: dict[str, str],
    fetch_bundle: Callable[[str], bytes | None],
    fetch_asset: Callable[[str, str], bytes | None],
    decode: Callable[[str, bytes], Note],
    app_text: Callable[[Note], AppText] | None = None,
    vision: bool = True,
    on_indexed: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    stale = store.stale_notes(con)
    log.info("%d notes in library, %d to download and index", len(titles), len(stale))
    indexed = failed = 0
    for i, note_id in enumerate(stale, 1):
        try:
            if (bundle := fetch_bundle(note_id)) is None:
                log.info(
                    "[%d/%d] %s: content not downloaded yet", i, len(stale), titles.get(note_id)
                )
                continue
            pages = index_note(
                con,
                note_id,
                bundle,
                lambda sha, note_id=note_id: fetch_asset(note_id, sha),
                decode,
                app_text,
                vision,
            )
        except api.AuthError:
            raise
        except Exception:  # one unreadable note must not stop the rest
            failed += 1
            log.exception("[%d/%d] %s: failed", i, len(stale), titles.get(note_id))
            continue
        indexed += 1
        if on_indexed:
            on_indexed(note_id)
        log.info("[%d/%d] %s (%d pages)", i, len(stale), titles.get(note_id), pages)
    return indexed, failed
