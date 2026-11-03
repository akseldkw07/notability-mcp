"""Synthetic copies of the Mac app's search-index files (NotesSearchIndex/), in both formats:
binary plists (until about April 2026) and FlatBuffers (since). Layouts are in appindex.py."""

import plistlib
from pathlib import Path

import flatbuffers

from notability_mcp.bundle import SeqId


def _envelope(b: flatbuffers.Builder, versions: tuple[int, int], payload: int) -> bytes:
    b.StartObject(4)
    b.PrependUint8Slot(0, versions[0], 0)
    b.PrependUint8Slot(1, versions[1], 0)
    b.PrependUOffsetTRelativeSlot(2, payload, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def _vector(b: flatbuffers.Builder, offsets: list[int]) -> int:
    b.StartVector(4, len(offsets), 4)
    for off in reversed(offsets):
        b.PrependUOffsetTRelative(off)
    return b.EndVector()


def _seq_id(b: flatbuffers.Builder, seq: SeqId) -> int:
    site, ts, index = seq
    b.Prep(4, 12)
    b.PrependUint32(index)
    b.PrependUint32(ts)
    b.Pad(2)
    b.PrependUint16(site)
    return b.Offset()


def handwriting_fb(pages: list[tuple[SeqId, str]], versions=(4, 5)) -> bytes:
    """Pages with recognized text: {0: [page {0: page id, 1: text, 3: sha256}]}."""
    b = flatbuffers.Builder(256)
    tables = []
    for seq, text in pages:
        s, digest = b.CreateString(text), b.CreateByteVector(bytes(32))
        b.StartObject(4)
        b.PrependStructSlot(0, _seq_id(b, seq), 0)
        b.PrependUOffsetTRelativeSlot(1, s, 0)
        b.PrependUOffsetTRelativeSlot(3, digest, 0)
        tables.append(b.EndObject())
    vec = _vector(b, tables)
    b.StartObject(2)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    return _envelope(b, versions, b.EndObject())


def image_fb(texts: list[str], versions=(1, 2)) -> bytes:
    """{0: [image {0: id (2 x u32), 1: text}]}."""
    b = flatbuffers.Builder(256)
    tables = []
    for i, text in enumerate(texts):
        s = b.CreateString(text)
        b.StartObject(4)
        b.Prep(4, 8)
        b.PrependUint32(i)
        b.PrependUint32(1)
        b.PrependStructSlot(0, b.Offset(), 0)
        b.PrependUOffsetTRelativeSlot(1, s, 0)
        tables.append(b.EndObject())
    vec = _vector(b, tables)
    b.StartObject(2)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    return _envelope(b, versions, b.EndObject())


def recording_fb(
    jobs: list[tuple[str, str, list[tuple[float, float, str]]]], versions=(2, 3)
) -> bytes:
    """{0: titles, 1: [job {0: sha512 (64 bytes), 1: language, 2: [segment {0: text, 1: start,
    2: end}]}]}."""
    b = flatbuffers.Builder(256)
    job_tables = []
    for sha512, language, segments in jobs:
        segs = []
        for start, end, text in segments:
            s = b.CreateString(f" {text} ")  # the app's segment text has stray spaces
            b.StartObject(3)
            b.PrependUOffsetTRelativeSlot(0, s, 0)
            b.PrependFloat64Slot(1, start, 0)
            b.PrependFloat64Slot(2, end, 0)
            segs.append(b.EndObject())
        seg_vec, lang = _vector(b, segs), b.CreateString(language)
        b.StartObject(3)
        b.Prep(1, 64)
        for byte in reversed(bytes.fromhex(sha512)):
            b.PrependUint8(byte)
        b.PrependStructSlot(0, b.Offset(), 0)
        b.PrependUOffsetTRelativeSlot(1, lang, 0)
        b.PrependUOffsetTRelativeSlot(2, seg_vec, 0)
        job_tables.append(b.EndObject())
    vec, titles = _vector(b, job_tables), b.CreateString("Recording 1")
    b.StartObject(3)
    b.PrependUOffsetTRelativeSlot(0, titles, 0)
    b.PrependUOffsetTRelativeSlot(1, vec, 0)
    return _envelope(b, versions, b.EndObject())


def handwriting_plist(pages: dict[int, str]) -> bytes:
    """The old format: {pages: {"<0-based page>": {text}}}."""
    return plistlib.dumps(
        {"pages": {str(k): {"text": v} for k, v in pages.items()}}, fmt=plistlib.FMT_BINARY
    )


def write(library: Path, indexer: str, note_id: str, name: str, data: bytes) -> Path:
    path = library / "NotesSearchIndex" / indexer / note_id.upper() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _id(b: flatbuffers.Builder, site: int, ts: int) -> int:
    b.Prep(4, 8)
    b.PrependUint32(ts)
    b.Pad(2)
    b.PrependUint16(site)
    return b.Offset()


def change_log(ops: list[tuple[tuple[int, int], int, dict[int, SeqId]]]) -> bytes:
    """A change log (ops in root field 0) of ops whose payloads hold only SeqId fields:
    [((site, ts), payload tag, {field id: SeqId})]. Enough for CreatePage (tag 3, field 0: the
    page it follows) and CreateBlock (tag 22, field 2: its page)."""
    b = flatbuffers.Builder(256)
    tables = []
    for (site, ts), tag, fields in ops:
        b.StartObject(max(fields, default=0) + 1)
        for fid, seq in fields.items():
            b.PrependStructSlot(fid, _seq_id(b, seq), 0)
        payload = b.EndObject()
        b.StartObject(6)
        b.PrependStructSlot(0, _id(b, site, ts), 0)
        b.PrependUint8Slot(4, tag, 0)
        b.PrependUOffsetTRelativeSlot(5, payload, 0)
        tables.append(b.EndObject())
    vec = _vector(b, tables)
    b.StartObject(1)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def image_fb_with_ids(images: list[tuple[tuple[int, int], str]], versions=(1, 2)) -> bytes:
    """Like image_fb, with each image's id (the CreateBlock op's id)."""
    b = flatbuffers.Builder(256)
    tables = []
    for (site, ts), text in images:
        s = b.CreateString(text)
        b.StartObject(4)
        b.PrependStructSlot(0, _id(b, site, ts), 0)
        b.PrependUOffsetTRelativeSlot(1, s, 0)
        tables.append(b.EndObject())
    vec = _vector(b, tables)
    b.StartObject(2)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    return _envelope(b, versions, b.EndObject())


def change_log_with_image(
    sha512: str, origin: tuple[float, float], size: tuple[float, float]
) -> bytes:
    """A change log with one CreateBlock op: an image on the note's implicit first page.
    CreateBlock: {0: kind, 2: page, 3: origin, 6: size, 10: ImageAsset {0: AssetMetadata
    {0: SHA-512}}}."""
    b = flatbuffers.Builder(256)
    b.StartObject(1)
    b.Prep(1, 64)
    for byte in reversed(bytes.fromhex(sha512)):
        b.PrependUint8(byte)
    b.PrependStructSlot(0, b.Offset(), 0)
    meta = b.EndObject()
    b.StartObject(2)
    b.PrependUOffsetTRelativeSlot(0, meta, 0)
    asset = b.EndObject()
    b.StartObject(11)
    b.PrependInt8Slot(0, 1, 0)
    b.PrependStructSlot(2, _seq_id(b, (0, 0, 0)), 0)
    for fid, (x, y) in ((3, origin), (6, size)):
        b.Prep(4, 8)
        b.PrependFloat32(y)
        b.PrependFloat32(x)
        b.PrependStructSlot(fid, b.Offset(), 0)
    b.PrependUOffsetTRelativeSlot(10, asset, 0)
    payload = b.EndObject()
    b.StartObject(6)
    b.PrependStructSlot(0, _id(b, 1, 5), 0)
    b.PrependUint8Slot(4, 22, 0)
    b.PrependUOffsetTRelativeSlot(5, payload, 0)
    op = b.EndObject()
    vec = _vector(b, [op])
    b.StartObject(1)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def _point(b: flatbuffers.Builder, x: float, y: float) -> int:
    b.Prep(4, 8)
    b.PrependFloat32(y)
    b.PrependFloat32(x)
    return b.Offset()


def _color(b: flatbuffers.Builder, rgba: tuple[int, int, int, int]) -> int:
    b.Prep(1, 4)
    for c in reversed(rgba):
        b.PrependUint8(c)
    return b.Offset()


def _op(b: flatbuffers.Builder, oid: tuple[int, int], tag: int, payload: int) -> int:
    b.StartObject(6)
    b.PrependStructSlot(0, _id(b, *oid), 0)
    b.PrependUint8Slot(4, tag, 0)
    b.PrependUOffsetTRelativeSlot(5, payload, 0)
    return b.EndObject()


def _log(b: flatbuffers.Builder, ops: list[int]) -> bytes:
    vec = _vector(b, ops)
    b.StartObject(1)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def change_log_with_shapes() -> bytes:
    """A change log with shapes on the note's implicit first page:
    - (1, 5) a black line from (100, 100) to (300, 100), 2 pt wide
    - (1, 6) a red 100 x 50 ellipse created at (0, 0), then moved to (200, 300) by ModifyPositions
    - (1, 8) a filled 40% yellow highlighter rectangle (a Polygon) at (100, 500), 200 x 50
    - (1, 9) a line that is then deleted
    CreateShape: {0: page, 1: origin, 4: definition type (1 Line, 2 Polygon, 3 NormalShape),
    5: definition, 6: tool, 9: color, 10: borderWidth, 11: fillColor}."""
    b = flatbuffers.Builder(512)

    def shape(oid, origin, kind, definition, color, width=2.0, tool=0, fill=None):
        b.StartObject(12)
        b.PrependStructSlot(0, _seq_id(b, (0, 0, 0)), 0)
        b.PrependStructSlot(1, _point(b, *origin), 0)
        b.PrependUint8Slot(4, kind, 0)
        b.PrependUOffsetTRelativeSlot(5, definition, 0)
        b.PrependInt8Slot(6, tool, 0)
        b.PrependStructSlot(9, _color(b, color), 0)
        b.PrependFloat32Slot(10, width, 4.0)
        if fill:
            b.PrependStructSlot(11, _color(b, fill), 0)
        return _op(b, oid, 18, b.EndObject())

    def line(end):
        b.StartObject(5)
        b.PrependStructSlot(0, _point(b, 0, 0), 0)
        b.PrependStructSlot(3, _point(b, *end), 0)
        return b.EndObject()

    ops = [shape((1, 5), (100, 100), 1, line((200, 0)), (0, 0, 0, 255))]
    b.StartObject(2)
    b.PrependStructSlot(1, _point(b, 100, 50), 0)
    ops.append(shape((1, 6), (0, 0), 3, b.EndObject(), (255, 0, 0, 255)))
    b.StartObject(3)
    b.PrependStructSlot(0, _id(b, 1, 6), 0)
    b.PrependStructSlot(2, _point(b, 200, 300), 0)
    move = b.EndObject()
    vec = _vector(b, [move])
    b.StartObject(1)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    ops.append(_op(b, (1, 7), 24, b.EndObject()))
    points = [(0, 0), (200, 0), (200, 50), (0, 50)]
    b.StartVector(8, len(points), 4)
    for x, y in reversed(points):
        _point(b, x, y)
    pts = b.EndVector()
    b.StartObject(1)
    b.PrependUOffsetTRelativeSlot(0, pts, 0)
    ops.append(
        shape(
            (1, 8),
            (100, 500),
            2,
            b.EndObject(),
            (255, 255, 0, 102),
            tool=2,
            fill=(255, 255, 0, 102),
        )
    )
    ops.append(shape((1, 9), (100, 700), 1, line((200, 0)), (0, 0, 0, 255)))
    b.StartVector(8, 1, 4)
    _id(b, 1, 9)
    dead = b.EndVector()
    b.StartObject(4)
    b.PrependUOffsetTRelativeSlot(0, dead, 0)
    ops.append(_op(b, (1, 10), 25, b.EndObject()))
    return _log(b, ops)


def _boundary(b: flatbuffers.Builder, seq: SeqId, kind: int) -> int:
    site, ts, index = seq
    b.Prep(4, 16)
    b.Pad(3)
    b.PrependInt8(kind)
    b.PrependUint32(index)
    b.PrependUint32(ts)
    b.Pad(2)
    b.PrependUint16(site)
    return b.Offset()


def change_log_with_text() -> bytes:
    """A change log with typed text:
    - SetMetadata (1, 1): default font Inter 12 pt, text aligned to lines
    - body text (1, 2): "Hello world\\nsecond", with "world" made bold (1, 3) and the second
      paragraph indented two levels (1, 4)
    - a text box (1, 5) at (100, 400), 200 x 50, holding "boxed" (1, 6)
    SetMetadata {3: alignTextToLines, 4: defaultFontFamily, 5: defaultFontSize};
    InsertString {0: location, 1: string, 2: textField}; ModifyStyle {0: start, 1: end
    (Boundary: SeqId + type, 0 before / 1 after), 2: textField, 3: bold {0: value}};
    ModifyParagraphStyle {0: start, 1: end, 2: indentLevel {0: value}, 7: textField}."""
    b = flatbuffers.Builder(512)
    ops = []
    family = b.CreateString("Inter")
    b.StartObject(6)
    b.PrependBoolSlot(3, True, False)
    b.PrependUOffsetTRelativeSlot(4, family, 0)
    b.PrependFloat32Slot(5, 12.0, 0.0)
    ops.append(_op(b, (1, 1), 1, b.EndObject()))
    text = b.CreateString("Hello world\nsecond")
    b.StartObject(3)
    b.PrependUOffsetTRelativeSlot(1, text, 0)
    ops.append(_op(b, (1, 2), 8, b.EndObject()))
    b.StartObject(1)
    b.PrependBoolSlot(0, True, False)
    bold = b.EndObject()
    b.StartObject(4)
    b.PrependStructSlot(0, _boundary(b, (1, 2, 6), 0), 0)  # before "w"
    b.PrependStructSlot(1, _boundary(b, (1, 2, 10), 1), 0)  # after "d"
    b.PrependUOffsetTRelativeSlot(3, bold, 0)
    ops.append(_op(b, (1, 3), 12, b.EndObject()))
    b.StartObject(1)
    b.PrependUint8Slot(0, 2, 0)
    indent = b.EndObject()
    b.StartObject(8)
    b.PrependStructSlot(0, _seq_id(b, (1, 2, 12)), 0)  # "s"
    b.PrependStructSlot(1, _seq_id(b, (1, 2, 17)), 0)  # "d"
    b.PrependUOffsetTRelativeSlot(2, indent, 0)
    ops.append(_op(b, (1, 4), 13, b.EndObject()))
    b.StartObject(7)
    b.PrependInt8Slot(0, 0, 0)  # a text block
    b.PrependStructSlot(2, _seq_id(b, (0, 0, 0)), 0)
    b.PrependStructSlot(3, _point(b, 100, 400), 0)
    b.PrependStructSlot(6, _point(b, 200, 50), 0)
    ops.append(_op(b, (1, 5), 22, b.EndObject()))
    boxed = b.CreateString("boxed")
    b.StartObject(3)
    b.PrependUOffsetTRelativeSlot(1, boxed, 0)
    b.PrependStructSlot(2, _id(b, 1, 5), 0)
    ops.append(_op(b, (1, 6), 8, b.EndObject()))
    return _log(b, ops)
