# notability-mcp: mine vs `adriandlam/notability-mcp`

> **History.** Written in the TypeScript repo (`akseldkw07/notability-mcp-ts`) on 2026-11-01, before the port; kept here for the reasoning and the real-data results. "Mine" is the TypeScript server, "upstream" is `adriandlam/notability-mcp`. Current plans are in [../TODO.md](../TODO.md).

Written 2026-10-30.
- **Mine:** `akseldkw07/notability-mcp` at `43ee97d` (TypeScript).
- **Upstream:** `adriandlam/notability-mcp` at `6db4b17e0ee53506375530fa26e62cbacdeefd25` (Python), cloned read-only to
  `~/coding/mcp/notability-mcp-upstream`.

Upstream was read in full before anything ran. Its `login` and `sync` were never run, and
no token was used.

## Other repos looked at

| Repo | What it is | Relevance |
| --- | --- | --- |
| [`Zpankz/notability-cli`](https://github.com/Zpankz/notability-cli) (MIT, Rust; created 2026-10-26, 2 commits) | CLI plus MCP server for the private web API: 131 commands, reads and **writes** (ink, typed text, shapes, imports, transcripts, sharing, Notability AI). 74 commands verified live. | The source of upstream's wire format (`THIRD_PARTY_NOTICES.md`). The reference if write support is ever wanted. |
| [`dbalchunas2-pixel/notability-mcp-server`](https://github.com/dbalchunas2-pixel/notability-mcp-server) (**no license**) | FastMCP over Notability's Google Drive PDF auto-backup: Drive full-text search and PDF text layer only. | A different approach: handwritten pages come back empty. With no license, its code can't be reused. |
| `jvns/svg2notability`, `steventango/notability2pdf`, `GZhonghui/NotabilityViewer`, `HuyNguyenAu/notability-to-svg` and similar | Converters for the old `.note` export format (zipped plists). | Not the format either server reads. |

## Key finding: the local change logs are upstream's format

My repo never decoded the per-note change logs at
`…/collab-production/notes/<UUID>` (TODO.md, "Later" 1). Upstream decodes the same
FlatBuffers op log, as downloaded from the web API (`bundle.decode_bundle`,
`bundle.py:176`).

The local file has a different top-level table: the ops are in field 0, where a web
`NoteBundle` keeps them in field 6. The ops themselves have the same layout. So upstream's
own functions decode the local files as they are:

```python
build_note(note_id, read_ops(local_bytes, ops_field=0))   # bundle.py:164, :216
```

Checked on 400 local notes, printing counts only:
- All 400 decode, giving 8,790 pages and about 246k ink strokes.
- All 1,136 pages with text in the old plist index have ink at the same position in the
  decoded page order.
- Of 7,956 PDF-backed pages, 6,804 have their PDF in local `assets/`; the rest aren't
  downloaded on this Mac.
- 25 sample pages rendered with ink, median 0.08 s each (`render.render_page`).

So upstream's decoding, rendering and OCR can run on the Mac's local data **with no token
and no private API**. This also fixes my two biggest limits: page numbers for 162 newer
notes, and rendering pages other than the first.

## 1. Feature matrix

| Capability | Mine | Upstream | Notes |
| --- | --- | --- | --- |
| Auth | None. Reads the Mac app's files; needs Full Disk Access on the node binary (`src/tools/status.ts:13` checks each path) | `nb-auth-token` cookie in the OS keychain via `keyring` (`cli.py:23`, `cli.py:84-90`), or `NOTABILITY_TOKEN` | Mine has no credential to leak, but Full Disk Access is broad (see §5) |
| Library fetch | Live: APFS clone of the app's SQLite, refreshed when it changes (`src/data/db.ts:75`, `:91`); folders and notes from `organizers` / `note_metadata` (`src/data/catalog.ts:40`, `:69`) | Socket.IO `partial-update` from `wss://notability.com/collab-api` (`api.py:40`), parsed in `sync.parse_library` (`sync.py:19`) | Mine is always current; upstream only as of the last `sync` |
| Note/bundle decoding | Doesn't decode change logs. Reads Notability's **search indexes**: `.fb` decoders (`src/data/fb.ts:172-223`) plus a binary plist parser (`src/data/plist.ts:12`) | Full op-log decoder: pages (RGA order), ink, typed text, PDF backgrounds (`bundle.py:216`) | Upstream's decoder is more complete and works on local files (above) |
| PDF backgrounds | Original PDFs from `assets/<sha512>`; any page rendered with poppler (`src/data/pdf.ts:40`), **without ink** | Downloaded via signed URL (`api.py:84`), stored in the `assets` table, drawn under the ink (`render.py:28`, `pdf.py:23`) | Upstream shows what you wrote on the slide |
| OCR engine(s) | None of its own. Uses **Notability's own** on-device handwriting recognition and image OCR from its indexes (`src/data/text.ts:52`, `:98`) | Apple Vision rev. 2 on macOS, RapidOCR elsewhere, on the ink alone (`ocr.py:21`, `index.py:22`) | Which reads handwriting better is unmeasured (§6). Upstream reports 44% key words / 7% formulas (Vision) on 20 pages |
| Search | Case- and accent-insensitive **substring** count, ranked by matches per note then recency (`src/tools/notes.ts`, `search_notes`); no fuzziness | SQLite FTS5 trigram prefilter (300 candidates), then difflib word-similarity rescoring, threshold 0.6 (`store.py:182-221`) | Upstream tolerates OCR typos ("conjogate"); mine finds exact strings, including in very long PDFs |
| List/filter/pagination | Folder by id, path or title, including subfolders; title query; modified/created dates; favourites; recordings; trash; 4 sorts; limit/cursor (`list_notes`) | Folder path substring, `modified_after`, sort recent/title, limit/cursor; returns every folder and `synced_at` (`server.py:158`) | Mine filters more |
| Read note | JSON: handwriting per page, typed, image OCR, PDF text, recordings summary; `max_chars` / `offset` windows (`get_note`) | Markdown per page: OCR, the page's own PDF text, typed text; low-confidence pages flagged; about 25k-character cap with a "continue at page N" hint (`server.py:205`) | Upstream ties PDF text to the right page; mine can't (no page breaks in Notability's index) |
| Page rendering | First-page thumbnail as the app draws it, everything included (`get_thumbnail`); PDF pages without ink (`get_attachment`) | Any page: ink over the PDF, highlighter multiplied in; no shapes, pasted images or typed text (`render.py:18`) | Complementary: the thumbnail shows everything but only page 1 |
| Write/edit support | None (only `open_in_notability` shows a note) | None; CONTRIBUTING rules writes out | `Zpankz/notability-cli` writes |
| Audio | Notability's own transcripts, with timings, plus why a recording has none (`get_transcript`; `src/data/text.ts:129`, `src/data/assets.ts:85`) | Not supported | Mine only |
| Shared notes | Whatever the Mac app has locally (`note_metadata` has `isShared` / `accessLevel`); not verified | Skipped: only `privateSpace` is parsed (`sync.py:21`) | |
| Incremental sync | Not needed: reads live data, caches text per note by index-file mtimes (`src/data/text.ts:149`) | `stale_notes`: re-indexes notes whose `modified_at` moved; `INDEX_VERSION` forces a full re-index (`store.py:134-148`, `sync.py:12`) | |
| Scheduling | Not needed (Notability's app syncs itself) | None built in; run `sync` with cron, launchd or Task Scheduler | |
| CLI | None (npm scripts) | `serve`, `login`, `logout`, `sync`, `setup` (`cli.py:65`) | |
| Client setup helpers | README snippet, mcp-utils `install-desktop.mjs`, `npm run deploy` with a post-merge hook (`scripts/deploy.mjs`) | `notability-mcp setup` writes Claude Desktop's config atomically and keeps its permissions (`cli.py:42`); one-liners for Claude Code, Cursor, VS Code, Codex | Upstream is easier for strangers; mine fits the mcp-compat setup |
| Config/env vars | `NOTABILITY_LIBRARY_DIR`, `NOTABILITY_MCP_LOG_LEVEL`, `NOTABILITY_MCP_CACHE_DIR`, `NOTABILITY_MCP_MAX_RESPONSE_CHARS`, `NOTABILITY_MCP_OPEN_COMMAND` | `NOTABILITY_MCP_DB`, `NOTABILITY_TOKEN`, `NOTABILITY_CLIENT_VERSION` | |
| Storage location | Nothing of its own besides `~/Library/Caches/notability-mcp` (a database clone that shares blocks, plus resized images) | `index.db` under `~/Library/Application Support/notability-mcp` (Windows and Linux equivalents), holding raw bundles **and every PDF** | Upstream copies the library: about 1 GB of assets and 111 MB of change logs here |
| Platforms | macOS, with the Notability Mac app installed and synced | macOS, Windows, Linux | |

## 2. MCP tool surface

| Purpose | Mine | Upstream | Status |
| --- | --- | --- | --- |
| Full-text search | `search_notes(query, folder?, sources?[title,handwriting,typed,pdf,image,recording], modified_since?, limit≤100=20, context_chars≤1000=150)` → `{notes_matched, total_matches, results:[{note_id,title,folder_path,modified,source,page?,in?,matches,snippet}], more_rows?}` | `notability_search_notes(query 1-200, limit≤50=10, cursor?)` → structured `{hits:[{note_id,title,folder,page,source,snippet«»,score}], next_cursor, notice}` | **Equivalent, conflicting**: name prefix; mine ranks by count, upstream by fuzzy score; mine filters by folder, date and source, upstream pages by cursor |
| List notes | `list_notes(folder?, query?, modified_since?, modified_until?, created_since?, has_recordings?, favorites_only?, include_deleted?, sort[modified,created,last_opened,title], limit≤200=50, cursor?)` → `{total, notes:[{…meta, sources}], next_cursor?}` | `notability_list_notes(folder?, modified_after?, sort[recent,title], limit≤200=50, cursor?)` → `{notes:[{note_id,title,folder,page_count,created_at,modified_at}], total, next_cursor, folders[], synced_at}` | **Equivalent**; field names differ (`id` vs `note_id`, `folder_path` vs `folder`) |
| List folders | `list_folders(parent?, include_counts?)` → `{folders:[{id,title,parent_id,path,emoji?,note_count}]}` | (folder paths are a field of `list_notes`) | Mine only |
| Read note | `get_note(note, pages?, sources?, max_chars?, offset?)` → JSON | `notability_read_note(note_id, start_page=1, end_page?)` → Markdown text (`structured_output=False`) | **Equivalent, conflicting**: JSON vs Markdown; mine takes a title or an id |
| Page image | `get_thumbnail(note, max_width?)` (page 1, app-rendered); `get_attachment(note, hash, page?, as?[image,text,path], max_width?)` (PDF page, no ink) | `notability_get_page_image(note_id, page, max_edge 256-2576=1568)` → PNG | **Overlapping**: upstream's renders any page with ink |
| Transcripts | `get_transcript(note, recording?, since_seconds?, max_chars?)` | — | Mine only |
| Attachments | `list_attachments(note)` → `[{hash,kind,mime,size_bytes,pages?,available}]` | — | Mine only |
| Open in app | `open_in_notability(note)` (`WRITE_LOCAL`) | — | Mine only; goes against upstream's "reads the index and nothing else" scope |
| Health | `status()` (paths, counts, index formats, drift) | — (errors say "run sync") | Mine only |

**Annotations.**
- **Mine:** mcp-utils `READ_LOCAL` on every tool, so all four hints are set; `WRITE_LOCAL` on `open_in_notability`.
- **Upstream:** `READ_ONLY` with the same four hints (`server.py:27`).
- **Untrusted text:** mine wraps every note-derived response in an untrusted-data fence
  (`src/untrusted.ts`, `src/tools/common.ts:24`). Upstream returns OCR, PDF and typed text
  as is.

## 3. Architecture

| | Mine | Upstream |
| --- | --- | --- |
| Data path | **Live local read** of the Mac app's container: database clone plus search-index files, decoded per request and cached per note | **Sync to own index**, then a read-only server: Socket.IO + HTTP → decode → OCR → SQLite FTS5 |
| Sync model | None: Notability's app is the sync engine; changes appear within the 2 s re-check | Manual `sync`; stale notes found by `modified_at` |
| Schema | Notability's own tables (read-only) | `meta`, `folders`, `notes` (with `bundle` BLOB), `assets` (PDF BLOBs), `page_text` FTS5 with trigram tokenizer (`store.py:14-42`) |
| Dependencies | `@modelcontextprotocol/sdk`, `zod`, `mcp-utils`; built-in `node:sqlite`; macOS `sips` / `plutil`; optional poppler | `mcp`, `keyring`, `pillow`, `pypdfium2`, `websockets`; `pyobjc-framework-vision` on macOS; `rapidocr` + `onnxruntime` elsewhere |
| Language | TypeScript 5.9, Node ≥ 22 (`node:sqlite`) | Python ≥ 3.12 (uses PEP 695 generics) |
| Packaging | git checkout + `npm ci` (`prepare` builds `dist/`) | `uv tool install git+…`; PyPI release workflow (trusted publishing) |
| Transport | stdio, behind mcp-compat | stdio |

## 4. Quality

| | Mine | Upstream |
| --- | --- | --- |
| Tests | 31 `node:test` tests (2.6 s) against a synthetic container built at test time: formats, database snapshot (WAL visibility, nothing created in the container), text layer, every tool, drift | 30 pytest tests (37.7 s) on a synthetic bundle and ink ops recorded by notability-cli; tests for sync, store, render, PDF, OCR, CLI and server |
| Run today | **31/31 pass**, `tsc` clean | **30/30 pass**, `ruff check` and `ruff format --check` clean, pyright 0 errors |
| Typing, linting | `strict` tsc | pyright, ruff (E, F, I, UP, B, SIM, RUF), conventional-commit hook |
| CI | GitHub Actions, `macos-latest` only (fixtures need `plutil` and `sips`) | Matrix of macOS / Ubuntu / Windows × Python 3.12 / 3.14; actions pinned by SHA; Dependabot; PyPI release |
| Error handling | Errors name the next step and list ambiguous titles (`src/data/catalog.ts:99-125`); unreadable index files reported per source (`unreadable`); drift errors (`src/data/drift.ts:50`) | `ToolError` messages name the next tool or say to run sync (`server.py:47-61`); one note failing doesn't stop sync (`sync.py:66`) |
| Result size | `fitResponse` at 25k characters plus per-tool windows (`src/tools/common.ts:10`) | `read_note` about 25k characters with a continuation hint; search ≤ 50, list ≤ 200 |
| Docs | README, HANDOFF, TODO (internal and detailed) | README with every client's setup, CONTRIBUTING, SECURITY, THIRD_PARTY_NOTICES, issue template |
| Activity | Mine | 28 commits on 2026-10-27 and 28, one author, 0 stars, forks or issues |

## 5. Security

| | Mine | Upstream |
| --- | --- | --- |
| Token storage | No token | OS keychain (`keyring`), or `NOTABILITY_TOKEN`; the input prompt hides it (`cli.py:86`) |
| Hosts the token goes to | — | `notability.com` only: as cookie, header and Socket.IO auth payload (`api.py:42-68`). **Not** sent to the storage host: signed asset URLs are fetched without it (`api.py:95`) |
| Input validation | Note ids are UUID-checked (`uuidBlob`); attachments are served only if listed for that note (`src/data/assets.ts:69`); folder and page specs are parsed strictly | `uuid.UUID` and a SHA-512 regex check before ids go in a URL (`api.py:73`, `:87`), tested in `test_sync.py:71` |
| Read-only guarantees | Never opens the app's database in place (it clones it); a test checks the container is untouched. Its own cache only in `~/Library/Caches` | The server opens its own index `mode=ro` and makes no network calls (`server.py:39`); `sync` writes only its own index |
| Leak risks | **Full Disk Access on the node binary** lets every node script on the machine read protected data, not just this server; worth a dedicated node binary | The index is an unencrypted copy of all notes and PDFs in `Application Support`. The token gives full read/write access to the account, and the client headers mimic the web app (`api.py:21-26`) |
| Prompt injection | Every response is fenced as untrusted, with hidden characters escaped | None: PDF text, which third parties wrote, goes to the model raw |

## 6. Correctness on real data

**Run 2026-11-01, local only, with your approval.** The old TS server ran against the app's data,
and the fork (`akseldkw07/notability-mcp`, branch `feat/local-source`) ran against a
`sync --source local` index of the same library: 770 notes, 17,845 pages. Only aggregate numbers
were recorded, no note content. The web token was never used.

**Sync.** The first full sync took 96 s and indexed all 770 notes, with 0 failures. A sync with
nothing changed takes 0.4 s. The index is 930 MB, mostly copied PDFs.

**Searches** (top 10 from each server):

| Query | TS hits | Fork hits | Top-5 overlap | Fork hits with a page number |
| --- | --- | --- | --- | --- |
| "harmonic conjugate" | 0 | 10 (all loose) | 0 | 10 |
| "eigenvalue" | 10 | 10 | 2, same top hit | 10 |
| "gradient descent" | 10 | 10 | 1 | 10 |
| a title word from the newest note | 2 | 10 | 1, same top hit | 7 |
| "convolusion" (misspelt) | 0 | 10 (score 0.91) | 0 | 10 |

- **Typos:** the fork finds the misspelt term; the TS server's substring search finds nothing.
- **Phrases that aren't there:** "harmonic conjugate" appears verbatim in no note. The fork
  still returns 10 hits scoring 0.80 to 0.84, just over its 0.8 "close" threshold, so no
  loose-match warning is shown. This is upstream behaviour; the threshold is worth raising, or
  the warning should also require one exact word.
- **PDF text crowds out handwriting** in mixed results. Ties at score 1.0 fall back to FTS
  order, which favours long PDF pages. With `sources: ["handwriting"]` the fork returns 6 to 10
  close handwriting hits for the same queries. A tie-break that prefers handwriting would help.
- **Page numbers:** the fork gives a page on nearly every hit. The TS server gave one on 1 hit
  of 22.

**Reads:**

| Note | TS | Fork |
| --- | --- | --- |
| Recent, handwriting only | 2 pages, numbered among pages with text | 2 pages, real page numbers |
| PDF-heavy lecture | 5 handwriting pages, PDF text as 1 block | 11 pages with text, PDF text per page; truncated at 25k characters with a continuation |
| With a recording | 3 pages, transcript via `get_transcript` | 3 pages, plus the transcript via `notability_get_transcript` |

**Page images.** On page 3 of a handwritten note and an annotated PDF page, the fork renders
both with ink over the PDF. The TS server can only show the first page, as a thumbnail.

**A bug found here, in upstream's renderer.** 427 of 1,704 inked PDF pages, across 78 notes,
drew some or all of their ink off the page, and Vision OCR missed that ink. Pages were sized by
the PDF's crop box in points, but ink is placed in the page background's space: the PDF page
scaled to the background's width. Fixed in the fork (commit 97f0ba4); 25 pages still have ink
past the edge. This is the strongest upstream PR candidate.

**Notability's handwriting text vs Vision** (40 random pages, after the fix):
- The two agree on 36% of words on average, so they really differ.
- Share of output tokens that are dictionary words: Notability 0.56, Vision 0.52. Notability
  reads better on 18 pages, Vision on 8, with 14 ties. Both garble math and drawings.
- Notability's text is free; Vision takes 0.39 s per page.
- So the fork's default, `--ocr auto`, uses Notability's text and runs Vision only on the
  62 inked pages Notability found no text on.

## 7. Where each one is better

- **Upstream is better at:**
  - rendering any page with ink over its PDF;
  - fuzzy search that survives OCR typos;
  - PDF text tied to the right page;
  - cross-platform support and packaging;
  - setup for every client;
  - CI breadth and OSS hygiene.
  - Its bundle decoder is the most valuable code across both repos.
- **Mine is better at:**
  - zero credentials and no private API;
  - always-current data, with no sync step;
  - Notability's own recognition (handwriting text, **image OCR** and **audio
    transcripts**; upstream has no image OCR and no audio);
  - richer filters, folders, attachments and thumbnails;
  - untrusted-data fencing;
  - drift detection;
  - mcp-compat and deploy integration.
- **Redundant:** both have list, search and read tools with the same purpose and different
  shapes. My `.fb` search-index decoders are mostly made redundant by decoding the change
  logs, **except** for image OCR and transcripts, which only the indexes have.
- **Mine is weaker at:** page numbers for 162 newer notes; no ink rendering; no page breaks
  in PDF text. Upstream's decoder running on local files fixes all three.
