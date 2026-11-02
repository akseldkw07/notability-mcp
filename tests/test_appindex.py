import os

import pytest
from appindex_files import (
    handwriting_fb,
    handwriting_plist,
    image_fb,
    recording_fb,
    write,
)
from conftest import LECTURE, Container

from notability_mcp import appindex, local
from notability_mcp.bundle import Note, Page

AUDIO = "ab" * 64


def note_with_pages(*ids) -> Note:
    return Note(LECTURE, None, [Page(i + 1, seq, 612, 792) for i, seq in enumerate(ids)], "")


def test_handwriting_maps_to_pages_by_page_id(container: Container):
    # only pages with text are stored, so the third page's id says it's page 3
    note = note_with_pages((1, 10, 0), (1, 10, 1), (2, 30, 0))
    write(
        container.library,
        "NoteHandwritingIndexer",
        LECTURE,
        "HandwritingIndex.fb",
        handwriting_fb([((1, 10, 0), "first"), ((2, 30, 0), "third"), ((9, 9, 9), "gone")]),
    )

    assert appindex.handwriting(note) == {1: "first", 3: "third"}


def test_old_plist_handwriting_is_numbered_from_zero(container: Container):
    note = note_with_pages((1, 10, 0), (1, 10, 1))
    write(
        container.library,
        "NoteHandwritingIndexer",
        LECTURE,
        "index.plist",
        handwriting_plist({1: "second", 0: "  "}),
    )

    assert appindex.handwriting(note) == {2: "second"}


def test_the_newer_of_plist_and_fb_wins(container: Container):
    note = note_with_pages((1, 10, 0))
    old = write(
        container.library,
        "NoteHandwritingIndexer",
        LECTURE,
        "index.plist",
        handwriting_plist({0: "old"}),
    )
    write(
        container.library,
        "NoteHandwritingIndexer",
        LECTURE,
        "HandwritingIndex.fb",
        handwriting_fb([((1, 10, 0), "new")]),
    )
    os.utime(old, (1, 1))

    assert appindex.handwriting(note) == {1: "new"}


def test_no_index_means_none_not_empty(container: Container):
    assert appindex.handwriting(note_with_pages((1, 10, 0))) is None


def test_image_text_and_transcripts(container: Container):
    write(container.library, "NoteImageIndexer", LECTURE, "ImageIndex.fb", image_fb(["EXIT", " "]))
    write(
        container.library,
        "NoteRecordingIndexer",
        LECTURE,
        "RecordingIndex.fb",
        recording_fb([(AUDIO, "en-US", [(0.0, 2.5, "so today"), (2.5, 4.0, "we start")])]),
    )

    assert appindex.image_text(LECTURE) == ["EXIT"]
    [rec] = appindex.transcripts(LECTURE)
    assert (rec.asset, rec.language) == (AUDIO, "en-US")
    assert rec.segments == [(0.0, 2.5, "so today"), (2.5, 4.0, "we start")]


def test_an_unknown_format_version_is_refused(container: Container):
    note = note_with_pages((1, 10, 0))
    write(
        container.library,
        "NoteHandwritingIndexer",
        LECTURE,
        "HandwritingIndex.fb",
        handwriting_fb([((1, 10, 0), "x")], versions=(5, 6)),
    )

    with pytest.raises(appindex.FormatError, match=r"handwriting index format 5\.6"):
        appindex.handwriting(note)


def test_a_corrupt_file_is_a_format_error(container: Container):
    write(container.library, "NoteImageIndexer", LECTURE, "ImageIndex.fb", b"\x04\0\0\0junk")

    with pytest.raises(appindex.FormatError):
        appindex.image_text(LECTURE)


def test_signature_changes_when_the_app_reindexes(container: Container):
    before = appindex.signature(LECTURE)
    write(container.library, "NoteImageIndexer", LECTURE, "ImageIndex.fb", image_fb(["x"]))

    assert appindex.signature(LECTURE) != before
    assert local.library_dir() == container.library
