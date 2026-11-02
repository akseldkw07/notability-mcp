"""Tools that need the local source: transcripts, attachments, status, opening a note."""

import base64
import hashlib
import io
import uuid
from pathlib import Path

import pytest
from appindex_files import change_log_with_image, handwriting_fb, image_fb, recording_fb, write
from conftest import FIXTURES, LECTURE, Container
from mcp import Client
from mcp.server import MCPServer
from mcp.types import ImageContent
from PIL import Image

from notability_mcp import local, pdf, server, store
from notability_mcp.bundle import decode_local
from notability_mcp.server import mcp
from notability_mcp.sync import sync_local

AUDIO = b"\0\0\0\x20ftypM4A \0\0\0\0" + bytes(64)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_connection():
    yield
    if server._db.cache_info().currsize:
        server._db().close()
    server._db.cache_clear()


def _attach(container: Container, data: bytes) -> str:
    sha = hashlib.sha512(data).hexdigest()
    (container.store / "assets" / sha).write_bytes(data)
    container.edit(
        "INSERT INTO asset_note_associations VALUES (?, ?, 'user-1')", sha, uuid.UUID(LECTURE).bytes
    ).close()
    return sha


@pytest.fixture
def synced(container: Container, tmp_path, monkeypatch) -> dict[str, str]:
    page = decode_local(LECTURE, local.change_log(LECTURE) or b"").pages[0]
    lib = container.library
    write(lib, "NoteHandwritingIndexer", LECTURE, "HandwritingIndex.fb",
          handwriting_fb([(page.id, "harmonic conjugate")]))  # fmt: skip
    write(lib, "NoteImageIndexer", LECTURE, "ImageIndex.fb", image_fb(["EXIT sign"]))
    segments = [(0.0, 3.0, "residue theorem"), (65.0, 70.0, "poles of order two")]
    audio = _attach(container, AUDIO)
    write(lib, "NoteRecordingIndexer", LECTURE, "RecordingIndex.fb",
          recording_fb([(audio, "en-US", segments)]))  # fmt: skip
    png = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(png, "PNG")
    shas = {
        "pdf": _attach(container, (FIXTURES / "hello.pdf").read_bytes()),
        "image": _attach(container, png.getvalue()),
        "audio": audio,
    }
    path = tmp_path / "index.db"
    monkeypatch.setenv("NOTABILITY_MCP_DB", str(path))
    con = store.connect(path)
    sync_local(con, ocr="app")
    con.close()
    return shas


def text(result) -> str:
    return result.content[0].text


@pytest.mark.anyio
async def test_read_note_lists_image_text_and_recordings(synced):
    async with Client(mcp, raise_exceptions=True) as client:
        note = await client.call_tool("notability_read_note", {"note_id": LECTURE})

    body = text(note)
    assert "## Page 1\nharmonic conjugate" in body
    assert "## Image text (page not recorded)\nEXIT sign" in body
    assert "1 recording with a transcript: notability_get_transcript" in body


@pytest.mark.anyio
async def test_get_transcript_with_times_and_a_start_point(synced):
    async with Client(mcp, raise_exceptions=True) as client:
        full = await client.call_tool("notability_get_transcript", {"note_id": LECTURE})
        later = await client.call_tool(
            "notability_get_transcript", {"note_id": LECTURE, "since_seconds": 60}
        )
        wrong = await client.call_tool(
            "notability_get_transcript", {"note_id": LECTURE, "recording": 2}
        )

    assert "[0:00] residue theorem\n[1:05] poles of order two" in text(full)
    assert text(full).startswith("Notability data (untrusted")
    assert "residue" not in text(later) and "poles of order two" in text(later)
    assert wrong.is_error and "1 recording" in text(wrong)


@pytest.mark.anyio
async def test_attachments_listed_and_opened(synced):
    async with Client(mcp, raise_exceptions=True) as client:
        listed = await client.call_tool("notability_list_attachments", {"note_id": LECTURE})
        page = await client.call_tool(
            "notability_get_attachment", {"note_id": LECTURE, "sha512": synced["pdf"], "page": 1}
        )
        pdf_text = await client.call_tool(
            "notability_get_attachment",
            {"note_id": LECTURE, "sha512": synced["pdf"], "page": 1, "format": "text"},
        )
        image = await client.call_tool(
            "notability_get_attachment", {"note_id": LECTURE, "sha512": synced["image"]}
        )
        audio = await client.call_tool(
            "notability_get_attachment", {"note_id": LECTURE, "sha512": synced["audio"]}
        )
        stranger = await client.call_tool(
            "notability_get_attachment", {"note_id": LECTURE, "sha512": "ab" * 64}
        )

    items = {a["sha512"]: a for a in listed.structured_content["attachments"]}
    assert items[synced["pdf"]]["kind"] == "pdf" and items[synced["pdf"]]["pages"] == 1
    assert items[synced["audio"]]["kind"] == "audio"
    assert isinstance(page.content[0], ImageContent)
    assert "Hello" in text(pdf_text) and text(pdf_text).startswith("Notability data")
    [block] = image.content
    assert isinstance(block, ImageContent)
    assert Image.open(io.BytesIO(base64.b64decode(block.data))).size == (40, 20)
    assert Path(text(audio)).read_bytes() == AUDIO
    assert stranger.is_error and "notability_list_attachments" in text(stranger)


