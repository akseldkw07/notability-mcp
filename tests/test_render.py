import io
from typing import cast

from PIL import Image, ImageOps

from notability_mcp import pdf
from notability_mcp.bundle import HIGHLIGHTER, IMAGE_BLOCK, Block, Note, Page, PdfPage
from notability_mcp.render import render_page


def test_ink_is_drawn_where_it_sits_on_the_page(ink_note: Note):
    png = render_page(ink_note.pages[0], max_edge=792)  # 1 px per point on a 612x792 page

    img = Image.open(io.BytesIO(png))
    left, top, right, _ = ImageOps.invert(img.convert("L")).getbbox() or (0, 0, 0, 0)
    assert img.size == (612, 792)
    assert 70 <= left <= 85 and 290 <= top <= 300  # stroke origin is (80, 300)
    assert 165 <= right <= 180


def test_highlighter_tints_white_paper_and_leaves_dark_pdf_text_dark(ink_note: Note, monkeypatch):
    page = ink_note.pages[0]
    page.inks[0].tool, page.inks[0].color = HIGHLIGHTER, (255, 255, 0, 102)  # 40% yellow
    on_paper = Image.open(io.BytesIO(render_page(page, max_edge=792)))

    page.pdf = PdfPage("ab" * 64, 0)
    monkeypatch.setattr(pdf, "render", lambda data, index, size: Image.new("RGB", size, "black"))
    on_text = Image.open(io.BytesIO(render_page(page, max_edge=792, background=b"pdf")))

    paper = on_paper.convert("RGB").tobytes()
    assert min(paper[0::3]) == min(paper[1::3]) == 255  # yellow: only blue is taken away
    assert min(paper[2::3]) < 200
    assert max(on_text.convert("RGB").tobytes()) == 0  # nothing painted over the text


def test_long_edge_is_capped(ink_note: Note):
    img = Image.open(io.BytesIO(render_page(ink_note.pages[0], max_edge=400)))
    assert max(img.size) == 400


def test_pasted_images_are_cropped_scaled_and_placed():
    # a 20x10 image, left half red and right half blue; the block shows only the blue half,
    # stretched to 100x50 points at (200, 300)
    pic = Image.new("RGB", (20, 10), "red")
    pic.paste((0, 0, 255), (10, 0, 20, 10))
    data = io.BytesIO()
    pic.save(data, "PNG")
    block = Block(None, IMAGE_BLOCK, 0, (200.0, 300.0), (100.0, 50.0), (1.0, 1.0), 0.0, "ab" * 64,
                  (10.0, 0.0, 10.0, 10.0))  # fmt: skip
    page = Page(1, (1, 1, 0), 612, 792, blocks=[block])

    img = Image.open(io.BytesIO(render_page(page, 792, image={"ab" * 64: data.getvalue()}.get)))

    assert img.convert("RGB").getpixel((250, 325)) == (0, 0, 255)
    assert img.convert("RGB").getpixel((195, 325)) == (255, 255, 255)
    without = Image.open(io.BytesIO(render_page(page, 792)))
    assert without.convert("RGB").getpixel((250, 325)) == (255, 255, 255)


def test_shapes_are_drawn_and_highlighter_shapes_tint_the_paper():
    from appindex_files import change_log_with_shapes

    from notability_mcp.bundle import decode_local

    [page] = decode_local("n", change_log_with_shapes()).pages
    img = Image.open(io.BytesIO(render_page(page, max_edge=792))).convert("RGB")

    def px(xy: tuple[int, int]) -> tuple[int, ...]:
        return cast(tuple[int, ...], img.getpixel(xy))

    assert max(px((200, 100))) < 80  # the line, (100, 100) to (300, 100)
    r, g, b = px((200, 325))  # the ellipse's left edge, at (200, 325) after its move
    assert r > 200 and g < 80 and b < 80
    assert px((250, 325)) == (255, 255, 255)  # not filled
    r, g, b = px((200, 525))  # inside the highlighter rectangle
    assert r == g == 255 and b < 200
    assert px((200, 700)) == (255, 255, 255)  # the deleted line


def test_paper_is_cream_with_a_centred_grid_and_legacy_13_is_plain_white():
    from notability_mcp.bundle import Paper

    # like a real note: 792 x 612, a 36 pt grid, no colour set
    page = Page(1, (1, 1, 0), 792, 612, paper=Paper(flair=2, spacing=36.0, legacy=1))
    img = Image.open(io.BytesIO(render_page(page, max_edge=792))).convert("RGB")

    def px(xy: tuple[int, int]) -> tuple[int, ...]:
        return cast(tuple[int, ...], img.getpixel(xy))

    assert px((30, 50)) == (251, 248, 242)  # paper, inside a cell
    assert max(px((18, 50))) < 230 and max(px((30, 36))) < 230  # a vertical and a horizontal line
    assert px((36, 50)) == (251, 248, 242)  # no line at x = 36: the page centre is mid-cell

    page.paper = Paper(flair=2, spacing=36.0, legacy=13)
    white = Image.open(io.BytesIO(render_page(page, max_edge=792))).convert("RGB")
    assert white.getpixel((30, 50)) == (255, 255, 255)
