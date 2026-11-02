"""Turn a note bundle into searchable page text."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import fmean

from notability_mcp import ocr, pdf, store
from notability_mcp.bundle import HIGHLIGHTER, Note, Page, decode_bundle
from notability_mcp.render import render_page
from notability_mcp.store import PageText, Transcript

OCR_MAX_EDGE = 2048  # a bit above page-image size: small handwriting reads better


@dataclass
class AppText:
    """Text the Notability Mac app has already recognized for a note (see appindex.py)."""

    handwriting: dict[int, str] | None = None  # by page number; None: the app hasn't indexed it
    images: list[str] = field(default_factory=list)
    transcripts: list[Transcript] = field(default_factory=list)


def _ocr(page: Page) -> PageText:
    # the ink alone, without the PDF, so OCR reads only the handwriting
    lines = ocr.recognize(render_page(page, OCR_MAX_EDGE))
    confidence = fmean(line.confidence for line in lines) if lines else None
    return PageText(page.number, "handwriting", "\n".join(line.text for line in lines), confidence)


def page_texts(
    note: Note,
    pdfs: dict[str, bytes] | None = None,
    app: AppText | None = None,
    vision: bool = True,
) -> list[PageText]:
    """Note-level text (page 0: typed, image OCR, transcripts), one handwriting row per page
    (empty when there is no ink), and the text of each page's PDF, given the note's PDFs by
    SHA-512.

    Handwriting the app recognized is used as it is. Other inked pages are OCRed with Vision
    unless `vision` is off; with the app's text, pages with only highlighter are skipped."""
    texts = [PageText(0, "typed", note.text)] if note.text else []
    if app:
        texts += [PageText(0, "image", t) for t in app.images]
        texts += [
            PageText(0, "recording", " ".join(s[2] for s in t.segments if s[2]))
            for t in app.transcripts
            if t.segments
        ]
    known = app.handwriting if app else None
    pdf_pages = {sha: pdf.page_texts(data) for sha, data in (pdfs or {}).items()}
    for page in note.pages:
        if known is not None and page.number in known:
            texts.append(PageText(page.number, "handwriting", known[page.number], None))
        elif (
            vision
            and page.inks
            and (known is None or any(ink.tool != HIGHLIGHTER for ink in page.inks))
        ):
            texts.append(_ocr(page))
        else:
            texts.append(PageText(page.number, "handwriting", "", None))
        doc = pdf_pages.get(page.pdf.asset, []) if page.pdf else []
        if page.pdf and page.pdf.index < len(doc) and doc[page.pdf.index].strip():
            texts.append(PageText(page.number, "pdf", doc[page.pdf.index]))
    return texts


def index_note(
    con: sqlite3.Connection,
    note_id: str,
    bundle: bytes,
    fetch_asset: Callable[[str], bytes | None],
    decode: Callable[[str, bytes], Note] = lambda _, b: decode_bundle(b),
    app_text: Callable[[Note], AppText] | None = None,
    vision: bool = True,
) -> int:
    """Decode, OCR and store one note, fetching PDFs not stored yet; returns its page count.

    A PDF the source doesn't have (the Mac app may not have downloaded it) is left out: its pages
    are indexed without PDF text and drawn without a background."""
    note = decode(note_id, bundle) if bundle else Note(note_id, None, [], "")
    pdfs = {}
    for sha in {page.pdf.asset for page in note.pages if page.pdf}:
        if (data := store.asset(con, sha)) is None:
            if (data := fetch_asset(sha)) is None:
                continue
            store.save_asset(con, sha, data)
        pdfs[sha] = data
    app = app_text(note) if app_text else None
    texts = page_texts(note, pdfs, app, vision)
    store.save_note(con, note_id, bundle, len(note.pages), texts, app.transcripts if app else None)
    return len(note.pages)
