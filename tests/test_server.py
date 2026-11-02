import base64
import io
import json
from pathlib import Path

import pytest
from conftest import LECTURE
from mcp import Client
from mcp.types import ImageContent
from PIL import Image as PILImage

from notability_mcp import server, store
from notability_mcp.server import mcp
from notability_mcp.store import Folder, NoteMeta, PageText
from notability_mcp.sync import sync_local

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLE = (FIXTURES / "synthetic_bundle.bin").read_bytes()
TOOLS = {
    "notability_search_notes",
    "notability_list_notes",
    "notability_read_note",
    "notability_get_page_image",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_connection():
    """The server keeps its index open for the life of the process; each test has its own."""
    yield
    if server._db.cache_info().currsize:
        server._db().close()
    server._db.cache_clear()


@pytest.fixture
def index(tmp_path, monkeypatch):
    path = tmp_path / "index.db"
    monkeypatch.setenv("NOTABILITY_MCP_DB", str(path))
    con = store.connect(path)
    store.save_library(
        con,
        [Folder("f", "MATH 300", "subject", None)],
        [
            NoteMeta("n1", "Complex Analysis", "f", None, "2026-10-01T00:00:00+00:00"),
            NoteMeta("n2", "Groceries", None, None, "2026-10-05T00:00:00+00:00"),
        ],
    )
    store.save_note(
        con,
        "n1",
        BUNDLE,
        2,
        [
            PageText(1, "handwriting", "Cauchy-Riemann equations", 1.0),
            PageText(2, "handwriting", "V homeria conjogate ot u in D", 0.4),
        ],
    )
    con.close()


def text(result) -> str:
    return result.content[0].text


@pytest.mark.anyio
async def test_every_tool_is_annotated_read_only(index):
    async with Client(mcp, raise_exceptions=True) as client:
        tools = (await client.list_tools()).tools

    assert {t.name for t in tools} == TOOLS
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


@pytest.mark.anyio
async def test_search_read_and_view_a_page(index):
    async with Client(mcp, raise_exceptions=True) as client:
        found = await client.call_tool("notability_search_notes", {"query": "conjugate"})
        note = await client.call_tool("notability_read_note", {"note_id": "n1"})
        image = await client.call_tool(
            "notability_get_page_image", {"note_id": "n1", "page": 1, "max_edge": 512}
        )

    hit = found.structured_content["hits"][0]
    assert (hit["note_id"], hit["page"], hit["folder"]) == ("n1", 2, "MATH 300")
    assert found.structured_content["notice"] is None
    assert "## Page 1\nCauchy-Riemann equations" in text(note)
    assert "## Page 2 (low OCR confidence" in text(note)
    [block] = image.content
    assert isinstance(block, ImageContent) and block.mime_type == "image/png"


@pytest.mark.anyio
async def test_loose_matches_come_with_a_warning(index):
    async with Client(mcp, raise_exceptions=True) as client:
        found = await client.call_tool("notability_search_notes", {"query": "riemann equality"})

    [hit] = found.structured_content["hits"]
    assert hit["page"] == 1 and hit["score"] < 0.8
    assert "No close matches" in found.structured_content["notice"]


@pytest.mark.anyio
async def test_list_notes_filters_by_folder_and_sorts_by_recency(index):
    async with Client(mcp, raise_exceptions=True) as client:
        everything = await client.call_tool("notability_list_notes", {})
        math = await client.call_tool("notability_list_notes", {"folder": "math"})

    listing = everything.structured_content
    assert [n["note_id"] for n in listing["notes"]] == ["n2", "n1"]
    assert listing["folders"] == ["MATH 300"]
    assert listing["synced_at"]
    assert [n["note_id"] for n in math.structured_content["notes"]] == ["n1"]


@pytest.mark.anyio
async def test_errors_say_what_to_do_next(index):
    async with Client(mcp, raise_exceptions=True) as client:
        unknown = await client.call_tool("notability_read_note", {"note_id": "nope"})
        past_end = await client.call_tool(
            "notability_read_note", {"note_id": "n1", "start_page": 9}
        )
        unsynced = await client.call_tool("notability_get_page_image", {"note_id": "n2", "page": 1})

    assert unknown.is_error and "notability_search_notes" in text(unknown)
    assert past_end.is_error and "pages 1-2" in text(past_end)
    assert unsynced.is_error and "sync" in text(unsynced)


@pytest.mark.anyio
async def test_missing_index_asks_the_user_to_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTABILITY_MCP_DB", str(tmp_path / "missing.db"))

    async with Client(mcp, raise_exceptions=True) as client:
        result = await client.call_tool("notability_list_notes", {})

    assert result.is_error and "notability-mcp sync" in text(result)


@pytest.mark.anyio
async def test_a_note_synced_from_the_mac_app_renders_its_ink(container, tmp_path, monkeypatch):
    # a captured AppendOpsEvent has the change log's layout (ops in root field 0): one pen stroke
    event = json.loads((FIXTURES / "25_ink_pen.json").read_text())["append_ops_event_hex"]
    (container.store / "notes" / LECTURE.upper()).write_bytes(bytes.fromhex(event))
    path = tmp_path / "local.db"
    monkeypatch.setenv("NOTABILITY_MCP_DB", str(path))
    con = store.connect(path)
    sync_local(con)
    con.close()

    async with Client(mcp, raise_exceptions=True) as client:
        image = await client.call_tool(
            "notability_get_page_image", {"note_id": LECTURE, "page": 1, "max_edge": 512}
        )

    [block] = image.content
    assert isinstance(block, ImageContent)
    png = PILImage.open(io.BytesIO(base64.b64decode(block.data))).convert("L")
    darkest, _ = png.getextrema()
    assert isinstance(darkest, int) and darkest < 128  # dark ink on the white page
