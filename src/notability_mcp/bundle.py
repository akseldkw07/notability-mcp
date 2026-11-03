"""Decode a Notability note bundle (a FlatBuffers op log) into pages, ink and typed text.

A note is a list of CRDT ops ordered by Lamport time (timestamp, site). The wire layout was
reverse-engineered by notability-cli; field ids below are from its schema:
https://github.com/Zpankz/notability-cli/blob/d49dc45/crates/nb-protocol/schema/notability-ops.fbs
"""

import struct
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from enum import IntEnum

type Id = tuple[int, int]  # (site, timestamp)
type SeqId = tuple[int, int, int]  # (site, timestamp, index)


class _Op(IntEnum):
    """PayloadUnion tags (member index + 1) for the ops this module reads."""

    SET_METADATA = 1
    CREATE_PAGE = 3
    MODIFY_PAGE = 4
    INSERT_CHAR = 7
    INSERT_STRING = 8
    REMOVE_CHAR = 9
    REMOVE_CHARS = 10
    REVIVE_CHARS = 11
    MODIFY_STYLE = 12
    MODIFY_PARAGRAPH_STYLE = 13
    CLEAR_STYLE = 14
    CREATE_INK = 15
    ADD_PATH_ELEMENTS = 16
    MODIFY_INK = 17
    CREATE_SHAPE = 18
    MODIFY_SHAPE = 19
    CREATE_BLOCK = 22
    MODIFY_BLOCK = 23
    MODIFY_POSITIONS = 24
    DELETE_ENTITIES = 25


HIGHLIGHTER = 2  # CreateInk.tool
DEFAULT_PAGE_SIZE = (612.0, 792.0)
_MAX_PAGES_PER_OP = 10_000  # bounds a corrupt or hostile pageCount
_ID, _SEQ = struct.Struct("<H2xI"), struct.Struct("<H2xII")
_PAIR, _RGBA, _HASH = struct.Struct("<ff"), struct.Struct("<4B"), struct.Struct("<64s")
_RECT = struct.Struct("<ffff")
_UUID = struct.Struct("<QQ")
_BOUNDARY = struct.Struct("<H2xIIb3x")  # SeqId, then BoundaryType


class _Table:
    """Minimal FlatBuffers table reader: fields are addressed by schema field id."""

    __slots__ = ("buf", "pos", "vlen", "vtable")

    def __init__(self, buf: bytes, pos: int) -> None:
        self.buf, self.pos = buf, pos
        self.vtable = pos - struct.unpack_from("<i", buf, pos)[0]
        self.vlen = struct.unpack_from("<H", buf, self.vtable)[0]

    def _at(self, fid: int) -> int | None:
        slot = 4 + 2 * fid
        off = struct.unpack_from("<H", self.buf, self.vtable + slot)[0] if slot < self.vlen else 0
        return self.pos + off if off else None

    def _deref(self, fid: int) -> int | None:
        p = self._at(fid)
        return None if p is None else p + struct.unpack_from("<I", self.buf, p)[0]

    def scalar[T](self, fid: int, fmt: str, default: T) -> T:
        p = self._at(fid)
        return default if p is None else struct.unpack_from(fmt, self.buf, p)[0]

    def _unpack(self, fid: int, s: struct.Struct) -> tuple | None:
        p = self._at(fid)
        return None if p is None else s.unpack_from(self.buf, p)

    def id(self, fid: int) -> Id | None:
        return self._unpack(fid, _ID)

    def seq(self, fid: int) -> SeqId | None:
        return self._unpack(fid, _SEQ)

    def pair(self, fid: int) -> tuple[float, float] | None:
        """A Point or Size struct."""
        return self._unpack(fid, _PAIR)

    def rgba(self, fid: int) -> tuple[int, int, int, int] | None:
        return self._unpack(fid, _RGBA)

    def digest(self, fid: int) -> str | None:
        """An AssetHash struct (SHA-512) as hex."""
        t = self._unpack(fid, _HASH)
        return None if t is None else t[0].hex()

    def table(self, fid: int) -> "_Table | None":
        p = self._deref(fid)
        return None if p is None else _Table(self.buf, p)

    def string(self, fid: int) -> str | None:
        p = self._deref(fid)
        if p is None:
            return None
        n = struct.unpack_from("<I", self.buf, p)[0]
        return self.buf[p + 4 : p + 4 + n].decode("utf-8", "replace")

    def _vec(self, fid: int) -> tuple[int, int]:
        p = self._deref(fid)
        return (0, 0) if p is None else (p + 4, struct.unpack_from("<I", self.buf, p)[0])

    def bytes(self, fid: int) -> bytes:
        start, n = self._vec(fid)
        return self.buf[start : start + n]

    def _unpack_all(self, fid: int, s: struct.Struct) -> list:
        start, n = self._vec(fid)
        return [s.unpack_from(self.buf, start + i * s.size) for i in range(n)]

    def ids(self, fid: int) -> list[Id]:
        return self._unpack_all(fid, _ID)

    def seqs(self, fid: int) -> list[SeqId]:
        return self._unpack_all(fid, _SEQ)

    def pairs(self, fid: int) -> list[tuple[float, float]]:
        return self._unpack_all(fid, _PAIR)

    def tables(self, fid: int) -> Iterator["_Table"]:
        start, n = self._vec(fid)
        for i in range(n):
            p = start + 4 * i
            yield _Table(self.buf, p + struct.unpack_from("<I", self.buf, p)[0])


