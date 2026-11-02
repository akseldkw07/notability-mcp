"""The MCP server: read-only tools over the local note index."""

import sqlite3
from datetime import UTC, datetime
from functools import cache
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from notability_mcp import store
from notability_mcp.bundle import Note, decode_bundle, decode_local
from notability_mcp.render import MAX_EDGE, render_page

INSTRUCTIONS = """\
Read-only access to the user's Notability notes. Most notes are handwritten, so their text \
comes from OCR: it is approximate and math is often garbled.

Find notes with notability_search_notes (fuzzy, returns matching pages) or \
notability_list_notes (browse by folder or recency). Read OCR text with notability_read_note, \
and look at a page with notability_get_page_image whenever exact wording, math or diagrams \
matter. Content is as fresh as the user's last `notability-mcp sync` (see synced_at)."""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
READ_NOTE_MAX_CHARS = 25_000  # Claude Code warns above ~10k tokens per tool result
LOW_CONFIDENCE = 0.6
CLOSE_MATCH = 0.8  # search score from which a hit is worth trusting without a warning


mcp = MCPServer("notability", instructions=INSTRUCTIONS)


@cache
def _db() -> sqlite3.Connection:
    """The index, opened read-only on first use and kept open for the life of the process."""
    path = store.default_path()
    if not path.exists():
        raise ToolError(f"No note index at {path}. Ask the user to run `notability-mcp sync`.")
    return store.connect(path, readonly=True)


def _note(con: sqlite3.Connection, note_id: str) -> store.NoteRow:
    if note := store.get_note(con, note_id):
        return note
    raise ToolError(
        f"No note with id {note_id!r}. Find ids with notability_search_notes or "
        "notability_list_notes."
    )


def _decode(con: sqlite3.Connection, note_id: str, bundle: bytes) -> Note:
    """Stored bundles are NoteBundles from the web, or the Mac app's change logs (local sync)."""
    if store.get_meta(con, "source") == "local":
        return decode_local(note_id, bundle)
    return decode_bundle(bundle)


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isdigit():
        raise ToolError("Invalid cursor. Pass next_cursor from a previous result, or omit it.")
    return int(cursor)


class SearchHit(BaseModel):
    note_id: str
    title: str
    folder: str | None
    page: int | None = Field(description="1-based page; null for typed text of the whole note")
    source: store.Source
    snippet: str = Field(description="Matching text; matched letters are marked «like this»")
    score: float = Field(description="Match quality 0..1; 0.8 and above is a close match")


class SearchResult(BaseModel):
    hits: list[SearchHit]
    next_cursor: str | None = None
    notice: str | None = None


@mcp.tool(name="notability_search_notes", title="Search notes", annotations=READ_ONLY)
def search_notes(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Plain words, e.g. 'harmonic conjugate'. Each word is matched fuzzily "
            "to cope with OCR errors; no quotes or operators. Few distinctive words work best.",
        ),
    ],
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
    cursor: Annotated[str | None, Field(description="next_cursor of a previous call")] = None,
) -> SearchResult:
    """Find note pages whose handwriting, typed text or note title match the query, best first.

    Use for a topic, term or phrase across all notes. Each hit is one page with a snippet; open
    it with notability_read_note or notability_get_page_image. To browse by folder or date,
    use notability_list_notes instead.
    """
    con, offset = _db(), _offset(cursor)
    try:
        hits, more = store.search(con, query, limit=limit, offset=offset)
    except store.QueryError as e:
        raise ToolError(str(e)) from e
    folders = {n.note_id: n.folder for n in store.all_notes(con)}
    result = SearchResult(
        hits=[
            SearchHit(
                note_id=h.note_id,
                title=h.title,
                folder=folders.get(h.note_id),
                page=h.page or None,
                source=h.source,
                snippet=h.snippet,
                score=round(h.score, 2),
            )
            for h in hits
        ],
        next_cursor=str(offset + limit) if more else None,
    )
    if not hits:
        result.notice = (
            "No matches. Try other or fewer words, or browse with notability_list_notes; "
            "pages with only drawings or math may have little OCR text."
        )
    elif offset == 0 and hits[0].score < CLOSE_MATCH:
        result.notice = (
            "No close matches; these pages only loosely resemble the query, often because OCR "
            "garbled the handwriting. Check snippets or page images before relying on them, "
            "or search for a single distinctive word."
        )
    return result


class NoteSummary(BaseModel):
    note_id: str
    title: str
    folder: str | None
    page_count: int | None = Field(description="null until the note's content is synced")
    created_at: str | None
    modified_at: str


