import os

from appindex_files import handwriting_fb, recording_fb, write
from conftest import LECTURE, NOT_DOWNLOADED, TRASHED, Container

from notability_mcp import local, store
from notability_mcp.bundle import decode_local
from notability_mcp.sync import sync_local


def test_library_comes_from_a_snapshot_and_the_container_is_untouched(container: Container):
    before = container.listing()

    with local.snapshot() as con:
        folders, notes = local.read_library(con)

    assert container.listing() == before
    assert [(f.id, f.name, f.parent_id) for f in folders] == [
        ("F1", "Fall 2026", None),
        ("S1", "MATH 300", "F1"),
    ]
    by_id = {n.id: n for n in notes}
    assert TRASHED not in by_id  # trashed notes are left out
    lecture = by_id[LECTURE]
    assert (lecture.title, lecture.folder_id) == ("Lecture 1", "S1")
    assert lecture.modified_at == "2026-09-01T10:00:00.000+00:00"
    assert by_id[NOT_DOWNLOADED].folder_id is None


def test_snapshot_sees_rows_the_app_has_only_in_its_wal(container: Container):
    app = container.edit("UPDATE note_metadata SET title = 'Renamed' WHERE title = 'Lecture 1'")
    assert (container.store / "local_persistence-wal").stat().st_size > 0

    with local.snapshot() as con:
        _, notes = local.read_library(con)
    app.close()

    assert {n.title for n in notes} >= {"Renamed"}


def test_sync_local_indexes_change_logs_and_only_changed_notes(container: Container, tmp_path):
    con = store.connect(tmp_path / "index.db")

    # the note with no change log has nothing to index yet, but stays listed
    assert sync_local(con) == (1, 0)
    assert sync_local(con) == (0, 0)
    container.edit(
        "UPDATE note_metadata SET modified = '2026-09-03 10:00:00.000' WHERE title = 'Lecture 1'"
    ).close()
    assert sync_local(con) == (1, 0)

    [hit], _ = store.search(con, "hello")
    assert (hit.note_id, hit.source) == (LECTURE, "typed")
    assert store.get_meta(con, "source") == "local"
    assert {n.note_id for n in store.all_notes(con)} == {LECTURE, NOT_DOWNLOADED}


def test_sync_local_uses_the_apps_text_and_follows_its_reindexing(container: Container, tmp_path):
    con = store.connect(tmp_path / "index.db")
    page = decode_local(LECTURE, local.change_log(LECTURE) or b"").pages[0]
    hw = write(
        container.library,
        "NoteHandwritingIndexer",
        LECTURE,
        "HandwritingIndex.fb",
        handwriting_fb([(page.id, "harmonic conjugate")]),
    )
    write(
        container.library,
        "NoteRecordingIndexer",
        LECTURE,
        "RecordingIndex.fb",
        recording_fb([("cd" * 64, "en-US", [(0.0, 3.0, "residue theorem")])]),
    )

    assert sync_local(con, ocr="app") == (1, 0)
    [hit], _ = store.search(con, "harmonic")
    assert (hit.note_id, hit.page, hit.source) == (LECTURE, 1, "handwriting")
    [rec] = store.note_transcripts(con, LECTURE)
    assert rec.segments == [(0.0, 3.0, "residue theorem")]

    # the app re-recognizes the page minutes after the edit; the note itself is unchanged
    hw.write_bytes(handwriting_fb([(page.id, "laurent series")]))
    os.utime(hw, (2_000_000_000, 2_000_000_000))
    assert sync_local(con, ocr="app") == (1, 0)
    assert store.search(con, "laurent")[0]
    assert sync_local(con, ocr="app") == (0, 0)