@dataclass
class Ink:
    page: SeqId | None
    origin: tuple[float, float]
    scale: tuple[float, float]
    rotation: float
    color: tuple[int, int, int, int]
    width: float
    tool: int
    paths: list[bytes]  # encoded centre paths: the original plus streamed AddPathElements chunks


@dataclass
class Block:
    """A placed object: an image (kind 1), or another kind this module doesn't draw."""

    page: SeqId | None
    kind: int
    corner: int  # which corner `origin` anchors (0: top left)
    origin: tuple[float, float]
    size: tuple[float, float]  # on the page, in points
    scale: tuple[float, float]
    rotation: float
    asset: str | None  # SHA-512 hex of the image
    crop: tuple[float, float, float, float] | None  # x, y, width, height in image pixels
    text: list["Run"] = field(default_factory=list)  # a text box's text


TEXT_BLOCK, IMAGE_BLOCK = 0, 1
DEFAULT_FONT = ("Helvetica Neue", 14.0)


@dataclass(frozen=True)
class Style:
    """Style of typed text: the character's, and its paragraph's (indent, align); None means
    the note's default."""

    family: str | None = None
    size: float | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    subscript: bool = False
    superscript: bool = False
    color: tuple[int, int, int, int] | None = None
    indent: int = 0  # paragraph indent level
    align: int = 0  # paragraph alignment: 0 natural, 1 left, 2 centre, 3 right
    decorator: int = 0  # paragraph list style: 0 none, 1 bullet, 2 number, 3 checkbox
    line_spacing: float = 0.0  # paragraph line height multiple; 0: the font's own


@dataclass
class Run:
    text: str
    style: Style


# CreateShape.definition types (DefinitionUnion member index + 1)
LINE, POLYGON, ELLIPSE = 1, 2, 3


@dataclass
class Shape:
    """A recognized shape: a line (start/end, optional control points), a polygon, or an
    ellipse in a `size` box, in its own space before `origin`, `scale` and `rotation`."""

    page: SeqId | None
    kind: int  # LINE, POLYGON or ELLIPSE
    origin: tuple[float, float]
    scale: tuple[float, float]
    rotation: float
    points: list[tuple[float, float]]  # line: start, its control points, end; polygon: corners
    size: tuple[float, float]  # ellipse
    color: tuple[int, int, int, int]
    width: float
    fill: tuple[int, int, int, int] | None
    tool: int
    style: int  # 0 variable, 1 fixed width, 2 dashed, 3 dotted


@dataclass
class Paper:
    """A page's paper (PageBackground.paper): its pattern, spacing and colour."""

    flair: int | None = None  # 0 lines, 1 dots, 2 grid; None: blank
    spacing: float | None = None  # points between lines or dots
    bleeds: bool = True  # pattern runs to the page edges, or stops at the margins
    color: tuple[int, int, int, int] | None = None
    legacy: int | None = None  # legacyPaperIndex: 13 is plain white paper


@dataclass
class PdfPage:
    asset: str  # SHA-512 hex of the PDF asset
    index: int  # 0-based page within that PDF


