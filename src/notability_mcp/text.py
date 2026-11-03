"""Lay out and draw typed text: the body text, flowed from page 1, and text boxes.

The constants are measured on the app's own PDF exports (see scripts/save_as.py and
tests/test_parity.py, which compare the two):

- body text starts at the page's top and left margins (plus INDENT_STEP per indent level) and
  wraps at its right margin;
- each line is LINE_PITCH x the font size tall; a page holds as many whole lines as fit
  between its top and bottom margins, then the text continues on the next page;
- but when the note aligns text to lines and the paper is lined or squared, each line sits on
  one of the paper's lines (see rulings): those at least a font size below the top margin
  and above the bottom one;
- glyphs are drawn at GLYPH_SCALE x the style's size.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import ImageDraw, ImageFont

from notability_mcp.bundle import TEXT_BLOCK, Block, Note, Page, Run, Style

GLYPH_SCALE = 1.0  # glyphs at the style's size
LINE_PITCH = 1.7  # body text line height, in font sizes (23.8 pt at 14 pt)
BOX_LINE_PITCH = 1.2  # text box line height, in font sizes (not yet measured)
BASELINE = 0.64  # the baseline's depth in its line, as a fraction of the line height
INDENT_STEP = 25.0  # points per paragraph indent level
SNAP_RAISE = 0.12  # on lined paper, the baseline sits this many font sizes above the line
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


def text_width(text: str, font: Font) -> float:
    """Advance width of `text` in points."""
    px = 200  # measure at a large size, then scale: font hinting at small sizes skews widths
    return _truetype(font, px).getlength(text) * font.size * GLYPH_SCALE / px


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
            w = text_width(word, font)
            # trailing spaces may hang past the margin
            if x > 0 and x + text_width(word.rstrip(" "), font) > width:
                lines.append([])
                x = 0.0
            while w > width and len(word) > 1:  # break an over-long word
                cut = _fit(word, font, width - x)
                lines[-1].append((word[:cut], style, font))
                lines.append([])
                x, word = 0.0, word[cut:]
                w = text_width(word, font)
            if word:
                lines[-1].append((word, style, font))
                x += w
    return lines


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


def _size(line: Line, paragraph: Paragraph, default: tuple[str, float]) -> float:
    """The line's largest font size (an empty line: its paragraph's)."""
    sizes = [font.size for _, _, font in line] or [
        _resolve(style, default).size for _, style in paragraph
    ]
    return max(sizes, default=default[1])


def _spans(line: Line, x: float, baseline: float, width: float, align: int) -> list[Span]:
    """Place a line's pieces from `x` (or centred / right-aligned in `width`)."""
    natural = sum(text_width(t, f) for t, _, f in line[:-1])
    if line:
        natural += text_width(line[-1][0].rstrip(" "), line[-1][2])
    if align == 2:
        x += (width - natural) / 2
    elif align == 3:
        x += width - natural
    out = []
    for text, style, font in line:
        color = style.color or TEXT_COLOR
        out.append(Span(x, baseline, text, font, color, style.underline, style.strikethrough))
        x += text_width(text, font)
    return out


def rulings(length: float, spacing: float) -> list[float]:
    """Positions of the paper's lines along an edge `length` long: `spacing` apart, laid out so
    the edge's middle falls between two lines (as on the app's paper)."""
    first = (length / 2 - spacing / 2) % spacing
    return [first + i * spacing for i in range(int((length - first) / spacing) + 1)]


class _Cursor:
    """Where the body text's next line goes on a page: on the paper's lines when the note
    aligns text to lines and the paper has them, else one LINE_PITCH line after another."""

    def __init__(self, note: Note, page: Page) -> None:
        self.page = page
        self.top, self.bottom, self.left, self.right = page.margins
        self.y = self.top
        paper = page.paper
        self.slots: list[float] | None = None
        if note.align_to_lines and paper and paper.flair in (0, 2) and (paper.spacing or 0) > 1:
            sp = paper.spacing or 0.0
            last = page.height - self.bottom
            first = self.top + note.font[1]  # room above the line for a line of text
            self.slots = [y for y in rulings(page.height, sp) if first <= y <= last]

    @property
    def width(self) -> float:
        return self.page.width - self.left - self.right

    def next(self, size: float) -> float | None:
        """The baseline for a line of `size` pt text, or None when the page is full."""
        if self.slots is not None:
            return self.slots.pop(0) - SNAP_RAISE * size if self.slots else None
        height = LINE_PITCH * size
        if self.y + height > self.page.height - self.bottom and self.y > self.top:
            return None
        baseline = self.y + height * BASELINE
        self.y += height
        return baseline


def body_spans(note: Note) -> dict[int, list[Span]]:
    """The body text placed on the note's pages, by page number. Each paragraph wraps to the
    width of the page it starts on, less its indent."""
    out: dict[int, list[Span]] = {}
    if not note.body or not note.pages:
        return out
    pages = iter(note.pages)
    cursor = _Cursor(note, next(pages))
    for paragraph in paragraphs(note.body):
        style = paragraph[0][1]
        indent = INDENT_STEP * style.indent
        width = cursor.width - indent
        for line in wrap(paragraph, note.font, width):
            size = _size(line, paragraph, note.font)
            while (baseline := cursor.next(size)) is None:
                page = next(pages, None)
                if page is None:
                    return out  # the app adds pages as text grows; the log has them all
                cursor = _Cursor(note, page)
            if line:
                x = cursor.left + indent
                spans = _spans(line, x, baseline, width, style.align)
                out.setdefault(cursor.page.number, []).extend(spans)
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
            height = BOX_LINE_PITCH * _size(line, paragraph, default) * (sy or 1.0)
            x = block.origin[0] + indent
            out.extend(_spans(line, x, y + height * BASELINE, width - indent, style.align))
            y += height
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
        img_draw.text((x, y), span.text, font=font, fill=color, anchor="ls")
        if span.underline or span.strikethrough:
            w = text_width(span.text.rstrip(), span.font) * s
            thick = max(1, round(px / 14))
            for on, dy in ((span.underline, px * 0.12), (span.strikethrough, -px * 0.3)):
                if on:
                    img_draw.line([(x, y + dy), (x + w, y + dy)], fill=color, width=thick)
