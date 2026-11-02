"""Turn a note bundle into searchable page text."""

import sqlite3
from collections.abc import Callable
from statistics import fmean

from notability_mcp import ocr, pdf, store
from notability_mcp.bundle import Note, decode_bundle
from notability_mcp.render import render_page
from notability_mcp.store import PageText

OCR_MAX_EDGE = 2048  # a bit above page-image size: small handwriting reads better


def page_texts(note: Note, pdfs: dict[str, bytes] | None = None) -> list[PageText]:
    """Typed text (page 0), one handwriting row per page (empty when there is no ink), and the
    text of each page's PDF, given the note's PDFs by SHA-512."""
    texts = [PageText(0, "typed", note.text)] if note.text else []
    pdf_pages = {sha: pdf.page_texts(data) for sha, data in (pdfs or {}).items()}
    for page in note.pages:
        # the ink alone, without the PDF, so OCR reads only the handwriting
        lines = ocr.recognize(render_page(page, OCR_MAX_EDGE)) if page.inks else []
        confidence = fmean(line.confidence for line in lines) if lines else None
        texts.append(
            PageText(page.number, "handwriting", "\n".join(line.text for line in lines), confidence)
        )
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
    store.save_note(con, note_id, bundle, len(note.pages), page_texts(note, pdfs))
    return len(note.pages)