@dataclass
class Page:
    number: int  # 1-based position in the note
    id: SeqId
    width: float
    height: float
    pdf: PdfPage | None = None
    paper: Paper | None = None
    margins: tuple[float, float, float, float] = (36.0, 36.0, 36.0, 36.0)  # top bottom left right
    inks: list[Ink] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    shapes: list[Shape] = field(default_factory=list)


@dataclass
class Note:
    id: str
    title: str | None
    pages: list[Page]
    text: str  # typed text, one block per text field
    # page number of each block (image, web link, math...) by its id, e.g. to place the app's
    # image OCR on its page
    block_pages: dict[Id, int] = field(default_factory=dict)
    body: list[Run] = field(default_factory=list)  # the body text, flowed from page 1
    font: tuple[str, float] = DEFAULT_FONT  # the note's default family and size
    align_to_lines: bool = False  # body text sits on the paper's lines


def read_ops(buf: bytes, ops_field: int) -> list[tuple[Id, int, _Table]]:
    """(op id, payload tag, payload) for every op in a root table, in Lamport order."""
    root = _Table(buf, struct.unpack_from("<I", buf, 0)[0])
    ops = []
    for op in root.tables(ops_field):
        oid, payload = op.id(0), op.table(5)
        if oid is not None and payload is not None:
            ops.append((oid, op.scalar(4, "<B", 0), payload))
    ops.sort(key=lambda o: (o[0][1], o[0][0]))
    return ops


def decode_bundle(buf: bytes) -> Note:
    """Decode a NoteBundle as returned by the collab API."""
    root = _Table(buf, struct.unpack_from("<I", buf, 0)[0])
    lo, hi = root._unpack(0, _UUID) or (0, 0)
    h = f"{(hi << 64) | lo:032x}"
    note_id = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
    return build_note(note_id, read_ops(buf, 6))


def decode_local(note_id: str, buf: bytes) -> Note:
    """Decode the Mac app's per-note change log (`notes/<UUID>` in its container): the same
    ops as a NoteBundle, but in root field 0, and without the note id."""
    return build_note(note_id, read_ops(buf, 0))


def linearize(nodes: list[tuple[SeqId, SeqId | None]]) -> list[SeqId]:
    """Order an RGA sequence: each node follows its anchor, newest sibling first."""
    kids: dict[SeqId | None, list[SeqId]] = defaultdict(list)
    for node, anchor in nodes:
        kids[anchor].append(node)
    for v in kids.values():
        v.sort(key=lambda n: (-n[1], -n[0], n[2]))
    out: list[SeqId] = []
    seen: set[SeqId] = set()  # a corrupt log can contain anchor cycles
    stack = list(reversed(kids.get(None, [])))
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
        stack.extend(reversed(kids.get(n, [])))
    return out


def _background(bg: _Table | None) -> dict:
    if bg is None:
        return {}
    out: dict = {}
    if size := bg.pair(3):
        out["size"] = size
    if paper := bg.table(0):
        out["paper"] = Paper(
            flair=paper.scalar(0, "<b", None),
            spacing=paper.scalar(1, "<f", None),
            bleeds=paper.scalar(2, "<?", True),
            color=paper.rgba(4),
            legacy=paper.scalar(5, "<B", None),
        )
    if margins := bg._unpack(4, _RECT):
        out["margins"] = margins
    if (pdf := bg.table(1)) and (meta := pdf.table(0)) and (digest := meta.digest(0)):
        out["pdf"] = (digest, pdf.scalar(4, "<I", 0), pdf.pairs(5))
    return out