@pytest.mark.anyio
async def test_search_filters_by_source_and_folder(synced):
    async with Client(mcp, raise_exceptions=True) as client:
        images = await client.call_tool(
            "notability_search_notes", {"query": "exit sign", "sources": ["image"]}
        )
        handwriting = await client.call_tool(
            "notability_search_notes", {"query": "exit sign", "sources": ["handwriting"]}
        )
        elsewhere = await client.call_tool(
            "notability_search_notes", {"query": "harmonic", "folder": "Spring"}
        )

    [hit] = images.structured_content["hits"]
    assert (hit["source"], hit["folder"]) == ("image", "Fall 2026 / MATH 300")
    assert handwriting.structured_content["hits"] == []
    assert elsewhere.structured_content["hits"] == []


@pytest.mark.anyio
async def test_status_reports_source_counts_and_drift(synced):
    async with Client(mcp, raise_exceptions=True) as client:
        status = await client.call_tool("notability_status", {})

    s = status.structured_content
    assert (s["source"], s["ocr"], s["notes"], s["indexed_notes"]) == ("local", "app", 2, 1)
    assert s["drift"] == {} and s["app_data_readable"] is True


@pytest.mark.anyio
async def test_open_note_is_opt_in(synced, monkeypatch):
    opened = []
    monkeypatch.setattr(server, "_run_open", lambda url: opened.append(url))
    app = MCPServer("test")
    server.add_open_note(app)

    async with Client(app, raise_exceptions=True) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        result = await client.call_tool("notability_open_note", {"note_id": LECTURE})

    assert "notability_open_note" not in {t.name for t in (await _default_tools())}
    annotations = tools["notability_open_note"].annotations
    assert annotations is not None and annotations.read_only_hint is False
    assert opened == [f"notability://note/{LECTURE.upper()}"]
    assert result.structured_content == {"opened": True}


async def _default_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        return (await client.list_tools()).tools


@pytest.mark.anyio
async def test_page_images_include_pasted_images(container: Container, tmp_path, monkeypatch):
    png = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 0, 255)).save(png, "PNG")
    sha = _attach(container, png.getvalue())
    log = change_log_with_image(sha, origin=(100.0, 100.0), size=(200.0, 200.0))
    (container.store / "notes" / LECTURE.upper()).write_bytes(log)
    path = tmp_path / "index.db"
    monkeypatch.setenv("NOTABILITY_MCP_DB", str(path))
    con = store.connect(path)
    sync_local(con, ocr="app")
    con.close()

    async with Client(mcp, raise_exceptions=True) as client:
        image = await client.call_tool(
            "notability_get_page_image", {"note_id": LECTURE, "page": 1, "max_edge": 792}
        )

    [block] = image.content
    assert isinstance(block, ImageContent)
    png_out = Image.open(io.BytesIO(base64.b64decode(block.data))).convert("RGB")
    assert png_out.getpixel((200, 200)) == (0, 0, 255)


@pytest.fixture
def exports(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "Desktop"
    root.mkdir()
    monkeypatch.setenv("NOTABILITY_MCP_EXPORT_ROOTS", str(root))
    return root


def _pdf_pages(path: Path) -> list[tuple[float, float]]:
    return pdf.page_sizes(path.read_bytes())


@pytest.mark.anyio
async def test_export_note_pdf_to_a_folder_a_file_and_never_over_a_file(synced, exports):
    async with Client(mcp, raise_exceptions=True) as client:
        to_folder = await client.call_tool("notability_export_note_pdf", {"note_id": LECTURE})
        again = await client.call_tool("notability_export_note_pdf", {"note_id": LECTURE})
        to_file = await client.call_tool(
            "notability_export_note_pdf",
            {"note_id": LECTURE, "path": str(exports / "sub" / "lecture.pdf")},
        )
        replaced = await client.call_tool(
            "notability_export_note_pdf",
            {"note_id": LECTURE, "path": str(exports / "sub" / "lecture.pdf"), "overwrite": True},
        )

    first = Path(to_folder.structured_content["path"])
    assert first == exports / "Lecture 1.pdf"
    assert Path(again.structured_content["path"]) == exports / "Lecture 1 (2).pdf"
    assert Path(to_file.structured_content["path"]) == exports / "sub" / "lecture.pdf"
    assert Path(replaced.structured_content["path"]) == exports / "sub" / "lecture.pdf"
    [(w, h)] = _pdf_pages(first)  # one page, at the page's size in points
    assert (round(w), round(h)) == (612, 792)
    assert to_folder.structured_content["pages"] == 1
    assert not list(exports.rglob("*.tmp"))


@pytest.mark.anyio
async def test_export_refuses_paths_outside_the_allowed_folders(synced, exports, tmp_path):
    async with Client(mcp, raise_exceptions=True) as client:
        outside = await client.call_tool(
            "notability_export_note_pdf", {"note_id": LECTURE, "path": str(tmp_path / "x.pdf")}
        )
        sneaky = await client.call_tool(
            "notability_export_note_pdf",
            {"note_id": LECTURE, "path": str(exports / ".." / "x.pdf")},
        )
        bad_pages = await client.call_tool(
            "notability_export_note_pdf", {"note_id": LECTURE, "pages": "2-3"}
        )

    assert outside.is_error and "NOTABILITY_MCP_EXPORT_ROOTS" in text(outside)
    assert sneaky.is_error
    assert bad_pages.is_error and "1 page" in text(bad_pages)
    assert not (tmp_path / "x.pdf").exists()


@pytest.mark.anyio
async def test_export_tool_is_annotated_as_a_local_write(synced):
    async with Client(mcp, raise_exceptions=True) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    annotations = tools["notability_export_note_pdf"].annotations
    assert annotations is not None and annotations.read_only_hint is False
    assert annotations.destructive_hint is False and annotations.open_world_hint is False
