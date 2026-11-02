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

- **Shapes and typed text** aren't drawn on page images or exports (ink, PDF pages and pasted
  images are). Typed text needs the text runs' positions and styles from the change log; shapes
  are `CreateShape` / `ModifyShape` ops.
- **25 inked PDF pages** on a real library still have ink past the page edge after the
  page-size fix: look at one.

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

## Later

- **Notability's study quizzes:** `quiz_session` and `quiz_sessions/*.plist`.
- **Calendar links:** `calendarNoteLinks` and `syllabusCourses`.