def _styled_runs(
    seq: list[SeqId],
    glyph: dict[SeqId, str],
    removed: set[SeqId],
    styling: list[tuple[int, "_Table"]],
) -> list[Run]:
    """A text field's live characters as runs of one style, with ModifyStyle / ClearStyle ops
    applied in order over the ranges their boundaries mark (boundaries may sit on removed
    characters, so positions are taken in the full sequence)."""
    index = {node: i for i, node in enumerate(seq)}
    styles = [Style()] * len(seq)

    def position(b: tuple | None, default: int) -> int:
        if b is None:
            return default
        site, ts, i, kind = b
        if kind == 2:  # start of document
            return 0
        if kind == 3:  # end of document
            return len(seq)
        at = index.get((site, ts, i))
        return default if at is None else at + (kind == 1)  # 1: after the character

    for op, p in styling:
        if op == _Op.MODIFY_PARAGRAPH_STYLE:
            ends = [index.get(at) if (at := p.seq(f)) else None for f in (0, 1)]
            start, end = _paragraphs(seq, glyph, *ends)
            for i in range(start, end):
                styles[i] = _repara(styles[i], p)
            continue
        start = position(p._unpack(0, _BOUNDARY), 0)
        end = position(p._unpack(1, _BOUNDARY), len(seq))
        for i in range(start, min(end, len(seq))):
            if op == _Op.CLEAR_STYLE:  # paragraph attributes stay unless asked
                old = styles[i]
                keep = not p.scalar(2, "<?", False)
                styles[i] = Style(
                    indent=old.indent if keep else 0,
                    align=old.align if keep else 0,
                    decorator=old.decorator if keep else 0,
                    line_spacing=old.line_spacing if keep else 0.0,
                )
            else:
                styles[i] = _restyle(styles[i], p)
    runs: list[Run] = []
    for node, style in zip(seq, styles, strict=True):
        if node in removed:
            continue
        if runs and runs[-1].style == style:
            runs[-1].text += glyph[node]
        else:
            runs.append(Run(glyph[node], style))
    return runs


def _paragraphs(
    seq: list[SeqId], glyph: dict[SeqId, str], start: int | None, end: int | None
) -> tuple[int, int]:
    """The whole paragraphs from position `start` to `end` (inclusive), as a range: back to
    the character after the previous newline, on to the next newline (included)."""
    first = 0 if start is None else start
    last = len(seq) - 1 if end is None else end
    if first < last and glyph[seq[first]] == "\n":
        first += 1  # starts on the newline that ends the paragraph before: not that one
    while first > 0 and glyph[seq[first - 1]] != "\n":
        first -= 1
    while last < len(seq) - 1 and glyph[seq[last]] != "\n":
        last += 1
    return first, last + 1


def _repara(style: Style, p: "_Table") -> Style:
    """ModifyParagraphStyle's indent level (field 2), alignment (3), line spacing (4) and list
    style (5) over `style`."""
    changes: dict = {}
    if t := p.table(2):
        changes["indent"] = t.scalar(0, "<B", 0)
    if t := p.table(3):
        changes["align"] = t.scalar(0, "<b", 0)
    if t := p.table(4):
        changes["line_spacing"] = t.scalar(0, "<f", 0.0)
    if t := p.table(5):
        changes["decorator"] = t.scalar(0, "<b", 0)
    return replace(style, **changes) if changes else style


def _restyle(style: Style, p: "_Table") -> Style:
    """ModifyStyle's Set* fields over `style` (each Set* table holds its value in field 0)."""
    changes: dict = {}
    flags = ((3, "bold"), (4, "italic"), (5, "underline"), (11, "superscript"),
             (12, "subscript"), (13, "strikethrough"))  # fmt: skip
    for fid, name in flags:
        if t := p.table(fid):
            changes[name] = t.scalar(0, "<?", False)
    if t := p.table(7):
        changes["family"] = t.string(0)
    if t := p.table(8):
        changes["size"] = t.scalar(0, "<f", None)
    if t := p.table(9):
        changes["color"] = t.rgba(0)
    return replace(style, **changes) if changes else style


def exported_page_count(pages: list[Page]) -> int:
    """How many pages the app's own export has: all of them, less the last if it's empty (the
    page the app adds as you write; earlier empty pages stay). A page is empty without ink,
    shapes, images or a PDF page of its own (one repeating a template PDF page seen earlier in
    the note doesn't count). Typed text doesn't count either: body text that runs onto that
    page is left out of the app's export too."""
    if len(pages) < 2:
        return len(pages)
    last = pages[-1]
    earlier = {(p.pdf.asset, p.pdf.index) for p in pages[:-1] if p.pdf}
    own_pdf = last.pdf is not None and (last.pdf.asset, last.pdf.index) not in earlier
    empty = not (last.inks or last.shapes or last.blocks or own_pdf)
    return len(pages) - 1 if empty else len(pages)


