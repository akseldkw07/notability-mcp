# Merge proposal: notability-mcp → a fork of `adriandlam/notability-mcp`

> **History.** Written in the TypeScript repo (`akseldkw07/notability-mcp-ts`) on 2026-11-01, before the port; kept here for the reasoning and the real-data results. "Mine" is the TypeScript server, "upstream" is `adriandlam/notability-mcp`. Current plans are in [../TODO.md](../TODO.md).

Written 2026-10-30, from [the comparison](notability-mcp-comparison.md). Your additions to
the brief set the destination: deprecate this repo, and finish with a fork of upstream.
This proposal agrees with that destination and adds the order of work that makes it safe.

**Decided 2026-11-01:**
- **Fork name:** rename this repo to `akseldkw07/notability-mcp-ts`, then fork upstream
  as `akseldkw07/notability-mcp` (open question 1).
- **Deprecation:** a README banner now; archive at the switch (step 9).
- **Real data:** a local sync on real data and the comparison are approved, local only.
  Report counts and pass/fail, never note content. Never use the web token (questions 3
  and 4).
- **Tooling:** keep upstream's uv, with a self-contained `untrusted.py` (question 2).
- **PDF round-trip tools** from `TODO.md` belong in the fork, after the core port:
  `export_note_pdf`, `find_notes_for_pdf`, `export_note_text`, `list_stale_exports`,
  `get_annotated_pages`, `check_quiz_update` and `import_pdf`.

## 1. Strategy: (b) fork upstream and port my features in, upstreaming what fits

**Recommendation: (b), with each ported feature written so it could also go upstream as a
PR (a).**

- **Upstream's core is the better foundation.** Its bundle decoder (`bundle.py`), ink
  renderer (`render.py`), page-aware PDF text and fuzzy FTS search are the parts mine lacks
  and would take longest to rebuild. They're small (about 1,150 lines), typed, linted and
  tested on three OSes.
- **My features fit its design.** Upstream is "sync into a local index; the server reads
  only the index" (CONTRIBUTING, "Scope"). My unique value is mostly a different **source**
  for that same index:
  - The Mac app's container: its database, the change logs and `assets/`, which
    upstream's own decoder already reads with `read_ops(buf, 0)` (comparison, "Key finding").
  - Notability's own image OCR and audio transcripts, which become new `source` values in
    `page_text`.

  None of that touches the server's read-only, no-network rule.
- **The local source cuts the riskiest dependency.** Upstream's token path depends on a
  private API that pins a web-client version (`api.py:21-26`) and can break any day. On a
  Mac with the app installed, the local source needs no token and no network; app updates
  are caught by my drift guard. The token path stays for non-Mac machines.
