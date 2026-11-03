# notability-mcp

An [MCP](https://modelcontextprotocol.io) server for your [Notability](https://notability.com)
notes. It lets Claude, Cursor or any other MCP client search and read what you wrote, including
handwriting.

Unofficial and not affiliated with Ginger Labs. Notability has no public API, so this reads your
notes through the private API its web app uses, which can change without notice.

> **This fork** ([`akseldkw07/notability-mcp`](https://github.com/akseldkw07/notability-mcp), of
> [`adriandlam/notability-mcp`](https://github.com/adriandlam/notability-mcp)) adds a **local
> source**: on a Mac with the Notability app, `notability-mcp sync --source local` builds the same
> index from the app's own data, with no token and no network. The local source also brings:
>
> - Notability's own handwriting recognition, image OCR and recording transcripts;
> - tools for transcripts, attachments and status;
> - untrusted-text fencing of tool output;
> - checks for app updates that change formats.
>
> See [Local source](#local-source-macos).

## How it works

`notability-mcp sync` downloads your notes into a SQLite file on your computer and runs OCR on
every handwritten page. Search is fuzzy, so "conjugate" still finds a page the OCR read as
"conjogate". The text of PDFs your notes are written on, like lecture slides or past exams, is
searchable too.

OCR is rough, especially on math, so the server can also return any page as an image: your ink
drawn over its PDF page. A model that reads images, like Claude, can then read
your handwriting directly.

The server itself only reads that local file. It never loads your Notability token and makes no
network requests; `sync` is the only command that talks to Notability.

## Requirements

- macOS, Windows or Linux
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A Notability account with your notes synced to it

## Setup

### 1. Install

```sh
uv tool install git+https://github.com/adriandlam/notability-mcp
```

This puts `notability-mcp` on your `PATH`. To update it later, run `uv tool upgrade notability-mcp`.

### 2. Log in

```sh
notability-mcp login
```

You'll be asked for your `nb-auth-token`, the cookie Notability's web app signs you in with. To
copy it, sign in at [notability.com/app](https://notability.com/app), open your browser's
developer tools, go to Application (Chrome, Edge) or Storage (Firefox, Safari), then Cookies >
`https://notability.com`, and copy the value of `nb-auth-token`.

The token is saved in your system keychain: Keychain on macOS, Credential Manager on Windows,
Secret Service on Linux.

### 3. Sync

```sh
notability-mcp sync
```

The first sync downloads every note and its PDFs and runs OCR on each handwritten page. For
4,000 pages that takes about 10 minutes on a Mac and up to an hour on Windows or Linux, where OCR
is slower. Later syncs only fetch notes that changed. Run it again whenever you want new notes to show up, or
schedule it with cron, launchd or Task Scheduler.

### 4. Connect your client

Most clients take this config:

```json
{
  "mcpServers": {
    "notability": {
      "command": "notability-mcp",
      "args": ["serve"]
    }
  }
}
```

If your client can't find `notability-mcp`, use its full path, from `which notability-mcp` (or
`where notability-mcp` on Windows).

<details>
<summary>Claude Code</summary>

```sh
claude mcp add notability -s user -- notability-mcp serve
```

</details>

<details>
<summary>Claude Desktop</summary>

```sh
notability-mcp setup
```

This adds the server to `claude_desktop_config.json` using the full path to `notability-mcp`, since Claude
Desktop doesn't see your shell's `PATH`. Restart Claude Desktop afterwards. To edit the file
yourself, open Settings > Developer > Edit Config.

</details>

<details>
<summary>Cursor</summary>

[Install in Cursor](https://cursor.com/en/install-mcp?name=notability&config=eyJjb21tYW5kIjoibm90YWJpbGl0eS1tY3AiLCJhcmdzIjpbInNlcnZlIl19),
or add the config above to `~/.cursor/mcp.json`.

</details>

<details>
<summary>VS Code</summary>

[Install in VS Code](https://insiders.vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%257B%2522name%2522%253A%2522notability%2522%252C%2522command%2522%253A%2522notability-mcp%2522%252C%2522args%2522%253A%255B%2522serve%2522%255D%257D),
or run:

```sh
code --add-mcp '{"name":"notability","command":"notability-mcp","args":["serve"]}'
```

</details>

<details>
<summary>Codex</summary>

```sh
codex mcp add notability -- notability-mcp serve
```

</details>

Then ask something like:

- What did I write about struct allocation in CPSC 213?
- Summarize my MATH 300 notes from last week.
- Find the page where I worked out the harmonic conjugate and check my steps.

## Local source (macOS)

The Notability Mac app keeps your whole library on disk: a database of folders and notes, one
change log per note (the same ops the web API serves), the attachments, and search indexes with
the text the app has already recognized.

```sh
notability-mcp sync --source local   # or NOTABILITY_MCP_SOURCE=local
```

- **No token and no network.** Nothing in the app's container is ever written. Its database is
  copied (an instant APFS clone) and the copy is opened, because even a read-only SQLite
  connection would take locks on a database the app has open.
- **Full Disk Access** is needed by whatever runs `sync`: your terminal, or the Python a launchd
  job runs. The server itself only needs it for attachments other than PDFs.
- **Handwriting text** comes from the app's own recognition, mapped to pages exactly by page id.
  `--ocr auto` (the default) runs Vision only on inked pages the app found no text in.
  `--ocr app` never runs Vision; `--ocr vision` ignores the app's text. On a real library the
  app's text read slightly better than Vision's, and costs nothing.
- **Image OCR and transcripts** from the app are indexed as the `image` and `recording` sources.
- **Freshness:** a note is re-indexed when it changes, and again when the app re-recognizes it a
  few minutes later. A sync with no changes takes well under a second, so running it every few
  minutes is cheap.
- **Format changes:** the app's index files carry format versions. Unknown ones are refused, not
  guessed, and reported by `notability_status`.

On a library of 770 notes (17,845 pages), the first local sync took 96 s.

## Tools

| Tool | What it does |
|---|---|
| `notability_search_notes` | Fuzzy search over handwriting, typed text, PDF text and titles; returns pages with snippets |
| `notability_list_notes` | Lists notes by folder or recency, plus every folder and when you last synced |
| `notability_read_note` | A note's text page by page, with pages where OCR looks unreliable flagged |
| `notability_get_page_image` | One page as a PNG: your ink over the page's PDF |
| `notability_get_transcript` | A recording's timed transcript (local source) |
| `notability_list_attachments` | The PDFs, images and audio in a note (local source) |
| `notability_get_attachment` | A PDF page as an image or text, an image, or a file's path; only files attached to that note |
| `notability_export_note_pdf` | Saves a note as a PDF at a path you choose (a file or folder; default `~/Desktop`), inside the allowed export folders |
| `notability_find_notes_for_pdf` | Which notes hold a PDF (or each PDF in a folder), by exact content, and which were never imported |
| `notability_status` | Index location, last sync, source, counts, and app formats this version can't read |
| `notability_open_note` | Opens a note in the Mac app. Only registered with `NOTABILITY_MCP_ALLOW_OPEN=1` |

All are read-only except `notability_export_note_pdf`, which writes a file (never inside
Notability, never over an existing file unless asked), and `notability_open_note`, which changes
what's on screen. Note text
(OCR, PDF text, transcripts, titles) can contain instructions aimed at the model, so text results
are fenced as untrusted data, and titles and snippets in structured results are neutralized.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NOTABILITY_MCP_DB` | see below | Where the index is stored |
| `NOTABILITY_TOKEN` | keychain | Token for `sync`, used instead of the keychain |
| `NOTABILITY_CLIENT_VERSION` | `26.43.7` | Web app version to report, in case Notability starts rejecting old ones |
| `NOTABILITY_MCP_SOURCE` | `web` | Default for `sync --source` |
| `NOTABILITY_LIBRARY_DIR` | the app's container `Data/Library` | Where the Mac app's data is (local source) |
| `NOTABILITY_MCP_CACHE_DIR` | `~/Library/Caches/notability-mcp` | Scratch space for database copies |
| `NOTABILITY_MCP_EXPORT_ROOTS` | `~/Desktop`, `~/Documents`, `~/Downloads` | Folders exports may be written in (`:`-separated); the first is the default |
| `NOTABILITY_MCP_ALLOW_OPEN` | unset | `1` registers `notability_open_note` |
| `NOTABILITY_MCP_OPEN_COMMAND` | `/usr/bin/open` | What `notability_open_note` runs |

The index is stored in `~/Library/Application Support/notability-mcp` on macOS,
`%LOCALAPPDATA%\notability-mcp` on Windows and `~/.local/share/notability-mcp` on Linux.

## Limitations

- OCR is Apple's Vision framework on macOS and [RapidOCR](https://github.com/RapidAI/RapidOCR)
  elsewhere. On 20 pages of handwritten math, Vision found 44% of the key words and 7% of the
  formulas; RapidOCR found 46% and 16%. Search will usually find the right page, but read the
  page image before trusting anything exact.
- Page images (and PDF exports) show ink, shapes, typed text, the paper or PDF page and, with
  the local source, pasted images. They're rendered, not the app's own export: body text is
  checked against the app's exports (`tests/test_parity.py`), text boxes and lists less so.
- Notes other people shared with you aren't synced.
- Audio recordings aren't transcribed or searched by the web source; the local source indexes
  the transcripts Notability made.
- On Linux without a keyring (a headless server, say), `login` can't save the token. Set
  `NOTABILITY_TOKEN` instead.

## Contributing

See [CONTRIBUTING.md](https://github.com/adriandlam/notability-mcp/blob/main/CONTRIBUTING.md).
To report a security problem, follow
[SECURITY.md](https://github.com/adriandlam/notability-mcp/blob/main/SECURITY.md) instead of
opening an issue.

## Credits

The Notability wire format was reverse-engineered by
[notability-cli](https://github.com/Zpankz/notability-cli) (MIT). See
[THIRD_PARTY_NOTICES.md](https://github.com/adriandlam/notability-mcp/blob/main/THIRD_PARTY_NOTICES.md).