def _definition(
    kind: int, d: _Table | None
) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    """(points, size) of a shape definition: Line {0 start, 1-2 control points, 3 end},
    Polygon {0 points} or NormalShape {1 size}."""
    if d is None:
        return [], (0.0, 0.0)
    if kind == LINE:
        start, end = d.pair(0) or (0.0, 0.0), d.pair(3) or (0.0, 0.0)
        c1, c2 = d.pair(1), d.pair(2)
        # one control point: a quadratic curve; two: a cubic
        return [start, *(c for c in (c1, c2) if c), end], (0.0, 0.0)
    if kind == POLYGON:
        return d.pairs(0), (0.0, 0.0)
    return [], d.pair(1) or (0.0, 0.0)


def build_note(note_id: str, ops: list[tuple[Id, int, _Table]]) -> Note:
    title: str | None = None
    note_bg: dict = {}
    page_nodes: list[tuple[SeqId, SeqId | None]] = []
    page_pos: dict[SeqId, SeqId] = {}  # page id -> its current node in the page sequence
    page_bg: dict[SeqId, dict] = {}
    inks: dict[Id, Ink] = {}
    blocks: dict[Id, Block] = {}
    shapes: dict[Id, Shape] = {}
    deleted: dict[Id | SeqId, bool] = {}
    chars: dict[Id | None, list[tuple[SeqId, SeqId | None, str]]] = defaultdict(list)
    removed: set[SeqId] = set()
    font = DEFAULT_FONT
    align_to_lines = False
    styling: dict[Id | None, list[tuple[int, _Table]]] = defaultdict(list)

    for (site, ts), kind, p in ops:
        match kind:
            case _Op.SET_METADATA:
                if (t := p.table(0)) and (s := t.string(0)) is not None:
                    title = s
                if (t := p.table(1)) and (bg := t.table(0)):
                    note_bg = _background(bg)
                if family := p.string(4):
                    font = (family, font[1])
                if (size := p.scalar(5, "<f", 0.0)) > 0:
                    font = (font[0], size)
                align_to_lines = p.scalar(3, "<?", align_to_lines)
            case _Op.MODIFY_STYLE | _Op.CLEAR_STYLE | _Op.MODIFY_PARAGRAPH_STYLE:
                field_of = {_Op.MODIFY_STYLE: 2, _Op.CLEAR_STYLE: 3, _Op.MODIFY_PARAGRAPH_STYLE: 7}
                styling[p.id(field_of[kind])].append((kind, p))
            case _Op.CREATE_PAGE:
                prev, bg = p.seq(0), _background(p.table(1))
                for i in range(min(p.scalar(2, "<I", 0), _MAX_PAGES_PER_OP) or 1):
                    node = (site, ts, i)
                    page_nodes.append((node, prev))
                    page_pos[node] = node
                    if bg:
                        page_bg[node] = bg
                    prev = node
            case _Op.MODIFY_PAGE:
                pages = p.seqs(0)
                if move := p.table(1):
                    prev = move.seq(0)
                    for i, pid in enumerate(pages):
                        node = (site, ts, i)
                        page_nodes.append((node, prev))
                        page_pos[pid] = node
                        prev = node
                if (t := p.table(2)) and (bg := t.table(0)):
                    for pid in pages:
                        page_bg[pid] = _background(bg)
            case _Op.CREATE_INK:
                inks[(site, ts)] = Ink(
                    page=p.seq(0),
                    origin=p.pair(1) or (0.0, 0.0),
                    rotation=p.scalar(2, "<f", 0.0),
                    scale=p.pair(3) or (1.0, 1.0),
                    tool=p.scalar(4, "<b", 0),
                    color=p.rgba(7) or (0, 0, 0, 255),
                    width=p.scalar(8, "<f", 1.0),
                    paths=[p.bytes(9)],
                )
            case _Op.ADD_PATH_ELEMENTS:
                if (oid := p.id(0)) and (ink := inks.get(oid)):
                    ink.paths.append(p.bytes(1))
            case _Op.MODIFY_INK:
                for oid in p.ids(0):
                    if ink := inks.get(oid):
                        ink.page = p.seq(1) or ink.page
                        ink.origin = p.pair(2) or ink.origin
                        ink.color = p.rgba(6) or ink.color
                        ink.width = p.scalar(7, "<f", ink.width)
                        if path := p.bytes(8):
                            ink.paths = [path]
            case _Op.CREATE_BLOCK:
                image = p.table(10)
                meta = image.table(0) if image else None
                blocks[(site, ts)] = Block(
                    page=p.seq(2),
                    kind=p.scalar(0, "<b", 0),
                    corner=p.scalar(1, "<b", 0),
                    origin=p.pair(3) or (0.0, 0.0),
                    size=p.pair(6) or (0.0, 0.0),
                    scale=p.pair(5) or (1.0, 1.0),
                    rotation=p.scalar(4, "<f", 0.0),
                    asset=meta.digest(0) if meta else None,
                    crop=p._unpack(11, _RECT),  # type: ignore[arg-type]
                )
            case _Op.MODIFY_BLOCK:
                for oid in p.ids(0):
                    if (block := blocks.get(oid)) is None:
                        continue
                    block.page = p.seq(2) or block.page
                    block.origin = p.pair(3) or block.origin
                    block.size = p.pair(6) or block.size
                    if rot := p.table(4):
                        block.rotation = rot.scalar(0, "<f", 0.0)
                    if (sc := p.table(5)) and (size := sc.pair(0)):
                        block.scale = size
                    if (crop := p.table(12)) and (rect := crop._unpack(0, _RECT)):
                        block.crop = rect  # type: ignore[assignment]
            case _Op.CREATE_SHAPE:
                points, size = _definition(p.scalar(4, "<B", 0), p.table(5))
                shapes[(site, ts)] = Shape(
                    page=p.seq(0),
                    kind=p.scalar(4, "<B", 0),
                    origin=p.pair(1) or (0.0, 0.0),
                    rotation=p.scalar(2, "<f", 0.0),
                    scale=p.pair(3) or (1.0, 1.0),
                    points=points,
                    size=size,
                    tool=p.scalar(6, "<b", 0),
                    style=p.scalar(7, "<b", 1),
                    color=p.rgba(9) or (0, 0, 0, 255),
                    width=p.scalar(10, "<f", 4.0),
                    fill=p.rgba(11),
                )
            case _Op.MODIFY_SHAPE:
                for oid in p.ids(0):
                    if (shape := shapes.get(oid)) is None:
                        continue
                    shape.page = p.seq(1) or shape.page
                    shape.origin = p.pair(2) or shape.origin
                    if rot := p.table(3):
                        shape.rotation = rot.scalar(0, "<f", 0.0)
                    if (sc := p.table(4)) and (size := sc.pair(0)):
                        shape.scale = size
                    if (kind := p.scalar(5, "<B", 0)) and (d := p.table(6)):
                        shape.kind = kind
                        shape.points, shape.size = _definition(kind, d)
                    shape.tool = p.scalar(7, "<b", shape.tool)
                    shape.style = p.scalar(8, "<b", shape.style)
                    shape.color = p.rgba(10) or shape.color
                    shape.width = p.scalar(11, "<f", shape.width)
                    if fill := p.table(12):
                        shape.fill = fill.rgba(0)
            case _Op.MODIFY_POSITIONS:
                for m in p.tables(0):
                    oid = m.id(0)
                    if oid is None:
                        continue
                    thing = inks.get(oid) or blocks.get(oid) or shapes.get(oid)
                    if thing is None:
                        continue
                    thing.page = m.seq(1) or thing.page
                    thing.origin = m.pair(2) or thing.origin
                    if rot := m.table(3):
                        thing.rotation = rot.scalar(0, "<f", 0.0)
                    if (sc := m.table(4)) and (size := sc.pair(0)):
                        thing.scale = size
            case _Op.DELETE_ENTITIES:
                for fid, flag in ((0, True), (1, False)):
                    deleted.update(dict.fromkeys(p.ids(fid), flag))
                for fid, flag in ((2, True), (3, False)):
                    deleted.update(dict.fromkeys(p.seqs(fid), flag))
            case _Op.INSERT_STRING:
                field_id, prev = p.id(2), p.seq(0)
                for i, ch in enumerate(p.string(1) or ""):
                    node = (site, ts, i)
                    chars[field_id].append((node, prev, ch))
                    prev = node
            case _Op.INSERT_CHAR:
                code = p.scalar(1, "<I", 0xFFFD)
                code = code if code <= 0x10FFFF else 0xFFFD
                chars[p.id(2)].append(((site, ts, 0), p.seq(0), chr(code)))
            case _Op.REMOVE_CHAR:
                if loc := p.seq(0):
                    removed.add(loc)
            case _Op.REMOVE_CHARS:
                removed.update(p.seqs(0))
            case _Op.REVIVE_CHARS:
                removed.difference_update(p.seqs(0))

    node_to_page = {n: pid for pid, n in page_pos.items() if not deleted.get(pid)}
    order = [node_to_page[n] for n in linearize(page_nodes) if n in node_to_page]
    if not order:
        order = [(0, 0, 0)]  # a note with no CreatePage ops has one implicit page
    pages = [_page(i + 1, pid, page_bg.get(pid) or note_bg) for i, pid in enumerate(order)]
    for page in pages:
        page.paper = page.paper or note_bg.get("paper")  # a page's own background may set none

    by_id = {pg.id: pg for pg in pages}
    for iid, ink in inks.items():
        if not deleted.get(iid) and ink.page in by_id:
            by_id[ink.page].inks.append(ink)
    for sid, shape in shapes.items():
        if not deleted.get(sid) and shape.page in by_id:
            by_id[shape.page].shapes.append(shape)

    texts = []
    styled: dict[Id | None, list[Run]] = {}
    for fid, runs in chars.items():
        glyph = {node: ch for node, _, ch in runs}
        seq = linearize([(node, anchor) for node, anchor, _ in runs])
        if text := "".join(glyph[n] for n in seq if n not in removed).strip():
            texts.append(text)
        styled[fid] = _styled_runs(seq, glyph, removed, styling.get(fid, []))
    for bid, block in blocks.items():
        if block.kind == TEXT_BLOCK:
            block.text = styled.get(bid, [])
    block_pages = {}
    for bid, block in blocks.items():
        if not deleted.get(bid) and block.page in by_id:
            by_id[block.page].blocks.append(block)  # type: ignore[index]
            block_pages[bid] = by_id[block.page].number  # type: ignore[index]
    body = styled.get(None, [])
    text = "\n\n".join(texts)
    return Note(note_id, title, pages, text, block_pages, body, font, align_to_lines)


