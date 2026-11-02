from conftest import LECTURE, NOT_DOWNLOADED, TRASHED, Container

from notability_mcp import local, store
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