- **Why not (c)** (keep mine, port upstream's parts into TypeScript): it means rewriting the
  decoder, renderer and OCR in Node. That's more code to own, and it loses upstream's
  future fixes to the wire format.
- **Why not (d)** (keep both): two servers over the same notes, with overlapping tools that
  conflict in naming and output.
- **Why not pure (a)** (only contribute PRs): upstream is two days old with one maintainer
  and no activity history. A fork lets you ship now and still offer PRs; if upstream goes
  quiet, the fork is self-sufficient.
- **Maintenance burden:** one Python codebase. Rebasing on upstream stays cheap as long as
  fork-only code lives in new modules and touches upstream files minimally (layout below).

## 2. Target architecture

Fork at `akseldkw07/<name>` (name: see open question 1), checked out at
`~/coding/mcp/<name>`, with `upstream` added as a git remote.

```
src/notability_mcp/
  api.py        upstream   (token path, unchanged)
  bundle.py     upstream   (+ decode_local(): read_ops(buf, 0) for the Mac app's files; small, upstreamable)
  render.py     upstream
  pdf.py        upstream
  ocr.py        upstream
  index.py      upstream   (+ extra PageText sources: "image", "recording", "notability")
  store.py      upstream   (+ get_meta keys for source/drift; Source literal widened)
  sync.py       upstream   (+ source dispatch: --source web|local)
  local.py      NEW, from mine: container paths, database clone (cp -c), library from
                organizers/note_metadata, change logs, assets by sha512, Notability's
                .fb/plist indexes (ports of src/data/{db,catalog,fb,text}.ts; plistlib
                replaces my plist parser)
  drift.py      NEW, from mine: schema and index-version checks for the local source (src/data/drift.ts)
  untrusted.py  NEW, from mine: fence for tool output (Python twin of mcp-utils createFence)
  server.py     upstream   (+ new tools below, fencing, folder/date/source filters)
  cli.py        upstream   (+ `sync --source local`, `status`)
```

**Final tool list.** Upstream's names are kept, so the fork stays upstream-compatible.

| Tool | Schema | From |
| --- | --- | --- |
| `notability_search_notes` | `query`, `limit≤50`, `cursor?` **+ `folder?`, `sources?`, `modified_after?`** | upstream, plus my filters |
| `notability_list_notes` | upstream's params **+ `include_subfolders` folder semantics, `favorites_only?`, `has_recordings?`** | upstream, plus my filters |
| `notability_read_note` | `note_id`, `start_page`, `end_page?` (Markdown) **+ image OCR and transcript summary sections** | upstream |
| `notability_get_page_image` | `note_id`, `page`, `max_edge` | upstream |
| `notability_get_transcript` | `note_id`, `recording?`, `since_seconds?` → timed segments, or why there's no transcript | mine |
| `notability_list_attachments` | `note_id` → `[{sha512, kind, mime, size, pages?, available}]` | mine |
| `notability_get_attachment` | `note_id`, `sha512`, `page?`, `as: image\|text\|path` | mine (pypdfium2 replaces poppler) |
| `notability_status` | → index path, last sync, source, counts, drift warnings, permission check | mine |
| `notability_open_note` | `note_id` → opens `notability://note/<UUID>`; `WRITE_LOCAL`; **fork-only, off unless `NOTABILITY_MCP_ALLOW_OPEN=1`** | mine |

Dropped: my `get_thumbnail`, because `get_page_image` covers every page, and my
`list_folders`, because `list_notes` already returns every folder path.

**Breaking changes for your current MCP clients (Claude Desktop):**
- **Every tool is renamed:** `search_notes` → `notability_search_notes`, and so on. Chats
  opened before the switch lose their old tools; mcp-compat can't map renames.
- **`note` (id or exact title) becomes `note_id`.** Title lookup could be kept as a fork
  extra if you want it.
- **`get_note`'s JSON becomes `read_note`'s Markdown,** and `page` numbering becomes real
  page numbers everywhere.
- **The Desktop config entry changes command:** `uv run` / `uv tool` instead of node,
  still behind mcp-compat. The Full Disk Access grant moves to the Python interpreter that
  runs `sync --source local`.

## 3. Feature-by-feature decisions

| Feature | Decision | Why |
| --- | --- | --- |
| Auth | **Combine** | Local source on Macs needs no token; keep upstream's keychain token for the web source |
| Library fetch | **Combine** | `local.py` reads the database clone; `api.py` stays for web |
| Bundle decoding | **Take upstream** | More complete; reads local change logs via `read_ops(…, 0)` |
| `.fb` / plist search indexes | **Keep mine, narrowed** | Only image OCR and transcripts; handwriting comes from decoding |
| PDF backgrounds and text | **Take upstream** | Page-aware text and drawn backgrounds; local `assets/` replaces downloads |
| OCR | **Combine** | Vision/RapidOCR as upstream does, plus Notability's own handwriting text as a second `source` (keep whichever wins §6, or both) |
| Search | **Take upstream, add my filters** | Fuzzy beats substring on OCR; add `folder`, `sources`, `modified_after` |
| List/filter | **Take upstream, add my filters** | Same shape, more filters |
| Read note | **Take upstream** | Per-page Markdown with PDF text in place; add image OCR and recording sections |
| Page rendering | **Take upstream** | Any page; my thumbnail tool is dropped |
| Write/edit | **Drop** | Out of scope for both; `Zpankz/notability-cli` if ever needed |
| Audio transcripts | **Keep mine** | Upstream has none; source = Notability's `RecordingIndex` |
| Shared notes | **Keep upstream's limit for web; local includes what the app has** | Verify with the local source |
| Incremental sync | **Take upstream** | `stale_notes` + `INDEX_VERSION`; the local source sets `modified_at` from `note_metadata` |
| Scheduling | **Add** (fork) | A launchd agent running `sync --source local` every 15 min; cheap, no network |
| CLI | **Take upstream, extend** | `sync --source local`, `status` |
| Client setup | **Take upstream, add mcp-compat** | Keep `setup`; register through mcp-compat for your Desktop; post-merge deploy |
| Untrusted fencing | **Keep mine** | Upstream returns third-party PDF text raw |
| Drift guard | **Keep mine** | For the local source's undocumented formats |
| Attachments, open-in-app | **Keep mine** (open: opt-in, fork-only) | Not in upstream's scope |
| Config / storage | **Take upstream** (`NOTABILITY_MCP_DB`) + `NOTABILITY_LIBRARY_DIR` | The local index copies notes; size is §6's open question 4 |

## 4. Migration plan

Each step ends with `make check` green in the fork; this repo stays registered and working
until step 9.

1. **Fork** upstream to `akseldkw07/<name>`, clone it beside this repo, add
   `upstream` as a remote. Then the house conventions: branch protection, gitpush flow,
   CI as upstream already runs it, tdd-guard via `tdd-guard-pytest`.
   *Data: none yet.*
2. **`bundle.decode_local`:** `build_note(id, read_ops(buf, 0))`. Add a synthetic local
   fixture with the field-0 root to the tests. *(Upstreamable.)*
3. **`local.py` library and notes:** database clone (`cp -c`, never in place), folders and
   notes from `organizers` / `note_metadata`, `modified_at` from `modified`, change logs and
   `assets/` as the bundle and asset source. `sync --source local` with a synthetic
   container fixture (ported from `test/fixtures/container.ts`).
4. **`drift.py`:** schema and version checks; `status` reports them.
5. **Notability's own text:** port the image-OCR, transcript and handwriting decoders
   (`src/data/fb.ts`, `text.ts`) and index them as `source = image | recording | notability`.
   Bump `INDEX_VERSION`.
6. **Fencing:** wrap tool output with `untrusted.py` and add the policy to `INSTRUCTIONS`.
7. **New tools and filters:** `get_transcript`, `list_attachments`, `get_attachment`,
   `status`, the opt-in `open_note`, and search/list filters.
8. **Desktop:**
   - Run `sync --source local` once (needs Full Disk Access on that Python).
   - Register as `notability-v2` beside the current server, through mcp-compat.
   - Add a launchd job for sync, and a deploy hook.
   - Run the §6 real-data comparison (needs your approval).
   *Data: the first local sync builds the index. This repo has no index of its own, so
   nothing to migrate.*
9. **Switch:**
   - Rename `notability-v2` to `notability` in the Desktop config and remove the old entry.
   - Add a deprecation banner to this repo's README pointing at the fork, then archive it
     on GitHub (reversible).
   - Keep `~/coding/mcp/notability-mcp` locally until you confirm deletion.
10. **Delete** (final, only on your explicit go-ahead): the GitHub repo, and the local
    checkout. Also remove its Full Disk Access grant if the node binary isn't used by
    imessage-mcp; it is today, so keep it.

## 5. Upstream PR candidates (only with your OK)

| Title | Scope | Files |
| --- | --- | --- |
| `fix(bundle): size PDF pages in the background's space` | ink drew off the page on a quarter of inked PDF pages, and OCR missed it (found in §6 of the comparison; fork commit 97f0ba4) | `bundle.py`, `sync.py`, `tests/test_bundle.py` |
| `feat(bundle): decode the Mac app's cached note logs` | `decode_local` (ops in field 0) + fixture | `bundle.py`, `tests/test_bundle.py`, `tests/fixtures/` |
| `feat(sync): local source on macOS, no token needed` | read the app's container (database clone, change logs, assets); `--source local` | new `local.py`, `sync.py`, `cli.py`, tests |
| `feat: fence note text as untrusted in tool results` | prompt-injection boundary for OCR, PDF and typed text | new `untrusted.py`, `server.py`, `tests/test_server.py` |
| `feat: audio transcripts from Notability's recording index` | index transcripts as `source = recording`; `get_transcript` tool | `local.py`, `index.py`, `server.py`, tests |
| `feat: image OCR from Notability's image index` | `source = image` | `local.py`, `index.py`, tests |
| `feat(search): folder, source and date filters` | params on `search_notes` | `store.py`, `server.py`, tests |

Upstream's CONTRIBUTING asks for an issue before anything bigger than a fix, so open
issues first for the local source and transcripts.

## 6. Risks and open questions

1. **Fork name and owner.** You wrote "an awalters fork". Your GitHub account is
   `akseldkw07`, and `akseldkw07/notability-mcp` (this repo) already exists, so a default
   fork name collides. Options:
   - archive and rename this repo first (e.g. `notability-mcp-ts`), then fork as
     `akseldkw07/notability-mcp`;
   - or fork under a new name (e.g. `notability-mcp-local`).
   Which account and name?
2. **House conventions vs upstream's.** Your Python servers use pixi and `mcp_utils`;
   upstream uses uv and has no mcp-utils dependency. Keeping uv and a self-contained
   `untrusted.py` keeps the fork rebaseable and its PRs acceptable. Adopting pixi and
   mcp-utils would make it diverge.
3. **OCR quality is unmeasured.** Notability's own handwriting recognition vs Vision: §6
   of the comparison needs your approval to run on real data.
4. **Index size and privacy.** A local sync copies notes and PDFs (up to about 1 GB of
   assets and 111 MB of logs here) into an unencrypted `index.db`. That's acceptable
   locally; confirm.
5. **Format fragility.** The local container format is undocumented; the field-0 root was
   verified on 400 of 779 notes. The drift guard must cover it. The web path pins a client
   version and can break without notice.
6. **Upstream's activity.** It's two days old with one author. Plan for the fork to be
   self-sufficient, and don't wait on PRs.
7. **Breaking tool changes** for Desktop chats (§2). Run v1 and v2 side by side for a week
   before switching?
8. **Full Disk Access moves to a Python interpreter** (uv-managed; its path changes on
   upgrade). Consider a stable venv path to grant.