type PathElement = tuple[int, list[tuple[float, float]], float | None]  # (kind, points, width)

_PATH_POINTS = {0: 3, 4: 3, 1: 2, 5: 2, 2: 1, 3: 1, 6: 1, 7: 1}


def decode_path(b: bytes) -> list[PathElement]:
    """Decode an encoded ink path.

    Layout: byte 0 = version << 3 | precision (0 = f16, 1 = f32), u16 element count, then per
    element a u8 kind and its points. Kinds 0/4 cubic, 1/5 quadratic, 2/6 line, 3/7 move-to;
    kinds 0-3 append f16 width, f16 force and two u8 pen angles.
    """
    if len(b) < 3 or b[0] >> 3 or (b[0] & 7) > 1:
        return []
    point = struct.Struct("<ee" if b[0] & 7 == 0 else "<ff")
    out: list[PathElement] = []
    p = 3
    try:
        for _ in range(int.from_bytes(b[1:3], "little")):
            kind = b[p]
            n = _PATH_POINTS[kind]
            pts = [point.unpack_from(b, p + 1 + i * point.size) for i in range(n)]
            p += 1 + n * point.size
            width = None
            if kind <= 3:
                width = struct.unpack_from("<e", b, p)[0]
                p += 6
            out.append((kind, pts, width))
    except (IndexError, KeyError, struct.error):
        pass  # truncated or unknown element: keep what decoded
    return out


def _page(number: int, pid: SeqId, bg: dict) -> Page:
    page = Page(number, pid, *bg.get("size", DEFAULT_PAGE_SIZE), paper=bg.get("paper"))
    if margins := bg.get("margins"):
        page.margins = margins
    if pdf := bg.get("pdf"):
        digest, offset, crop_boxes = pdf
        # a background with fewer PDF pages than the op made pages is a template, repeated:
        # e.g. a note's one-page paper PDF under every page
        i = pid[2] % len(crop_boxes) if crop_boxes else pid[2]
        page.pdf = PdfPage(digest, offset + i)
        if i < len(crop_boxes):
            width, height = crop_boxes[i]
            if "size" in bg and width > 0:
                # ink is placed in the background's space, the PDF page scaled to the
                # background's width (crop boxes are in PDF points): about a quarter of inked
                # PDF pages in a real library drew their ink off the page with the crop box
                page.height = height * page.width / width
            else:
                page.width, page.height = width, height
    return page
