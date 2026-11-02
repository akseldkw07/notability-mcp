from pathlib import Path

import pytest

from notability_mcp import ocr
from notability_mcp.bundle import HIGHLIGHTER, Ink, Note, Page, decode_bundle
from notability_mcp.index import AppText, page_texts
from notability_mcp.ocr import Line
from notability_mcp.store import PageText, Transcript

BUNDLE = (Path(__file__).parent / "fixtures" / "synthetic_bundle.bin").read_bytes()


def test_typed_text_is_note_level_and_every_page_gets_a_row():
    texts = page_texts(decode_bundle(BUNDLE))

    assert texts == [
        PageText(0, "typed", "Hello world!\n\nbox"),
        PageText(1, "handwriting", "", None),  # no ink, so nothing to OCR
    ]


def _ink(tool: int = 0) -> Ink:
    return Ink(None, (0, 0), (1, 1), 0, (0, 0, 0, 255), 1, tool, [])


def _pages(*inks: list[Ink]) -> Note:
    pages = [Page(i + 1, (1, 1, i), 612, 792, inks=list(v)) for i, v in enumerate(inks)]
    return Note("n", None, pages, "")


def test_the_apps_recognized_text_replaces_ocr_and_vision_fills_only_real_gaps(monkeypatch):
    ocr_calls = []
    monkeypatch.setattr(
        ocr, "recognize", lambda png: ocr_calls.append(1) or [Line("drawn", 0.9, (0, 0, 1, 1))]
    )
    note = _pages([_ink()], [_ink()], [_ink(HIGHLIGHTER)], [])
    app = AppText(
        handwriting={1: "from the app"},
        images=[(0, "EXIT sign")],
        transcripts=[Transcript("ab" * 64, "en-US", [(0.0, 1.0, "so"), (1.0, 2.0, "today")])],
    )

    texts = page_texts(note, app=app)

    assert texts == [
        PageText(0, "image", "EXIT sign"),
        PageText(0, "recording", "so today"),
        PageText(1, "handwriting", "from the app", None),
        PageText(2, "handwriting", "drawn", 0.9),  # ink the app found no text in
        PageText(3, "handwriting", "", None),  # highlighter only: not handwriting
        PageText(4, "handwriting", "", None),
    ]
    assert len(ocr_calls) == 1


def test_vision_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(ocr, "recognize", lambda png: pytest.fail("OCR ran"))

    texts = page_texts(_pages([_ink()]), app=AppText(handwriting={}), vision=False)

    assert texts == [PageText(1, "handwriting", "", None)]
