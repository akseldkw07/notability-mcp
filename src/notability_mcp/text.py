"""Lay out and draw typed text: the body text, flowed from page 1, and text boxes.

The constants are measured on the app's own PDF exports (see scripts/save_as.py and
tests/test_parity.py, which compare the two):

- body text starts at the page's top and left margins (plus INDENT_STEP per indent level) and
  wraps at its right margin;
- each line is as tall as its largest font's line height (line_height); a page holds as many
  whole lines as fit between its top and bottom margins, then the text continues on the next
  page;
- but on lined paper (or squared paper, when the note aligns text to lines), each line sits
  on one of the paper's lines (see rulings): those at least a font size below the top margin
  and above the bottom one;
- glyphs are drawn at GLYPH_SCALE x the style's size.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from notability_mcp.bundle import IMAGE_BLOCK, TEXT_BLOCK, Block, Note, Page, Run, Style

GLYPH_SCALE = 1.0  # glyphs at the style's size
# Line height and the baseline's depth below the line's top, in font sizes, measured on the
# app's exports; other fonts use their own metrics (ascent + descent + line gap, and ascent +
# line gap). Helvetica Neue's line height is its own (1.193); Inter gets more room than its own.
LINE_HEIGHT = {"Inter": 1.7}
BASELINE_DEPTH = {"Inter": 1.071, "Helvetica Neue": 1.0}
INDENT_STEP = 25.0  # points per paragraph indent level
SNAP_RAISE = 0.12  # on lined paper, the baseline sits this many font sizes above the line
TAB_STOP = 18.0  # points between tab stops, from the line's start

TEXT_COLOR = (0, 0, 0, 255)

_APP_FONTS = Path("/Applications/Notability.app/Contents/Resources")
_SYSTEM_FONTS = Path("/System/Library/Fonts")
# (file, collection index) per (bold, italic)
_FACES: dict[str, dict[tuple[bool, bool], tuple[Path, int]]] = {
    "Helvetica Neue": {
        (False, False): (_SYSTEM_FONTS / "HelveticaNeue.ttc", 0),
        (True, False): (_SYSTEM_FONTS / "HelveticaNeue.ttc", 1),
        (False, True): (_SYSTEM_FONTS / "HelveticaNeue.ttc", 2),
        (True, True): (_SYSTEM_FONTS / "HelveticaNeue.ttc", 3),
    },
    "Inter": {
        (False, False): (_APP_FONTS / "Inter-Regular.ttf", 0),
        (True, False): (_APP_FONTS / "Inter-Bold.ttf", 0),
        (False, True): (_APP_FONTS / "Inter-Italic.ttf", 0),
        (True, True): (_APP_FONTS / "Inter-BoldItalic.ttf", 0),
    },
    "Times New Roman": {
        (False, False): (_SYSTEM_FONTS / "Supplemental" / "Times New Roman.ttf", 0),
        (True, False): (_SYSTEM_FONTS / "Supplemental" / "Times New Roman Bold.ttf", 0),
        (False, True): (_SYSTEM_FONTS / "Supplemental" / "Times New Roman Italic.ttf", 0),
        (True, True): (_SYSTEM_FONTS / "Supplemental" / "Times New Roman Bold Italic.ttf", 0),
    },
}


@dataclass(frozen=True)
class Font:
    family: str
    size: float  # points, as the style says (glyphs are drawn GLYPH_SCALE times this)
    bold: bool = False
    italic: bool = False


@dataclass
class Span:
    """Text drawn on a page: one style, starting at (x, baseline) in points."""

    x: float
    baseline: float
    text: str
    font: Font
    color: tuple[int, int, int, int]
    underline: bool = False
    strikethrough: bool = False


@cache
def _truetype(font: Font, px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    faces = _FACES.get(font.family) or _FACES["Helvetica Neue"]
    path, index = faces[(font.bold, font.italic)]
    for candidate, i in ((path, index), *faces.values()):  # a missing face: any of the family
        try:
            return ImageFont.truetype(str(candidate), px, index=i)
        except OSError:
            continue
    return ImageFont.load_default(px)


FALLBACK = (_SYSTEM_FONTS / "Supplemental" / "Arial Unicode.ttf", 0)  # for missing glyphs


@cache
def _fallback(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(FALLBACK[0]), px, index=FALLBACK[1])
    except OSError:
        return ImageFont.load_default(px)


@cache
def _has_glyph(font: Font, ch: str) -> bool:
    """Whether the font draws `ch`, not its missing-glyph box (macOS substitutes another
    font's glyph there, e.g. arrows missing from Helvetica Neue)."""
    if ch.isspace() or ord(ch) < 0x250:  # Latin: every font here has it
        return True
    pil = _truetype(font, 40)

    def bitmap(c: str) -> bytes:
        img = Image.new("L", (80, 80))
        ImageDraw.Draw(img).text((10, 60), c, font=pil, fill=255, anchor="ls")
        return img.tobytes()

    return bitmap(ch) != bitmap("\U0010fffd")


def _segments(text: str, font: Font) -> list[tuple[str, bool]]:
    """`text` cut into runs the font has glyphs for (True) and runs it lacks (False)."""
    out: list[tuple[str, bool]] = []
    for ch in text:
        has = _has_glyph(font, ch)
        if out and out[-1][1] == has:
            out[-1] = (out[-1][0] + ch, has)
        else:
            out.append((ch, has))
    return out


def text_width(text: str, font: Font) -> float:
    """Advance width of `text` in points."""
    px = 200  # measure at a large size, then scale: font hinting at small sizes skews widths
    width = 0.0
    for part, has in _segments(text, font):
        width += (_truetype(font, px) if has else _fallback(px)).getlength(part)
    return width * font.size * GLYPH_SCALE / px


def line_height(font: Font) -> float:
    """A line of `font`'s height in points."""
    if font.family in LINE_HEIGHT:
        return LINE_HEIGHT[font.family] * font.size
    metrics = _truetype(font, 1000).font
    return metrics.height / 1000 * font.size  # pyright: ignore[reportAttributeAccessIssue]


def baseline_depth(font: Font) -> float:
    """How far below a line's top its baseline is, in points."""
    if font.family in BASELINE_DEPTH:
        return BASELINE_DEPTH[font.family] * font.size
    metrics = _truetype(font, 1000).font
    gap = metrics.height - metrics.ascent - metrics.descent  # pyright: ignore[reportAttributeAccessIssue]
    return (metrics.ascent + gap) / 1000 * font.size  # pyright: ignore[reportAttributeAccessIssue]


def _resolve(style: Style, default: tuple[str, float]) -> Font:
    return Font(style.family or default[0], style.size or default[1], style.bold, style.italic)


type Piece = tuple[str, Style, Font]
type Line = list[Piece]
type Paragraph = list[tuple[str, Style]]


def paragraphs(runs: list[Run]) -> list[Paragraph]:
    """Split styled runs at newlines into paragraphs of (text, style) segments."""
    out: list[Paragraph] = [[]]
    for run in runs:
        for i, part in enumerate(run.text.split("\n")):
            if i:
                out.append([])
            out[-1].append((part, run.style))
    return out


def wrap(paragraph: Paragraph, default: tuple[str, float], width: float) -> list[Line]:
    """Break a paragraph into lines no wider than `width` points, between words (a word wider
    than the line is broken by character). An empty paragraph is one empty line."""
    lines: list[Line] = [[]]
    x = 0.0
    for text, style in paragraph:
        font = _resolve(style, default)
        for word in _words(text):
            # trailing spaces may hang past the margin
            if x > 0 and advance(word.rstrip(" "), font, x) > width:
                lines.append([])
                x = 0.0
            while advance(word, font, 0.0) > width and len(word) > 1:  # an over-long word
                cut = _fit(word, font, width - x)
                lines[-1].append((word[:cut], style, font))
                lines.append([])
                x, word = 0.0, word[cut:]
            if word:
                lines[-1].append((word, style, font))
                x = advance(word, font, x)
    return lines


def advance(text: str, font: Font, x: float) -> float:
    """Where text set from `x` (points from the line's start) ends: a tab goes on to the next
    TAB_STOP."""
    for i, part in enumerate(text.split("\t")):
        if i:
            x = (x // TAB_STOP + 1) * TAB_STOP
        x += text_width(part, font)
    return x


def _words(text: str) -> list[str]:
    """Words with their trailing spaces: 'a  b' -> ['a  ', 'b']."""
    out: list[str] = []
    for ch in text:
        if out and (ch == " " or not out[-1].endswith(" ")):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def _fit(word: str, font: Font, width: float) -> int:
    n = 1
    while n < len(word) and text_width(word[: n + 1], font) <= width:
        n += 1
    return n


def _tallest(line: Line, paragraph: Paragraph, default: tuple[str, float]) -> Font:
    """The line's tallest font (an empty line: its paragraph's)."""
    fonts = [font for _, _, font in line] or [_resolve(style, default) for _, style in paragraph]
    return max(fonts, key=line_height, default=_resolve(Style(), default))


def _spans(line: Line, x: float, baseline: float, width: float, align: int) -> list[Span]:
    """Place a line's pieces from `x` (or centred / right-aligned in `width`)."""
    natural = 0.0
    for i, (text, _, font) in enumerate(line):
        natural = advance(text.rstrip(" ") if i == len(line) - 1 else text, font, natural)
    start = x
    if align == 2:
        start += (width - natural) / 2
    elif align == 3:
        start += width - natural
    out = []
    rel = 0.0  # from the line's start, where tab stops are counted
    for text, style, font in line:
        color = style.color or TEXT_COLOR
        for i, part in enumerate(text.split("\t")):
            if i:
                rel = (rel // TAB_STOP + 1) * TAB_STOP
            if part:
                out.append(Span(start + rel, baseline, part, font, color, style.underline,
                                style.strikethrough))  # fmt: skip
            rel += text_width(part, font)
    return out


def rulings(length: float, spacing: float) -> list[float]:
    """Positions of the paper's lines along an edge `length` long: `spacing` apart, laid out so
    the edge's middle falls between two lines (as on the app's paper)."""
    first = (length / 2 - spacing / 2) % spacing
    return [first + i * spacing for i in range(int((length - first) / spacing) + 1)]


class _Cursor:
    """Where the body text's next line goes on a page: on the paper's lines (see body_spans),
    else one line height after another."""

    def __init__(self, note: Note, page: Page) -> None:
        self.page = page
        self.top, self.bottom, self.left, self.right = page.margins
        self.y = self.top
        paper = page.paper
        self.slots: list[float] | None = None
        self.spacing = 0.0
        self.box = (0.0, 0.0)  # the last line's top and bottom: what images push aside
        self.ruled = False
        # text always sits on lined paper's lines; on squared paper, when the note says so
        lined = paper is not None and (
            paper.flair == 0 or (paper.flair == 2 and note.align_to_lines)
        )
        if paper and lined and (paper.spacing or 0) > 1:
            self.spacing = sp = paper.spacing or 0.0
            self.ruled = paper.flair == 0  # lined; squared paper uses its last line too
            last = page.height - self.bottom
            first = self.top + sp  # a whole line's room above it (as the Mac app lays it out)
            self.slots = [y for y in rulings(page.height, sp) if first <= y <= last]

    @property
    def width(self) -> float:
        return self.page.width - self.left - self.right

    def next(self, font: Font, text: bool = True) -> float | None:
        """The baseline for a line whose tallest font is `font`, or None when the page is
        full. On lined (not squared) paper a line with `text` also needs room below it: the
        app moves it to the next page rather than onto a line close to the page's bottom (an
        empty line may take that line)."""
        if self.slots is not None:
            if not self.slots:
                return None
            room = self.page.height - self.bottom - self.slots[0]
            # measured: a line 6.6 pt above the bottom is skipped, 17 pt is used; one right on
            # the bottom edge is used too (it is also the next page's top line)
            if text and self.ruled and 0.5 < room < font.size:
                return None
            line = self.slots.pop(0)
            self.box = (line - self.spacing, line)
            return line - SNAP_RAISE * font.size
        height = line_height(font)
        if self.y + height > self.page.height - self.bottom and self.y > self.top:
            return None
        baseline = self.y + baseline_depth(font)
        self.box = (self.y, self.y + height)
        self.y += height
        return baseline


def body_spans(note: Note) -> dict[int, list[Span]]:
    """The body text placed on the note's pages, by page number. Each line runs from the left
    margin (plus the paragraph's indent) to the right margin, around any pasted image it
    crosses (the app wraps text around images: what fits left of one, then right of it)."""
    out: dict[int, list[Span]] = {}
    if not note.body or not note.pages:
        return out
    pages = iter(note.pages)
    cursor = _Cursor(note, next(pages))
    for paragraph in paragraphs(note.body):
        style = paragraph[0][1]
        indent = INDENT_STEP * style.indent
        pieces = [
            (word, st, font)
            for text, st in paragraph
            for font in [_resolve(st, note.font)]
            for word in _words(text)
        ]
        while True:
            # the line's height: from the words it would hold across the whole width
            width = cursor.width - indent
            guess = wrap([(w, st) for w, st, _ in pieces], note.font, width)[0] if pieces else []
            tallest = _tallest(guess, paragraph, note.font)
            while (baseline := cursor.next(tallest, bool(pieces))) is None:
                page = next(pages, None)
                if page is None:
                    return out  # the app adds pages as text grows; the log has them all
                cursor = _Cursor(note, page)
            if not pieces:
                break  # an empty paragraph is one empty line
            left, right = cursor.left + indent, cursor.page.width - cursor.right
            gaps = _gaps(cursor.page, left, right, *cursor.box)
            spans, pieces = _fill(pieces, gaps, left, right, baseline, style.align)
            out.setdefault(cursor.page.number, []).extend(spans)
            if not pieces:
                break
    return out


def _gaps(
    page: Page, left: float, right: float, top: float, bottom: float
) -> list[tuple[float, float]]:
    """The stretches of a line (from `left` to `right`, `top` to `bottom`: on lined paper the
    whole space between two lines) that no pasted image covers."""
    gaps = [(left, right)]
    pad = page.margins[2]
    for block in page.blocks:
        if block.kind != IMAGE_BLOCK:
            continue
        (x, y), (w, h) = block.origin, block.size
        x1, y1 = x + w * abs(block.scale[0] or 1.0), y + h * abs(block.scale[1] or 1.0)
        if y1 <= top or y >= bottom:
            continue
        gaps = [
            part
            for a, b in gaps
            # text keeps the page's side margin away from an image (measured: 18.7 pt on a
            # page with 18.7 pt margins)
            for part in ((a, min(b, x - pad)), (max(a, x1 + pad), b))
            if part[1] - part[0] > 1
        ]
    return gaps


def _fill(
    pieces: list[Piece],
    gaps: list[tuple[float, float]],
    left: float,
    right: float,
    baseline: float,
    align: int,
) -> tuple[list[Span], list[Piece]]:
    """Set words on one line, in its gaps from left to right; returns the spans and the words
    left for the next line. A word wider than a clear line is broken."""
    spans: list[Span] = []
    rest = list(pieces)
    for a, b in gaps:
        x = a
        while rest:
            word, style, font = rest[0]
            if left + advance(word.rstrip(" "), font, x - left) > b:
                if x == a and (a, b) == (left, right) and len(word) > 1:  # too long for a line
                    cut = _fit(word, font, b - a)
                    spans.extend(_place(word[:cut], style, font, x, left, baseline))
                    rest[0] = (word[cut:], style, font)
                break
            spans.extend(_place(word, style, font, x, left, baseline))
            x = left + advance(word, font, x - left)
            rest.pop(0)
        if not rest or (a, b) == (left, right):
            break
    if align in (2, 3) and gaps == [(left, right)] and spans:
        natural = max(sp.x + text_width(sp.text.rstrip(" "), sp.font) for sp in spans) - left
        shift = (right - left - natural) / (2 if align == 2 else 1)
        for sp in spans:
            sp.x += shift
    return spans, rest


def _place(
    word: str, style: Style, font: Font, x: float, left: float, baseline: float
) -> list[Span]:
    """Spans for a word set from `x`, tabs moving on to the next stop from the line's start."""
    out = []
    color = style.color or TEXT_COLOR
    rel = x - left
    for i, part in enumerate(word.split("\t")):
        if i:
            rel = (rel // TAB_STOP + 1) * TAB_STOP
        if part:
            out.append(Span(left + rel, baseline, part, font, color, style.underline,
                            style.strikethrough))  # fmt: skip
        rel += text_width(part, font)
    return out


def box_spans(block: Block, default: tuple[str, float]) -> list[Span]:
    """A text box's text, wrapped to its width from its top-left corner."""
    if block.kind != TEXT_BLOCK or not block.text:
        return []
    sx, sy = block.scale
    width = block.size[0] * (sx or 1.0)
    out: list[Span] = []
    y = block.origin[1]
    for paragraph in paragraphs(block.text):
        style = paragraph[0][1]
        indent = INDENT_STEP * style.indent
        for line in wrap(paragraph, default, width - indent):
            tallest = _tallest(line, paragraph, default)
            x = block.origin[0] + indent
            baseline = y + baseline_depth(tallest) * (sy or 1.0)
            out.extend(_spans(line, x, baseline, width - indent, style.align))
            y += line_height(tallest) * (sy or 1.0)
    return out


def page_spans(note: Note, page: Page, body: dict[int, list[Span]] | None = None) -> list[Span]:
    """Everything typed on `page`: its share of the body text and its text boxes."""
    body = body_spans(note) if body is None else body
    spans = list(body.get(page.number, []))
    for block in page.blocks:
        spans.extend(box_spans(block, note.font))
    return spans


def draw(img_draw: ImageDraw.ImageDraw, spans: list[Span], s: float) -> None:
    """Draw spans on an image at `s` pixels per point."""
    for span in spans:
        if not span.text.strip():
            continue
        px = max(1, round(span.font.size * GLYPH_SCALE * s))
        font = _truetype(span.font, px)
        color = span.color[:3]
        x, y = span.x * s, span.baseline * s
        at = x
        for part, has in _segments(span.text, span.font):
            pil = font if has else _fallback(px)
            img_draw.text((at, y), part, font=pil, fill=color, anchor="ls")
            at += text_width(part, span.font) * s
        if span.underline or span.strikethrough:
            w = text_width(span.text.rstrip(), span.font) * s
            thick = max(1, round(px / 14))
            for on, dy in ((span.underline, px * 0.12), (span.strikethrough, -px * 0.3)):
                if on:
                    img_draw.line([(x, y + dy), (x + w, y + dy)], fill=color, width=thick)
