# TODO

What's planned for this fork, after the port from the TypeScript server (see
[history/](history/) for how the fork came about). Everything stays read-only with respect to
Notability: tools may write files *outside* the app's container, never inside it.

## Tools

1. **`notability_export_note_text`.** Write a note's recognized handwriting, image text and
   transcripts as Markdown next to an exported PDF, so exports are searchable and diffable in
   git (a PDF export's handwriting is images). Same path rules as `notability_export_note_pdf`.
2. **`notability_list_stale_exports`.** Given a folder, pair each exported PDF with its note
   (via a `.notability.json` sidecar that the export tools would write: note id, export time)
   and list notes modified since their export.
3. **`notability_get_annotated_pages`.** For a note built on an imported PDF: which background
   pages carry ink, highlighter or pasted images, and in which colours. Joined with a page map
   of the document (e.g. which question is on which page), it answers "which questions have I
   worked or starred" without OCR.
4. **`notability_check_update_pages`.** For documents updated incrementally (only revised pages
   are re-imported and inserted next to the pages they replace): check, from the note's page
   order and the PDF assets of each page, that every revised page sits next to the page it
   replaces and the replaced page is gone. Needs item 3's page data.
5. **`notability_import_pdf`** (writes, through the app). Notability declares itself an Editor
   for `com.adobe.pdf`, so `open -a Notability <file>` hands a PDF to the app's own import, which
   creates a new note; nothing is written into the container by this server. Untested: check
   whether the app asks for a folder, and return the new note's id by polling for the PDF's
   hash (`notability_find_notes_for_pdf`). Opt-in like `notability_open_note`, since each call
   adds a note.
6. **Google Drive.** Probably no tool: export to a local folder, then upload with a Google Drive
   connector, or export straight into a Google Drive for desktop folder
   (`~/Library/CloudStorage/GoogleDrive-<account>/My Drive/`). Sharing would need the Drive API
   (OAuth, `drive.file` scope) and is outward-facing: only with recipients the user confirms,
   never taken from note text.

Merging pages into an existing note isn't possible: there's no API, and writing the app's change
logs while it runs and syncs risks corrupting notes.

## Rendering

- **Large exports.** `export_note_pdf` renders every page as a 150 dpi image, even PDF pages
  nobody wrote on: a 1,416-page imported textbook (ink on 16 pages) took about 200 s and made
  a 451 MB PDF. Copy unannotated PDF pages straight from the source PDF (fast, small, text
  stays selectable) and render only the pages with ink, shapes, text or images; or at least
  warn before a very large export. Until then, pass `pages` to export a range.
- **Exports from the app's own "Save as…" without screen control.** The app's PDF is exact;
  ours is rendered (now checked against it, see Done). As of Notability on 2026-11-03 nothing
  triggers its export without driving the window: its App Intents only select notes or
  folders, and `notability://` only opens them. Watch for an export intent or Shortcuts action.
  `scripts/save_as.py` drives the menu, but only as a dev tool for parity references.
- **Typed text nobody uses yet.** No note in this library uses checkboxes, block quotes, code,
  highlight colour, links, superscript / subscript, or centred / right alignment (checked
  2026-11-03), so they're decoded but drawn plainly or not at all. Add one when a note uses it,
  with a reference (`scripts/save_as.py --note <id>`).
- **Rules that rest on one or two notes** (marked "measured" in `text.py`): which line near the
  page bottom lined-paper text may use (6.6 pt above it: no; 17 or 0: yes); the gap between
  an image and the text beside it (the page margin); Inter's line height (two sizes). Re-check
  when a new reference fails. One note's File > Export > PDF is greyed out in the app, so it
  can't be a reference.
- **26 inked PDF pages** still have ink with nowhere to show: past the left or right edge, or
  past the top of the first page or the bottom of the last. Look at one (strokes past a page's
  top or bottom now show on the page next to it, as in the app: 212 strokes).

## Search

- **Loose phrase matches:** a phrase that appears nowhere can still return hits scoring just
  over the 0.8 "close" threshold, with no warning. Raise the threshold, or require at least one
  exact word for "close".

## Upstream (only with the owner's OK)

Candidates for PRs to `adriandlam/notability-mcp`; its CONTRIBUTING asks for an issue first for
anything bigger than a fix:

1. `fix(bundle): size PDF pages in the background's space`: a quarter of inked PDF pages drew
   ink off the page, and OCR missed it.
2. `feat(bundle): decode the Mac app's change logs` (`decode_local`).
3. `feat(sync): local source on macOS, no token needed`.
4. `feat: fence note text as untrusted in tool results`.
5. `feat: transcripts and image OCR from the app's own indexes`.
6. `feat(search): folder, source and date filters; whole-word snippets`.
7. `feat(render): shapes, paper and typed text`, with the parity check that measured them.

## Done

- **Exports match the app's** (#5, #6, 2026-11-03), checked by `tests/test_parity.py` against
  the app's own PDFs of 17 notes picked to cover what we render (all pass):
  - shapes (lines, curves, polygons, ellipses, dashes, fills); paper colour and grid, lines or
    dots, also under PDF pages; template PDF backgrounds repeating on every page;
  - typed text: body text and text boxes, fonts and styles, line height per font, page breaks,
    lined-paper snapping, indent, tabs, wrapping around pasted images, fallback glyphs;
  - pages at the note's own size; only a last empty page is dropped;
  - ink past a page's edge continues on the next page; pencil see-through; tape left out.
- **Lists, line spacing, text boxes** (#8, 2026-11-03; 22 references, all pass): bullets and
  numbered items with hanging indents, paragraph line spacing, text boxes laid out like body
  text; squared paper's rows start at the page's top; paragraph styles start after the
  newline they're anchored on; strikethrough read from the right field.

## Later

- **Notability's study quizzes:** `quiz_session` and `quiz_sessions/*.plist`.
- **Calendar links:** `calendarNoteLinks` and `syllabusCourses`.
