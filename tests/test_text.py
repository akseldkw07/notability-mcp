import io

import pytest
from PIL import Image, ImageOps

from notability_mcp import text
from notability_mcp.bundle import TEXT_BLOCK, Block, Note, Page, Paper, Run, Style
from notability_mcp.render import render_page
from notability_mcp.text import body_spans, line_height, page_spans, rulings, text_width, wrap

FONT = ("Helvetica Neue", 14.0)


def note(body: str, pages: list[Page] | None = None, **kw) -> Note:
    pages = pages or [Page(1, (1, 1, 0), 612, 792)]
    return Note("n", "t", pages, body, body=[Run(body, Style())], font=FONT, **kw)


def test_words_wrap_at_the_width_and_newlines_start_lines():
    [line] = wrap([("hello", Style())], FONT, 1000)
    assert [t for t, _, _ in line] == ["hello"]

    width = text_width("aaa bbb", text.Font(*FONT))
    lines = wrap([("aaa bbb ccc", Style())], FONT, width + 1)
    assert ["".join(t for t, _, _ in line) for line in lines] == ["aaa bbb ", "ccc"]

    assert len(text.paragraphs([Run("a\n\nb", Style())])) == 3


def test_a_word_longer_than_the_line_is_broken():
    lines = wrap([("x" * 200, Style())], FONT, 100)
    assert len(lines) > 1
    assert all(sum(text_width(t, f) for t, _, f in line) <= 100 for line in lines)


def test_body_text_starts_at_the_margins_and_flows_to_the_next_page():
    pages = [Page(1, (1, 1, 0), 612, 792), Page(2, (1, 1, 1), 612, 792)]
    height = line_height(text.Font(*FONT))
    per_page = int((792 - 72) / height)
    n = note("\n" * per_page + "next page", pages)

    spans = body_spans(n)

    assert list(spans) == [2]
    first = spans[2][0]  # one span per word
    assert first.x == 36 and 36 < first.baseline < 36 + height


def test_on_lined_paper_aligned_text_sits_on_the_lines():
    paper = Paper(flair=0, spacing=40.0, bleeds=False)
    page = Page(1, (1, 1, 0), 612, 792, paper=paper, margins=(0, 0, 20, 20))
    n = note("a\nb", [page], align_to_lines=True)

    a, b = body_spans(n)[1]

    lines = [y for y in rulings(792, 40) if y >= 40]  # a whole line's room above it
    assert (a.x, b.x) == (20, 20)
    assert a.baseline == pytest.approx(lines[0] - text.SNAP_RAISE * 14)
    assert b.baseline - a.baseline == pytest.approx(40)


def test_indent_and_alignment():
    page = Page(1, (1, 1, 0), 612, 792)
    n = Note("n", "t", [page], "", body=[Run("in", Style(indent=2)), Run("\nmid", Style(align=2))],
             font=FONT)  # fmt: skip

    indented, centred = body_spans(n)[1]

    assert indented.x == 36 + 2 * text.INDENT_STEP
    w = text_width("mid", text.Font(*FONT))
    assert centred.x == pytest.approx(36 + (612 - 72 - w) / 2)


def test_text_boxes_are_placed_on_their_page_and_drawn():
    box = Block(None, TEXT_BLOCK, 0, (100.0, 400.0), (300.0, 50.0), (1.0, 1.0), 0.0, None, None,
                [Run("Boxed text", Style(color=(255, 0, 0, 255)))])  # fmt: skip
    page = Page(1, (1, 1, 0), 612, 792, blocks=[box])
    n = Note("n", "t", [page], "", font=FONT)

    spans = page_spans(n, page)
    span = spans[0]
    assert span.x == 100 and 400 < span.baseline < 400 + line_height(text.Font(*FONT))

    img = Image.open(io.BytesIO(render_page(page, 792, text=spans))).convert("RGB")
    left, top, right, bottom = ImageOps.invert(img.convert("L")).getbbox() or (0, 0, 0, 0)
    # text box line metrics aren't measured on an app export yet: only check it lands in the box
    assert 98 <= left <= 104 and top >= 395 and bottom <= 420 and right > 150
    r, g, b = img.getpixel(((left + right) // 2, (top + bottom) // 2 + 2))  # type: ignore[misc]
    assert r > g and r > b  # red text


def test_on_lined_paper_text_needs_room_above_the_page_bottom():
    paper = Paper(flair=0, spacing=40.0, bleeds=False)
    pages = [Page(i, (1, 1, i), 612, 850, paper=paper, margins=(0, 0, 20, 20)) for i in (1, 2)]
    slots = [y for y in rulings(850, 40) if 40 <= y <= 850]
    assert 850 - slots[-1] < 14  # the last line is too close to the bottom for 14 pt text

    blank_last = note("\n" * (len(slots) - 1) + "\nnext", pages, align_to_lines=True)
    text_last = note("\n" * (len(slots) - 1) + "last", pages, align_to_lines=True)

    assert list(body_spans(blank_last)) == [2]  # a blank line took the last one
    assert list(body_spans(text_last)) == [2]  # text skips it for the next page
    assert body_spans(text_last)[2][0].baseline == pytest.approx(slots[0] - text.SNAP_RAISE * 14)


def test_tabs_go_to_the_next_stop_from_the_line_start():
    n = note("\t1\tfirst")

    one, first = body_spans(n)[1]

    assert one.x == 36 + text.TAB_STOP
    assert first.x == 36 + 2 * text.TAB_STOP


def test_body_text_wraps_around_a_pasted_image():
    from notability_mcp.bundle import IMAGE_BLOCK

    image = Block((1, 1, 0), IMAGE_BLOCK, 0, (36.0, 30.0), (200.0, 100.0), (1.0, 1.0), 0.0,
                  "ab" * 64, None)  # fmt: skip
    page = Page(1, (1, 1, 0), 612, 792, blocks=[image])
    n = note("word " * 60, [page])

    spans = body_spans(n)[1]

    beside = [s for s in spans if s.baseline < 130]  # lines level with the image
    below = [s for s in spans if s.baseline > 150]
    assert beside and min(s.x for s in beside) >= 236  # to the right of the image
    assert below and min(s.x for s in below) == 36  # back at the margin under it


def test_a_glyph_the_font_lacks_comes_from_a_fallback_font():
    helvetica = text.Font("Helvetica Neue", 14.0)
    assert text._has_glyph(helvetica, "a") and not text._has_glyph(helvetica, "→")

    page = Page(1, (1, 1, 0), 612, 792)
    n = note("→", [page])
    img = Image.open(io.BytesIO(render_page(page, 792, text=page_spans(n, page)))).convert("L")
    left, top, right, bottom = ImageOps.invert(img).getbbox() or (0, 0, 0, 0)
    assert right - left > bottom - top  # an arrow: wider than tall, not the missing-glyph box
