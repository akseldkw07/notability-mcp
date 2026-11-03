from pathlib import Path

from notability_mcp.bundle import (
    HIGHLIGHTER,
    Note,
    PdfPage,
    _page,
    decode_bundle,
    decode_local,
    linearize,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_typed_text_from_bundle():
    note = decode_bundle((FIXTURES / "synthetic_bundle.bin").read_bytes())

    # body text (with an edit and a removed char), then a separate text box
    assert note.text == "Hello world!\n\nbox"


def test_streamed_ink_lands_on_the_implicit_first_page(ink_note: Note):
    [page] = ink_note.pages
    [ink] = page.inks
    assert (page.number, page.width, page.height) == (1, 612.0, 792.0)
    assert ink.origin == (80.0, 300.0)
    assert ink.width == 2.0
    assert ink.paths[1].hex() == "0001000060540045005500c5a0550000333d003cff00"


def test_rga_orders_newest_sibling_first():
    a, b, c = (1, 1, 0), (1, 1, 1), (2, 5, 0)
    # "ab" typed, then "c" inserted after "a" by a later op: a c b
    assert linearize([(a, None), (b, a), (c, a)]) == [a, c, b]


def test_rga_survives_an_anchor_cycle():
    a, b = (1, 1, 0), (1, 2, 0)
    assert linearize([(a, None), (b, a), (a, b)]) == [a, b]


def test_mac_app_change_log_decodes_like_the_bundle(local_log):
    bundle = (FIXTURES / "synthetic_bundle.bin").read_bytes()
    web = decode_bundle(bundle)

    note = decode_local("6a1d9e4c-0b7f-4c1e-9a52-3f0d2b8e7c11", local_log(bundle))

    assert note.id == "6a1d9e4c-0b7f-4c1e-9a52-3f0d2b8e7c11"
    assert (note.text, len(note.pages)) == (web.text, len(web.pages))


def test_a_pdf_page_takes_the_backgrounds_width_and_its_own_shape():
    # ink is placed in the background's space: the PDF page scaled to the background's width
    bg = {"size": (610.0, 458.0), "pdf": ("ab" * 64, 0, [(363.0, 272.0), (363.0, 500.0)])}

    first, second = _page(1, (1, 1, 0), bg), _page(2, (1, 1, 1), bg)

    assert (first.width, round(first.height, 1)) == (610.0, 457.1)
    assert (second.width, round(second.height, 1)) == (610.0, 840.2)
    assert second.pdf == PdfPage("ab" * 64, 1)


def test_without_a_background_size_the_crop_box_is_the_page():
    page = _page(1, (1, 1, 0), {"pdf": ("ab" * 64, 0, [(363.0, 272.0)])})

    assert (page.width, page.height) == (363.0, 272.0)


def test_shapes_land_on_their_page_with_moves_and_deletes_applied():
    from appindex_files import change_log_with_shapes

    from notability_mcp.bundle import ELLIPSE, LINE, POLYGON

    [page] = decode_local("n", change_log_with_shapes()).pages

    line, ellipse, rect = page.shapes
    assert (line.kind, line.origin, line.points, line.width) == (
        LINE, (100.0, 100.0), [(0.0, 0.0), (200.0, 0.0)], 2.0)  # fmt: skip
    assert (ellipse.kind, ellipse.origin, ellipse.size) == (ELLIPSE, (200.0, 300.0), (100.0, 50.0))
    assert ellipse.color == (255, 0, 0, 255)
    assert (rect.kind, rect.tool, rect.fill) == (POLYGON, HIGHLIGHTER, (255, 255, 0, 102))
    assert len(rect.points) == 4


def test_the_apps_trailing_empty_pages_are_not_content():
    from notability_mcp.bundle import Page, Shape, last_page_with_content

    def page(n: int, **kw) -> Page:
        return Page(n, (1, 1, n), 612, 792, **kw)

    shape = Shape(None, 1, (0, 0), (1, 1), 0, [], (0, 0), (0, 0, 0, 255), 1, None, 0, 1)
    pdf = PdfPage("ab" * 64, 0)
    assert last_page_with_content([page(1, shapes=[shape]), page(2), page(3)]) == 1
    assert last_page_with_content([page(1), page(2, pdf=pdf), page(3)]) == 2
    assert last_page_with_content([page(1), page(2)]) == 1  # an empty note still has a page
