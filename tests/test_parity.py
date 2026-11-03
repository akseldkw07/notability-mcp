"""Parity between our export and the app's own "Save as… > PDF".

The synthetic tests check the comparison itself. test_matches_the_apps_export checks our
export of each real note against the app's PDF of it, saved by scripts/save_as.py into the
cache directory (never the repo: they're the user's notes). It only runs locally, with
NOTABILITY_MCP_PARITY=1:

    uv run scripts/save_as.py --sample 12      # drives the app: brings it to the front
    NOTABILITY_MCP_PARITY=1 uv run pytest tests/test_parity.py -v
"""

import io
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from notability_mcp import parity, pdf


def _pdf(tmp_path: Path, name: str, pages: list[Image.Image]) -> bytes:
    def jpegs():
        for img in pages:
            out = io.BytesIO()
            img.save(out, "JPEG", quality=95)
            yield out.getvalue(), 612.0, 792.0

    path = tmp_path / name
    pdf.write_images(jpegs(), path)
    return path.read_bytes()


def _page(paper=(251, 248, 242), circle=True) -> Image.Image:
    img = Image.new("RGB", (612, 792), paper)
    draw = ImageDraw.Draw(img)
    draw.line([(50, 100), (550, 100)], fill="black", width=2)
    draw.text((50, 150), "Some typed text", fill="black")
    if circle:
        draw.ellipse((300, 300, 450, 450), outline="black", width=2)
    return img


def test_identical_exports_match(tmp_path):
    report = parity.compare(_pdf(tmp_path, "a.pdf", [_page()]), _pdf(tmp_path, "b.pdf", [_page()]))
    assert report.ok, report.summary()


def test_a_missing_drawing_fails(tmp_path):
    ours = _pdf(tmp_path, "a.pdf", [_page(circle=False)])
    app = _pdf(tmp_path, "b.pdf", [_page()])

    report = parity.compare(ours, app, tmp_path / "diff")

    assert not report.ok
    assert report.diffs[0].diff > parity.TOLERANCE
    assert (tmp_path / "diff" / "page-001.png").exists()


def test_the_wrong_paper_colour_fails(tmp_path):
    ours = _pdf(tmp_path, "a.pdf", [_page(paper=(255, 255, 255))])
    app = _pdf(tmp_path, "b.pdf", [_page()])

    [page] = parity.compare(ours, app).diffs

    assert page.diff <= parity.TOLERANCE and page.tone > parity.TONE_TOLERANCE
    assert not page.ok


def test_a_different_page_count_fails(tmp_path):
    ours = _pdf(tmp_path, "a.pdf", [_page(), _page()])
    report = parity.compare(ours, _pdf(tmp_path, "b.pdf", [_page()]))
    assert not report.ok and "pages 2 (ours) vs 1 (app)" in report.summary()


def _references() -> list[tuple[str, Path]]:
    if os.environ.get("NOTABILITY_MCP_PARITY") != "1":
        return []
    return parity.references()


@pytest.mark.skipif(
    os.environ.get("NOTABILITY_MCP_PARITY") != "1",
    reason="needs the app's own exports of real notes (scripts/save_as.py); set "
    "NOTABILITY_MCP_PARITY=1",
)
@pytest.mark.parametrize(("note_id", "app_pdf"), _references() or [("none", Path())])
def test_matches_the_apps_export(note_id: str, app_pdf: Path, tmp_path):
    if note_id == "none":
        pytest.skip(f"no references in {parity.parity_dir()}: run scripts/save_as.py")
    ours = tmp_path / "ours.pdf"
    parity.our_export(note_id, ours)

    diff_dir = parity.parity_dir() / "diffs" / note_id
    report = parity.compare(ours.read_bytes(), app_pdf.read_bytes(), diff_dir)

    assert report.ok, f"{report.summary()} (page images: {diff_dir})"