class NoteList(BaseModel):
    notes: list[NoteSummary]
    total: int = Field(description="Notes matching the filters, across all pages of results")
    next_cursor: str | None = None
    folders: list[str] = Field(description="Every folder path in the library")
    synced_at: str | None = Field(description="When the index was last synced (UTC)")


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@mcp.tool(name="notability_list_notes", title="List notes", annotations=READ_ONLY)
def list_notes(
    folder: Annotated[
        str | None,
        Field(description="Only notes whose folder path contains this text, e.g. 'MATH 300'"),
    ] = None,
    modified_after: Annotated[
        str | None, Field(description="ISO date or datetime, e.g. '2026-10-01'")
    ] = None,
    sort: Literal["recent", "title"] = "recent",
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Field(description="next_cursor of a previous call")] = None,
) -> NoteList:
    """List notes with folder, page count and dates, most recently modified first by default.

    Also returns every folder path. Use to browse a folder, see what changed recently, or find a
    note by title; to search inside notes use notability_search_notes.
    """
    con, offset = _db(), _offset(cursor)
    notes = store.all_notes(con)
    if folder:
        notes = [n for n in notes if n.folder and folder.casefold() in n.folder.casefold()]
    if modified_after:
        try:
            since = _utc(modified_after)
        except ValueError as e:
            raise ToolError("modified_after must be an ISO date such as 2026-10-01.") from e
        notes = [n for n in notes if _utc(n.modified_at) > since]
    if sort == "title":
        notes.sort(key=lambda n: n.title.casefold())
    else:
        notes.sort(key=lambda n: n.modified_at, reverse=True)
    page = notes[offset : offset + limit]
    return NoteList(
        notes=[NoteSummary.model_validate(n, from_attributes=True) for n in page],
        total=len(notes),
        next_cursor=str(offset + limit) if offset + limit < len(notes) else None,
        folders=sorted(set(store.folder_paths(con).values())),
        synced_at=store.get_meta(con, "synced_at"),
    )


@mcp.tool(
    name="notability_read_note",
    title="Read note",
    annotations=READ_ONLY,
    structured_output=False,
)
def read_note(
    note_id: str,
    start_page: Annotated[int, Field(ge=1)] = 1,
    end_page: Annotated[int | None, Field(ge=1, description="Inclusive; default: last")] = None,
) -> str:
    """A note's text page by page, as Markdown: OCR of the handwriting, the text of PDF pages
    the note is written on, and any typed text.

    OCR is approximate; pages where it looks unreliable are flagged, so view those with
    notability_get_page_image. Output stops near 25,000 characters and says how to continue.
    """
    con = _db()
    note = _note(con, note_id)
    if note.page_count is None:
        raise ToolError("This note's content isn't synced yet. Ask the user to run sync.")
    last = min(end_page or note.page_count, note.page_count)
    if start_page > last:
        raise ToolError(f"Note has {note.page_count} pages; ask for pages 1-{note.page_count}.")

    out = [f"# {note.title}", f"{note.folder or 'No folder'} · {note.page_count} pages"]
    texts = store.note_pages(con, note_id)
    if start_page == 1 and (typed := [t.text for t in texts if t.source == "typed"]):
        out += ["", "## Typed text", *typed]
    by_page = {t.page: t for t in texts if t.source == "handwriting"}
    pdf_by_page = {t.page: t.text for t in texts if t.source == "pdf"}
    size = sum(len(s) for s in out)
    for number in range(start_page, last + 1):
        page = by_page.get(number)
        pdf_text = pdf_by_page.get(number)
        body = page.text if page and page.text else ""
        if pdf_text:
            body = f"{body}\n\nPDF text:\n{pdf_text}" if body else f"PDF text:\n{pdf_text}"
        flag = ""
        if page and page.confidence is not None and page.confidence < LOW_CONFIDENCE:
            flag = " (low OCR confidence: view with notability_get_page_image)"
        section = f"\n## Page {number}{flag}\n{body or '(no handwriting recognized)'}"
        if size + len(section) > READ_NOTE_MAX_CHARS and number > start_page:
            out.append(
                f"\n[Truncated: showed pages {start_page}-{number - 1} of {note.page_count}. "
                f"Continue with notability_read_note(note_id={note_id!r}, start_page={number})]"
            )
            break
        out.append(section)
        size += len(section)
    return "\n".join(out)


@mcp.tool(name="notability_get_page_image", title="View page", annotations=READ_ONLY)
def get_page_image(
    note_id: str,
    page: Annotated[int, Field(ge=1)],
    max_edge: Annotated[int, Field(ge=256, le=2576, description="Long edge in pixels")] = MAX_EDGE,
) -> Image:
    """One note page as a PNG, to read handwriting, math and drawings exactly as written.

    Prefer this over OCR text whenever accuracy matters. Draws the ink over the page's PDF, if
    any; shapes, pasted images and typed text are not shown.
    """
    con = _db()
    note = _note(con, note_id)
    bundle = store.note_bundle(con, note_id)
    if not bundle:
        raise ToolError("This note's content isn't synced yet. Ask the user to run sync.")
    pages = _decode(con, note_id, bundle).pages
    if page > len(pages):
        raise ToolError(f"{note.title!r} has {len(pages)} pages; ask for 1-{len(pages)}.")
    target = pages[page - 1]
    background = store.asset(con, target.pdf.asset) if target.pdf else None
    return Image(data=render_page(target, max_edge, background), format="png")
