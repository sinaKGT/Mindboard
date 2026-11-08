# Mindboard — Design & Architecture

**Status:** draft v0.1 · 2026-08-30 · awaiting your review
**Locked:** Python backend + React canvas · native `.mindboard` bundle · AI deferred to v2
**All open questions resolved 2026-08-30 — see §14**

---

## 1. What Mindboard is

A **local-first web application** for mapping ideas and their context into a
multimodal graph on an infinite canvas.

The loop it optimises for: *think → drop a node → connect it → name the
relationship → zoom out and see the shape of your thinking.*

### Principles

These are the tie-breakers for every later argument:

1. **The canvas is the application.** No menu bar, no sidebar, no toolbar.
   Anything that isn't the graph is either transient (appears on interaction,
   disappears after) or lives behind the command palette.
2. **Local and isolated.** Runs on `127.0.0.1`, no account, no cloud, no
   telemetry. One CLI command installs it into its own environment.
3. **Multimodal by default.** A node is a thought; a thought may be text, an
   image, a video, a link, or an entire board of its own.
4. **Relationships are first-class.** An edge carries a label and a kind — it is
   a statement, not a line.
5. **You own the data.** One command exports the whole system, media included,
   as a single portable file. Import restores it exactly.
6. **Structure over decoration.** Meta-nodes provide hierarchy so a 500-node
   board stays readable.

### Explicitly out of scope for v1

Multi-user / realtime collaboration, cloud sync, mobile layout, plugin system,
version history beyond undo, and all AI features (§11).

---

## 2. Stack

| Layer | Choice | Rationale |
|---|---|---|
| Canvas & UI | React + TypeScript, **`@xyflow/react`** v12 (MIT, free for commercial use) | Nodes are React components, so `<video>`, `<img>` and a markdown editor *inside* a node are trivial. Pan/zoom, selection, handles, edge routing, minimap already solved and battle-tested. |
| State | Zustand store driven by a typed **op reducer** (§5) | Single mutation path → undo/redo, persistence and (v2) AI editing all fall out of one mechanism. |
| Styling | CSS custom properties + CSS Modules (no Tailwind) | Theming is a *product feature* here — the palette must be editable at runtime, which CSS variables do natively and utility classes fight. |
| Bundler | Vite | Fast dev loop; produces the static build embedded in the Python package. |
| Backend | Python 3.10+, FastAPI, Uvicorn | Matches your environment. Async streaming for video range requests. The natural host for v2's LLM calls. |
| Storage | SQLite (WAL mode) + content-addressed asset files | Incremental writes instead of rewriting a JSON file on every drag; crash-safe; queryable when v2 needs embeddings. |
| Packaging | One Python package with the **frontend prebuilt inside it**, installed via `uv tool install` / `pipx` | You never need Node to *run* Mindboard. One isolated venv. Windows-friendly. |

### Why not a raw `<canvas>` renderer

Pixi/Konva win above ~5–10k nodes. But every node here wants to contain real
content — an editable markdown block, a playing video, a focusable text field.
On raw canvas you end up re-implementing text editing and overlaying DOM anyway.
xyflow with `onlyRenderVisibleElements` comfortably handles the 1–2k-node boards
this tool is actually for. If that ceiling is ever hit, the escape hatch is a
canvas-rendered *level-of-detail* mode at low zoom — the data model doesn't change.

---

## 3. Data model

### Concepts

- **Board** — one document. Has settings (theme, palette) and a root graph.
- **Node** — a unit of thought. Typed: `text` | `image` | `video` | `link` | `file` | `meta`.
- **Edge** — a directed-or-not relationship between two nodes, with a label and a kind.
- **Meta-node** — a node that *owns a child graph* (§4).
- **Asset** — a media blob, content-addressed by SHA-256, referenced by nodes.

### SQLite schema

```sql
CREATE TABLE board (
  id             TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  description    TEXT NOT NULL DEFAULT '',    -- shown on the launcher card
  schema_version INTEGER NOT NULL,
  revision       INTEGER NOT NULL DEFAULT 0,   -- bumped by every applied op
  settings_json  TEXT NOT NULL,                -- theme, palette, viewport
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE node (
  id           TEXT PRIMARY KEY,               -- ULID (sortable, offline-safe)
  board_id     TEXT NOT NULL REFERENCES board(id) ON DELETE CASCADE,
  parent_id    TEXT REFERENCES node(id) ON DELETE CASCADE,  -- NULL = root level
  type         TEXT NOT NULL,                  -- text|image|video|link|file|meta
  x            REAL NOT NULL,
  y            REAL NOT NULL,
  w            REAL,                           -- NULL = auto-size to content
  h            REAL,
  z            INTEGER NOT NULL DEFAULT 0,
  title        TEXT,                           -- short label, always shown
  content_json TEXT NOT NULL DEFAULT '{}',     -- type-specific payload
  style_json   TEXT NOT NULL DEFAULT '{}',     -- colour, font size/weight, align, shape
  collapsed    INTEGER NOT NULL DEFAULT 0,     -- meta-nodes only
  provenance   TEXT NOT NULL DEFAULT 'user',   -- user|import|ai   <- reserved for v2
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX node_board_parent ON node(board_id, parent_id);

CREATE TABLE edge (
  id            TEXT PRIMARY KEY,
  board_id      TEXT NOT NULL REFERENCES board(id) ON DELETE CASCADE,
  source_id     TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  target_id     TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  source_handle TEXT,                          -- NULL = auto/floating
  target_handle TEXT,
  label         TEXT,                          -- the relationship, in your words
  kind          TEXT NOT NULL DEFAULT 'relates',-- semantic class, user-extensible
  directed      INTEGER NOT NULL DEFAULT 1,
  style_json    TEXT NOT NULL DEFAULT '{}',
  provenance    TEXT NOT NULL DEFAULT 'user',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX edge_board_source ON edge(board_id, source_id);
CREATE INDEX edge_board_target ON edge(board_id, target_id);

CREATE TABLE asset (
  sha256      TEXT PRIMARY KEY,                -- also the filename on disk
  mime        TEXT NOT NULL,
  bytes       INTEGER NOT NULL,
  orig_name   TEXT,
  width       INTEGER, height INTEGER,         -- images/video
  duration_ms INTEGER,                         -- video/audio
  created_at  TEXT NOT NULL
);

CREATE TABLE node_asset (                      -- many-to-many, enables GC
  node_id TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  sha256  TEXT NOT NULL REFERENCES asset(sha256),
  role    TEXT NOT NULL DEFAULT 'primary',
  PRIMARY KEY (node_id, sha256, role)
);
```

### `content_json` by node type

```jsonc
// text  — plain text; presentation lives in style_json, not in markup
{ "text": "Traffic density predicts dwell time at junction 4" }
// image
{ "sha256": "ab12…", "alt": "site plan", "fit": "cover" }
// video
{ "sha256": "cd34…", "poster": "ef56…", "start_ms": 0, "loop": false }
// link
{ "url": "https://…", "host": "…", "excerpt": "…", "thumb": "9a7b…", "provider": "YouTube" }
// file
{ "sha256": "…", "filename": "notes.pdf" }
// meta
{ "summary": "Everything about sensor placement", "child_count": 12 }
```

Keeping the payload in one JSON column means a new node type in v2 is a
frontend component plus a schema entry — **no migration**.

Every one of those hashes is a file the board owns, whichever key it sits under
(`sha256`, `poster`, `thumb`, and whatever the next card type invents). Export,
backup and `gc` therefore find them **by shape** — a 64-character hex string
anywhere in `content_json` — never by a list of key names. The list version of
this shipped first and had already lost `thumb`: link covers were left out of
every bundle and `gc` would have deleted them off disk. See §16.

### Two decisions worth flagging now

- **ULIDs, not autoincrement.** IDs are generated client-side so a node exists
  the instant you create it, before any round-trip. Also makes import/merge sane.
- **`provenance` exists in v1** even though nothing writes `'ai'` yet. Adding it
  later means migrating every row and re-exporting every bundle; adding it now
  costs one column and lets v2 render AI-suggested nodes distinctly on day one.

---

## 4. Meta-nodes

The feature that keeps big boards readable. A meta-node is a node that **owns a
child graph** — its children have their own coordinate space, so entering a
meta-node is entering a fresh canvas, not zooming into a crowded corner.

### Three states, one model

| State | Looks like | How you get there |
|---|---|---|
| **Collapsed** (default) | A single node showing title, summary and a `12 nodes` badge | Default; `Esc` from inside |
| **Expanded preview** | A frame on the parent canvas showing children scaled down, read-only | Click the expand chevron |
| **Entered** | Full canvas of just the children, breadcrumb `Board › Sensors › Placement` at top-left | Double-click the meta-node |

### Edges that cross a boundary

Edges are **global** — `source_id`/`target_id` may name any node anywhere in the
board. Rendering resolves each endpoint to the nearest visible ancestor at the
current level:

```
Root level                        Inside "Sensors"
┌──────────┐    "informs"         ┌──────────┐   "informs"
│ Sensors  │ ─────────────▶ Cost  │ Lidar #3 │ ─────────────▶ ⟨Cost⟩
│  ⟨12⟩ ×3 │                      └──────────┘        ghost node,
└──────────┘                                          click to jump out
     ▲
  3 real edges from inside Sensors, drawn as one
  labelled bundle; hover to list them
```

- At root, three edges from nodes inside `Sensors` to `Cost` collapse into one
  bundle edge with a `×3` badge; hovering lists the real relationships.
- Inside `Sensors`, an edge to a node that lives elsewhere renders as a **ghost
  node** at the canvas margin. Clicking it navigates to the real node.

This is the part most tools get wrong (they forbid cross-boundary edges
entirely). Getting it right is why edges are global and hierarchy lives only on
`node.parent_id`.

### Operations

- **Wrap:** select nodes → `Ctrl+G`. Creates a meta-node at the selection's
  centroid, reparents the selection, rebases their coordinates to the new
  origin. Edges are untouched — they resolve automatically.
- **Unwrap:** `Ctrl+Shift+G`. Children move back to the parent level, offset by
  the meta-node's position. Meta-node is deleted.
- **Nesting** is unlimited. The breadcrumb is the depth gauge; a soft warning
  appears past 5 levels because that usually means the model is wrong.
- **Deleting** a meta-node asks: delete children too, or unwrap first?

---

## 5. State, persistence and undo — one mechanism

Every mutation in the app — a drag, a typed character, a paste, and in v2 an AI
suggestion — becomes an **op**:

```ts
type Op =
  | { kind: 'node.create';  node: Node }
  | { kind: 'node.update';  id: string; patch: Partial<Node> }
  | { kind: 'node.move';    moves: Array<{id: string; x: number; y: number}> }
  | { kind: 'node.delete';  ids: string[] }
  | { kind: 'edge.create';  edge: Edge }
  | { kind: 'edge.update';  id: string; patch: Partial<Edge> }
  | { kind: 'edge.delete';  ids: string[] }
  | { kind: 'meta.wrap';    metaId: string; childIds: string[] }
  | { kind: 'meta.unwrap';  metaId: string }
  | { kind: 'board.update'; patch: Partial<BoardSettings> };

interface Envelope { opId: string; ts: number; op: Op; inverse: Op; origin: 'user'|'ai'|'import' }
```

Flow for every change:

```
UI event → makeOp() → applyLocal()  → store updates, canvas repaints (instant)
                    → undoStack.push(inverse)
                    → outbox.enqueue()
                         └─ debounced 300 ms / flushed on blur & unload
                              → POST /api/ops  → SQLite transaction → revision++
```

Why this shape:

- **Undo/redo is free** — each op carries its own inverse; no diffing.
- **The UI never waits on the disk.** Optimistic by construction.
- **Ops are idempotent by `opId`**, so a retried flush can't duplicate work.
- **v2 AI editing needs zero new machinery.** The model emits ops with
  `origin: 'ai'`; they land in the same reducer, are undoable the same way, and
  render with the `provenance` styling that already exists.
- If the backend rejects a batch (stale revision), the client refetches the
  affected subtree and replays its outbox. Single-user, so this is rare.

**Crash safety:** the outbox is mirrored to `sessionStorage` before each flush,
so a browser crash between edit and flush loses nothing.

---

## 6. HTTP API

All routes are same-origin, bound to `127.0.0.1`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/board` | Board metadata, settings, current revision |
| `GET` | `/api/graph?parent=<id\|root>&depth=1` | Nodes + edges for one level (lazy loading; `depth=all` for small boards) |
| `POST` | `/api/ops` | `{ baseRevision, envelopes[] }` → `{ revision, applied[], rejected[] }` |
| `POST` | `/api/assets` | Multipart upload → dedupe by SHA-256 → `{ sha256, mime, width, height, duration_ms }` |
| `GET` | `/api/assets/{sha256}` | Stream the blob. **Must implement HTTP `Range`** — without it `<video>` seeking silently fails in Chrome |
| `GET` | `/api/assets/{sha256}/thumb` | Generated WebP thumbnail (lazy, cached on disk) |
| `GET` | `/api/export` | Streams a `.mindboard` bundle (§7) |
| `POST` | `/api/import` | Upload a bundle → `{ mode: 'replace' \| 'merge' }` |
| `GET`/`PUT` | `/api/settings` | Theme and palette |
| `GET` | `/api/search?q=` | Full-text over node titles/markdown (SQLite FTS5) |
| `GET` | `/healthz` | Used by the CLI to know the server is up |

### Security posture for a "local and isolated" app

Localhost is not a trust boundary — any process or stray browser tab on the
machine can reach `127.0.0.1:7788`. So:

- Server binds `127.0.0.1` only, never `0.0.0.0`.
- A per-install token is generated into the data dir. `mindboard start` opens
  `http://127.0.0.1:7788/?t=<token>`; the app exchanges it for a `SameSite=Strict`
  cookie and strips it from the URL. API calls without the cookie get 401.
- Strict CORS (same-origin), and a CSP with no remote origins — the app loads
  nothing from the internet at runtime.
- Uploads are sniffed for real MIME type, not trusted by extension; assets are
  served with `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`
  except for the image/video types the canvas renders inline.

---

## 7. The `.mindboard` bundle

A zip archive with a fixed layout. One file carries the entire system.

```
board.mindboard  (zip)
├── manifest.json      format version, app version, board id/title, counts, checksum
├── graph.json         the canonical serialisation — nodes, edges, settings
├── assets/
│   ├── ab12cd….jpg    content-addressed, extension from real MIME
│   └── ef34ab….mp4
└── thumbs/            optional, regenerable — skipped with --no-thumbs
```

`graph.json` is deliberately flat and readable:

```jsonc
{
  "format": "mindboard/1",
  "board": { "id": "…", "title": "Nottingham DT ideas", "settings": { … } },
  "nodes": [
    { "id": "01J…", "type": "text", "parent": null, "title": "Hypothesis",
      "content": { "markdown": "…" }, "pos": [120, -40], "size": [280, null],
      "style": {}, "provenance": "user" }
  ],
  "edges": [
    { "id": "01J…", "source": "01J…", "target": "01J…",
      "label": "constrains", "kind": "relates", "directed": true }
  ]
}
```

Guarantees:

- **Lossless round-trip.** `import(export(B)) === B`, including node IDs, so a
  bundle is a backup, not just a share.
- **Versioned.** `format` gates migrations; the importer refuses a newer format
  with a clear message rather than corrupting a board.
- **Merge mode.** Importing into an existing board namespaces incoming IDs and
  drops the incoming graph as a single meta-node, so nothing collides.
- **LLM-legible.** The flat `nodes`/`edges` shape with human-readable labels is
  exactly what v2 will hand to a model (§11). Designing it that way now costs
  nothing.

Import validates before it writes: schema check → asset checksum check →
referential integrity (every `parent`/`source`/`target`/`sha256` resolves) →
then a single transaction. A partial bundle never half-lands.

---

## 7b. Backups

A `.mindboard` bundle carries one board. A **backup** carries the library:

```
mindboard-backup-2026-08-30.zip
├── manifest.json                 format, app version, date, one entry per board
└── boards/
    ├── nottingham-dt-ideas-01J7….mindboard
    └── thesis-chapters-01J8….mindboard
```

It is an ordinary zip of the bundles we already trust, so there is one storage
format to get right rather than two, and any zip tool can open it. Board file
names carry the title as well as the id, so the archive is legible a year later.

**Restoring keeps identity.** Each board goes back under its original id, which
makes restoring the same backup twice a no-op rather than a way to double your
library — the default `skip` mode leaves boards that are already present alone
and says so. `--replace` overwrites them from the backup; `--copy` brings them
in alongside under fresh ids. The same three modes are available over HTTP, and
the app's **Import** accepts a backup zip as readily as a single bundle.

---

## 7c. Exporting a picture

`Ctrl+E`, the dock, or the command palette renders the **whole current level**
as an image — every node, not the visible viewport. The nodes' bounding box sets
the canvas size, the viewport transform is overridden for the render, and
`pixelRatio` multiplies it: 2× by default, 4× for print, or SVG for vector.
Chrome, ports and live `<video>` elements are hidden for the render and each
video stands in with its captured poster frame.

One trap worth recording: an exported image is a *clone* of the DOM, and CSS
class rules do not reliably survive the clone. Any SVG path whose `fill: none`
lived only in a stylesheet came out filled black — including the invisible
18px-wide click targets that sit under every edge, which painted as large black
blobs. Presentation attributes and inline styles survive; class rules may not.
Pin `fill` and `stroke` on the element for anything that must render.

---

## 8. Interface and interaction

### What is permanently on screen

Almost nothing:

```
┌────────────────────────────────────────────────────────────┐
│ Board › Sensors ⌄                                          │  breadcrumb, only when nested
│                                                            │
│                    [ the canvas ]                          │
│                                                            │
│                                              ⊕ ⊖ ⤢  ▫️ ⚙   │  zoom, fit, minimap, settings
└────────────────────────────────────────────────────────────┘
```

Everything else is transient: a node's controls appear on hover, an edge's label
input on click, and `Ctrl/Cmd+K` opens the command palette that replaces the
menu bar entirely (import, export, theme, search, jump-to-node, wrap selection).

### Core interactions

| Action | Gesture |
|---|---|
| New text node | **`a`** — lands under the pointer, in edit mode. Double-clicking empty canvas does the same |
| New media node | Drag a file onto the canvas, or paste an image / URL |
| See a picture full screen | Click it (§21) |
| Open a PDF in your own reader | Click the card's face (§22) |
| Connect | Hover a node and four **⊕ ports** appear. *Click* one, a line follows the cursor, click any node to finish. Dragging from the same port still works for anyone who prefers it |
| Connect + create | Drag from a port — or, while linking, click **empty canvas** → creates a node and connects it. This is the fast idea-flow path — the single most important interaction in the app |
| Select a relationship | Click it — the line lights up and its label grows an ✕. `Delete` removes it |
| Label a relationship | Double-click the edge → inline input on the line |
| Copy / cut / paste | `Ctrl+C` / `Ctrl+X` / `Ctrl+V` — takes the cards, the relationships between them, and the contents of any group |
| Duplicate | `Ctrl+D`, or the ⧉ button on a card |
| Move one card alone | Hold `Alt` while dragging |
| Group into meta-node | Select two or more → a bar appears over them with **Group** (`Ctrl+G` still works) |
| Into an existing group | Drag the node onto the group — it lights up as you cross it |
| Move the graph with a card | Hold `Alt` while dragging |
| Back out of a group | **↑** in the node's toolbar |
| Connect | **`c`** from the selected node, the **link** button in its toolbar, or a ⊕ port. Then click the other node |
| Enter / leave meta-node | Double-click / `Esc` |
| Search & jump | `Ctrl+K`, type, `Enter` — canvas pans and highlights |
| Multi-select | Drag a marquee, or `Shift`-click |
| Undo / redo | `Ctrl+Z` / `Ctrl+Shift+Z` — spans everything including wraps and imports |

The open board lives in the URL (`#/b/<id>`), so a refresh keeps your place and
the browser's back button walks out of a board the way you would expect.

Auto-layout is available but never automatic: `Ctrl+L` applies a force-directed
or hierarchical tidy to the **current level only**, animated so you can see what
moved, and undoable in one step.

### Click-to-click linking

A port is both a drag handle and a button. The distinction is pointer travel:
under six pixels is a click and opens linking; more than that is xyflow's own
drag-connect. The port itself detects the click — relying on the connection
system to report a gesture that never moved turned out not to work — and the
canvas applies the same threshold so a click never also drops a node.

While a link is open the cursor drags a dashed line behind it, the ports
disappear so nothing competes for the click, every node highlights as a target,
and `Esc` cancels.

### Node anatomy

Every node type carries the same editable pair: a title and a body. An image or
a video is the medium *plus* the thought about it — a picture with no note is a
picture, not an idea. Double-click anywhere on a node to edit; the body is plain
text, styled by size and colour rather than markup.

Top to bottom: type accent bar, media (if any), title, body, type-specific
trimmings (a link's host, a video's duration, a group's node count and
miniature). On hover: a control row with colour, **text size**, add-connected
and delete. The row floats above the node, and while the node is hovered its hit
area extends upward to cover the gap — otherwise the row vanishes as you reach
for it.

Nodes auto-size to content until you resize one manually, after which they keep
your size — the default 380px readability cap is lifted the moment you set a
width yourself, or the card refuses to fill the frame you dragged.

**Conversion beats auto-save.** A node being turned into a link sets a flag that
makes the debounced auto-save a no-op for the rest of that edit. Without it the
1.5s idle save fired *after* the conversion landed, saw a node that was now a
`link`, skipped the "is this a URL?" branch, and wrote the raw address back over
the node as its title and body — a link node displaying its own URL twice.

**Why there is no embedded player.** An embed made the app send a request to the
provider every time a board opened, and YouTube rejected it anyway: the app
sends `Referrer-Policy: no-referrer`, and YouTube answers a referrer-less embed
with "Error 153". Relaxing the policy for that one iframe made the request
legal, but the whole feature was still trading a local, silent board for a
dependency on Google. A stored cover and `window.open` gives the same thing
without any of it. A double-click on a cover is still one intent, so a second
click within 500ms is ignored rather than opening two tabs.

**A link with no cover can ask for one.** Nodes made before covers existed — or
while the server was mid-upgrade — show a **Get cover** button rather than a
black rectangle, so a board can be repaired without re-pasting every link.

**The frontend detects video links too.** The same YouTube/Vimeo matching runs
client-side as a fallback, so a video still gets its player even when the server
tells us nothing about it — an older backend, a blocked page, a failed fetch.
The stored preview still needs the server; the player does not.

**A pasted link becomes the link.** Pasting a bare URL onto the canvas makes a
link node; pasting one into an empty node, or leaving one as a node's whole
body, converts that node in place. Pasting a YouTube address into a text
box and got the text — reasonable behaviour for a text box, and useless. The
address is never what you meant.

**Double-click anywhere on a node writes on it**, handled once by the canvas.
Three things keep their own: a `<video>`, for the player; the picture on an
image card, where a single click opens it full screen (§21); and the face of a
PDF card, where a single click opens it in your reader (§22). Write on those
cards by double-clicking the caption, or select one and press `Enter`. Dropping a single file also opens its
caption straight away — a picture usually arrives with something to say about
it.

Text size has three ways in: `A` / `A` step it, and clicking the number opens a
menu of sizes, each previewed at its own size.

**Pictures are contained, never cropped.** `object-fit: contain` throughout: a
diagram whose top row of labels has been sliced off to make it fit is worse than
one with a little space around it. Give a node a height and the media takes the
room you made — it becomes a flex column, the picture grows into the space and
the caption keeps its own — so resizing never leaves a hole under the text. The
leftover space around a contained picture is transparent so it reads as the
card; only video keeps a black backdrop, where letterboxing is expected.

---

## 9. Theming

Dark-first, with the palette as a user-editable feature rather than a hardcoded
stylesheet.

```css
:root {
  --mb-bg:        #0d0f13;   /* canvas */
  --mb-grid:      #171a21;
  --mb-surface:   #161a21;   /* node body */
  --mb-surface-2: #1e232c;   /* hover, controls */
  --mb-border:    #2a3038;
  --mb-text:      #e6e9ef;
  --mb-text-dim:  #8b93a1;
  --mb-accent:    #6ea8fe;   /* selection, focus, active edge */
  --mb-edge:      #3a4250;

  /* per node type — these are what the user actually wants to customise */
  --mb-type-text:  #6ea8fe;
  --mb-type-image: #7ee0a8;
  --mb-type-video: #d78ce8;
  --mb-type-link:  #f0c674;
  --mb-type-meta:  #ff9e64;
}
```

- Ships with 3–4 presets (Midnight, Slate, Ink, plus a Light theme derived from
  the same token names).
- The settings popover exposes each token as a colour swatch; changes apply
  live via `document.documentElement.style.setProperty` and persist in
  `board.settings_json`, so a theme travels inside the bundle.
- Contrast is validated at save time — a palette that drops text below 4.5:1 on
  its background gets a warning, not a silent unreadable board.
- Motion: 120–180 ms `ease-out` for everything; no bounce, no spring. Respects
  `prefers-reduced-motion`.
- Type: one variable UI font (Inter or system stack) plus a mono face for code
  blocks inside text nodes. No decorative fonts.

---

## 10. Install and CLI

### The user's experience

Double-click `Mindboard.bat` (Windows) or `Mindboard.command` (macOS / Linux).
The launcher installs on first run, then opens a numbered menu: open the app,
back up, restore, list boards, check the install, open the data folder,
reinstall. Every option is a thin wrapper over a CLI command, so nothing exists
only in the menu.

Two details that matter more than they look:

- **The menu serves on a background thread and stops on Enter**, not Ctrl+C.
  Pressing Ctrl+C inside a `.bat` makes cmd ask "Terminate batch job (Y/N)?",
  which is a baffling way to close an app.
- **Restore names the choice in plain language.** When a backup overlaps boards
  you already have, the menu offers "leave mine alone / replace mine / add
  alongside" rather than `--replace` and `--copy`.

Behind it, for anyone who prefers a terminal:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1    # Windows
```
```bash
./install.sh                                              # macOS / Linux
```

One command. The installer finds a Python 3.10+, builds an isolated environment
*inside the project folder* (uv when present, `venv` otherwise — never a
system-wide install), installs the package, then hands off to `mindboard
install`, which prepares storage, generates the local access key, and builds the
interface if Node is available and it is not already built. Then:

```
.venv/bin/mindboard run
```

The published wheel ships the interface prebuilt as package data, so a user who
installs with `uv tool install mindboard` never needs Node at all — the build
step in `mindboard install` exists only for source checkouts.

**Packaging trap, learned the hard way.** The built interface is gitignored, so
hatchling skips it unless told otherwise. The obvious fix — a `force-include`
mapping `src/mindboard/web` into the wheel — quietly breaks *editable* installs:
hatchling copies that one folder into `site-packages/mindboard/` and writes no
`.pth`, so `import mindboard` finds an empty namespace package and every
submodule disappears with `No module named 'mindboard.cli'`. The correct knob is
`artifacts = ["src/mindboard/web/**"]`, which includes gitignored files without
remapping any path. `tests/test_packaging.py` guards both halves: the wheel must
contain the interface, and the wheel target must never regain a force-include.

Because an install can look successful and still be unusable, both installers
run `mindboard version` afterwards and rebuild the environment once from scratch
if it fails, rather than reporting success and leaving a traceback for later.

### Commands

| Command | Does |
|---|---|
| `mindboard menu` | The screen the desktop launcher opens |
| `mindboard run [--port] [--data-dir] [--no-open] [--dev]` | Serve on 127.0.0.1 and open the browser |
| `mindboard install [--with-demo] [--skip-build]` | Prepare storage, key and interface. Idempotent |
| `mindboard backup [-o FILE]` | Every board, media included, into one zip |
| `mindboard restore FILE [--replace\|--copy]` | Put a whole library back |
| `mindboard boards` / `new` / `delete` | Board housekeeping |
| `mindboard export <id> -o F` / `import F` | One board at a time |
| `mindboard doctor` | Python version, storage permissions, interface built, port free, DB integrity per board |
| `mindboard gc` | Delete media no node references any more |
| `mindboard where` | Print the data directory |

`start` still works as a hidden alias for `run`.

### Data directory

```
%APPDATA%\Mindboard\            (Windows)
~/.local/share/mindboard/       (Linux/macOS)
├── token                       per-install access key, 0600
└── boards/
    └── 01J7…/
        ├── board.db            SQLite, WAL
        ├── assets/ab/ab12cd….jpg
        └── thumbs/
```

Sharded by the first two hex chars so a board with thousands of images doesn't
produce a directory that Explorer chokes on. `--data-dir` overrides everything,
so a board can live in a synced or project folder if you want.

### Developing on it

```bash
./install.sh
.venv/bin/mindboard run --dev             # FastAPI with reload
cd web && npm install && npm run dev      # Vite on :5173, proxying /api
```

`./build.sh` writes into `src/mindboard/web/`, which the wheel picks up. Only
contributors need Node.

## 11. Forward-compatibility with v2 (AI)

v1 ships no AI, but two v2 features are already decided, so v1 must not make
them expensive:

**(a) Export the whole graph to an LLM for analysis.** `graph.json` is already
the right shape. What v1 adds: a `to_context()` projection in the backend that
walks the graph depth-first and emits compact markdown —

```
# Board: Nottingham DT ideas
## [meta] Sensors  (12 nodes)
- [text] Lidar placement — "corner mounting reduces occlusion by ~30%"
  → constrains → [text] Budget
- [image] Site plan (image, 1920×1080)
```

— with a token budget, so a large board degrades by summarising meta-nodes
rather than truncating mid-graph. Media becomes captions in v2; the same hook
takes vision model output later.

**(b) AI co-creates nodes on the canvas.** This is why §5 exists. The model
returns ops, not prose. They enter the same reducer with `origin: 'ai'`, render
in a "proposed" style using the `provenance` column v1 already has, and you
accept or reject them — accept is a no-op, reject is the existing undo path.

Reserved but unused in v1: a `node_embedding(node_id, vec BLOB)` table (for
`sqlite-vec` semantic search), and an `/api/ai/*` route namespace.

---

## 12. Repository layout

```
mindboard/
├── DESIGN.md                    ← this file
├── README.md
├── Mindboard.bat / .command     double-click launcher -> the menu
├── install.ps1 / install.sh     one-command isolated setup
├── build.sh                     builds the interface into the package
├── pyproject.toml               packaging, CLI entry point, deps
├── src/mindboard/
│   ├── cli.py                   Typer commands
│   ├── menu.py                  the launcher's pick-an-option screen
│   ├── runtime.py               data dir, token, ports, serving, diagnostics
│   ├── app.py                   FastAPI app, static mount, auth middleware
│   ├── db.py                    connection, WAL setup, migrations runner
│   ├── models.py                Pydantic schemas
│   ├── ops.py                   op validation + application (the core)
│   ├── assets.py                upload, hashing, thumbnails, range streaming
│   ├── bundle.py                .mindboard bundles, and whole-library backups
│   ├── migrations/001_init.sql
│   └── web/                     ← generated: built frontend
├── web/
│   ├── src/
│   │   ├── canvas/              xyflow wiring, viewport, selection, marquee
│   │   ├── nodes/               TextNode, ImageNode, VideoNode, LinkNode, MetaNode
│   │   ├── edges/               LabeledEdge, BundleEdge, GhostEndpoint
│   │   ├── store/               zustand store, ops, undo stack, outbox
│   │   ├── commands/            Ctrl+K palette and command registry
│   │   ├── theme/               tokens, presets, settings popover
│   │   └── api/                 typed client
│   └── vite.config.ts
└── tests/
    ├── test_ops.py              every op + its inverse; wrap preserves all edges
    ├── test_bundle.py           import(export(b)) == b, incl. asset bytes
    ├── test_backup.py           library round-trip; restoring twice is a no-op
    └── test_api.py              auth, ops, range requests, backup over HTTP
```

---

## 13. Milestones

| # | Deliverable | Status |
|---|---|---|
| M0 | Skeleton - `mindboard start` serves a pannable canvas from the installed package | done |
| M1 | Text nodes, typed edges, op-log persistence, undo/redo | done |
| M2 | Multimodal - drag/paste images and video, range streaming, poster capture, link previews | done |
| M3 | Meta-nodes - wrap, unwrap, enter, breadcrumb, bundled cross-boundary edges, ghost endpoints | done |
| M4 | Command palette, theming UI, full-text search, auto-layout, keyboard map | done |
| M5 | `.mindboard` export/import, CLI equivalents, `doctor`, `gc` | done |
| - | **v1 complete** | |
| M6 | v2a - graph to LLM analysis | next |
| M7 | v2b - AI co-editing via ops | next |

Verified end to end: 11 backend tests (ops and their inverses, meta-wrap edge
preservation, bundle round-trip, HTTP surface including range requests), plus a
browser pass covering create, connect-by-drag, undo/redo, reload persistence and
theme persistence.

## 14. Resolved decisions

All five open questions were answered on 2026-08-30. Recorded here because the
reasoning matters more than the answers.

**1. Many boards, with a launcher.** Opening Mindboard shows a card view of
existing boards — each card carries name, description and created date — plus
**New** and **Import**. This is the one screen that exists outside the canvas;
it earns its place because it is where import lives and where a board's identity
is set. Boards are independent folders in the data dir, so each exports, backs
up and deletes on its own. `board` gains a `description` column.

**2. Edges get a fixed `kind` vocabulary *and* a free label.** Six kinds —
`relates`, `causes`, `supports`, `contradicts`, `part-of`, `example-of` — each
with its own line treatment (solid, arrowed, dashed, dotted, bracketed) **and
its own glyph**: joined dots, an arrow, a tick, a cross, a nested square, a
diamond. The glyph sits mid-edge whether or not the edge is labelled, so the
relationship is legible without hovering, without colour vision, and in a
printed export. The vocabulary is closed on purpose: v2's analysis reasons over
typed relations, and a drifting user-defined set would give the model nothing
stable to reason with.

**3. Media is stored locally and plays on the canvas.** A video arrives as a
dropped file or a pasted URL; a direct media URL is fetched and stored in the
board's asset directory, and the node plays it inline. A platform link keeps its
*preview* locally and plays through the provider's embed on demand (see the
amendment below). Everything you can store is stored, so a board stays legible
offline and every export is complete.

- Pasted URL pointing at a media file (`.mp4`, `.webm`, `.gif`, images) →
  fetched server-side, hashed, stored, becomes a video/image node.
- Pasted URL pointing at a page → becomes a `link` node with title, excerpt and
  **a preview picture stored locally** like any other media (the page's
  `og:image`, or the platform's thumbnail). A board still shows what its links
  are with the network off.
- Pasted URL pointing at a **video platform** (YouTube, Vimeo) → the same: the
  platform's thumbnail is stored locally as the node's cover, and clicking the
  cover opens the video in your browser.

  **Settled 2026-08-30, after two attempts.** The first design made platform
  links references only. The second embedded the provider's player in the node —
  which YouTube refused outright ("Error 153") because the app sends no
  referrer, and which meant a board with video links on it talked to Google.
  The third is what was asked for and what should have been obvious: a cover
  you own, and a click that goes where links go. No iframe, no third-party
  script, no request to anyone when a board opens. Downloading the video itself
  would need a platform downloader, which stays out of scope.

**4. Text nodes are plain text, styled well.** No markdown, no rich-text editor.
What a node gets instead is typography: font size (a scale, not free numbers),
weight, alignment and colour, per node, from the hover controls. `content_json`
holds `{ "text": "…" }`; presentation lives in `style_json`. This keeps nodes
short — which is what a mind map wants — and removes an editor dependency and a
week of schedule.

*Lists are the exception, added 2026-08-30.* They are structure, not markup, and
a mind map needs them. They live as ordinary leading markers in the same plain
string — `- item`, `1. item` — so nothing about the storage format changes and
v2 still hands an LLM readable prose. The editor continues a list when you press
Enter and ends it on an empty item; the canvas renders the markers as a real
`<ul>`/`<ol>`; the toolbar toggles a body between plain, bulleted and numbered.

**Node tones.** A colour carries as far as you want it to: `accent` (a bar on a
neutral card, the default), `tinted` (a wash), or `solid` (the whole card).
Stored as `style.tone`, applied through a `--node-color` custom property so one
colour drives the bar, the border, the title and the list markers together.

**5. Inter ships inside the package.** ~200 KB of woff2 as package data. Zero
network requests at runtime, and the board looks identical on every machine,
which matters when the whole design is a fixed dark aesthetic.

---

## 15. Launcher

The only non-canvas screen.

```
┌───────────────────────────────────────────────────────────┐
│  Mindboard                                    ＋ New   ⬆ Import │
│                                                           │
│  ┌─────────────────┐ ┌─────────────────┐ ┌──────────────┐ │
│  │ Nottingham DT   │ │ Thesis chapters │ │ Reading      │ │
│  │ sensor siting…  │ │ ch3 restructure │ │ VLM papers…  │ │
│  │ 12 Aug 2026     │ │ 27 Aug 2026     │ │ 30 Aug 2026  │ │
│  │        48 nodes │ │       112 nodes │ │     9 nodes  │ │
│  └─────────────────┘ └─────────────────┘ └──────────────┘ │
└───────────────────────────────────────────────────────────┘
```

Each card **draws the board's actual top-level graph** - the backend serves a
normalised miniature with every board listing - so a shelf of boards reads as a
shelf of distinct things rather than a column of identical rectangles. Below it:
name, description, a composition strip (how many text / image / video / group
nodes, colour-coded to match the canvas) and a relative time that keeps itself
honest without a reload.

The cards respond rather than sit still: a highlight tracks the cursor across
the card, the miniature's edges draw themselves in and its nodes breathe gently
on hover, cards stagger in on load, and pressing one springs. The same
miniature appears inside a collapsed meta-node, so a group also shows what is
in it. All of it is suppressed under `prefers-reduced-motion`.

Hovering a card reveals rename, export and delete. Clicking enters the canvas and the
launcher is gone until you ask for it (`Ctrl+K` → *Switch board*, or the
breadcrumb root). Same dark palette, same type scale — it reads as the same
application, not a separate shell.


---

## 16. Second pass on the canvas (2026-08-30)

Seven things asked for after living with v1 for a day. Recorded because
each one changed a decision, not just a line of code.

### Media belongs to the board, and the whole of it gets backed up

Anything dropped, pasted or uploaded is **copied into the app's own folder** —
`%APPDATA%\Mindboard\boards\<board>\assets\<xx>\<sha256>.<ext>` on Windows,
`~/Library/Application Support/Mindboard/...` on macOS, `mindboard where` prints
it anywhere. A node stores the hash and the original file name, never a path
into the user's disk, so moving or deleting the original cannot empty a card,
and a backup is complete by construction.

Two things were wrong and are now fixed:

- **`referenced_assets` matched a fixed list of keys** (`sha256`, `poster`). A
  link's cover picture lives under `thumb`, so covers were silently missing from
  every `.mindboard` bundle and every backup — and `mindboard gc` would have
  deleted them from disk while the node still showed them. It now scans
  `content_json` for anything shaped like a hash. Tests pin all three kinds.
- **Uploads were read whole into memory** (`await file.read()`). An hour of
  screen recording is gigabytes. `store_stream` now copies a megabyte at a time
  into a `.part` file beside its destination, hashing as it goes, and renames it
  into place only once it is whole — so a failed upload leaves nothing behind and
  a duplicate costs no second copy. The endpoint became a plain `def`, so it
  runs on a worker thread and a long upload does not stall the app.

`POST /boards/{id}/assets/copy` gives one board its own copy of another board's
file. Each board stays a complete thing: export it, delete every other board,
and it still shows everything.

### Copy and paste

`Ctrl+C` puts the **readable text** of the selected cards on the system
clipboard — paste into any other app and you get the words — while the shape of
those cards is kept in the tab (`sessionStorage`, so it survives opening another
board). A paste is recognised by matching the clipboard text against that copy,
which is what stops an unrelated clipboard from turning into cards.

What travels: the cards, the relationships whose **both** ends were copied, and
everything inside a copied group. Media travels as a hash, and if the paste
lands on a different board the file is copied there first. The whole paste is
one op, so one `Ctrl+Z` takes it back.

### Relationships read in words and are easy to remove

The mid-edge chip now says what the relationship is — *causes*, *is part of* —
next to its glyph, with any label the user typed after a middle dot. Colour and
shape alone were never enough, and are useless in a printed export.

Selection could not live in xyflow: the edges array is derived from the store
and re-supplied on every render, so the library's own edge selection was
overwritten immediately. Edge selection is ours (`ui.selectedEdges`); node
selection stays xyflow's, because node changes already flow back through
`onNodesChange`.

### The card toolbar is laid out, not wrapped

It was one flex row with dividers, wrapping wherever the width ran out — which
put the *default colour* ✕ next to the font size and left the bin on a row of
its own. It is now three deliberate rows separated by hairlines: **colour**
(swatches, default, the three tones), **text** (size, bullet and numbered
lists), **the card itself** (open/ungroup for a group, add-connected, duplicate,
and the bin pushed to the far right, away from everything else).

### Dragging pulls the graph with it

A mind map is held together by its relationships. Dragging a card pulls what it
is connected to along with it. The first version of this moved every neighbour
by a fixed fraction of the same vector — 55% at one hop, 26% at two — and was
replaced within the day; §18 has the reason and what took its place. Hold `Alt`
to move one card alone. Whatever moves lands in the same `node.move` op as the
card that pulled it, so one undo puts the whole arrangement back.


---

## 17. Third pass: the crash, and getting around the canvas (2026-08-30)

### One SQLite connection per board, per thread

`sqlite3.InterfaceError: bad parameter or other API misuse`, raised from
`board_meta`'s `SELECT * FROM board WHERE id=?` while opening a board with media
on it. Every DB endpoint in `app.py` is a plain `def`, which FastAPI runs on a
pool of worker threads, and `conn_for` handed all of them the **same**
`sqlite3.Connection` (`check_same_thread=False`). Two threads inside one
connection is exactly what sqlite reports as misuse; a board with several
videos fires enough parallel range requests to make it likely.

`_conns` is now keyed by `(thread id, data dir + board)`, with a lock around
creation so two threads never both run `migrate()`. WAL is what makes this
cheap: readers never block, one writer at a time, `busy_timeout` covers the
wait. `delete_board` closes every thread's connection to that board before
removing the folder — Windows will not delete an open file.

Honest note: this could not be reproduced on Linux, with either the test client
or a live server under sixteen threads. The reasoning is from the code, and it
is the only way that call can raise that error.

### Getting around

- **`a`** drops a card under the pointer. The canvas now always tracks the
  pointer in flow coordinates, not only while a link is open.
- **`c`** starts a connection from the selected card; so does a **link** button
  in its toolbar. Ports remain, but they are no longer the only way in — which
  is what made connecting feel fiddly.
- **Ports are 20px and sit outside the card.** An invisible 38px hit ring around
  each was tried first and reverted within the hour: it covered the top of every
  card, so pressing on a title to drag it started a connection instead. Verified
  by a Playwright drag that silently stopped moving the node.
- **The toolbar belongs to the selected card, not a hovered one.** Three rows of
  opaque panel opening on hover sat over whatever was above that card and ate
  clicks meant for it. Hovering still shows the ports. With more than one card
  selected every toolbar is hidden (`.stage.multi`) so the selection bar is the
  only thing acting on them.
- **A selection bar** appears over two or more selected cards: *n selected ·
  Group · Duplicate · bin*. Grouping was already there under `Ctrl+G` and in the
  palette, and was found by nobody.
- **Drag a card onto a group and it goes in**, the group lighting up as you
  cross it. The decision is made at drop time from the pointer's position, not
  from the last drag event — a quick flick can end before one more `onNodeDrag`
  fires. It is a `node.update` of `parent` and `pos`, so one undo takes it back
  out; **↑** in the toolbar does the same deliberately.

### A React trap worth remembering

Adding a multi-selection bar meant `Canvas` *read* `ui.selected` for the first
time. `onSelectionChange` wrote a fresh array on every call, which re-rendered
the canvas, which called it again: React tore the board down with "maximum
update depth exceeded" and left a blank screen. The handler is now a stable
`useCallback` that writes only when the selection actually differs.


---

## 18. The links are springs (2026-08-30)

The first version of elastic drag came back as *"move them other one change very
fake … they should be flowty and or organise in best position based on my
movement"*. He was right. Every neighbour moved by the same fraction of the same
vector, arriving in lockstep the instant the pointer moved — a rigid parallel
shift wearing the costume of a graph. Nothing in it knew what it was attached
to; a card holding four relationships travelled exactly as far as a card holding
one.

What replaced it is a small spring simulation, ~90 lines in `Canvas.tsx`,
running on `requestAnimationFrame` for the length of a drag and for a moment
after it.

- **Every link is a spring whose rest length is its length when the drag
  began.** Nothing pulls until something stretches, so picking a card up never
  quietly rearranges a board you had already arranged.
- **Cards have velocity and lose it to damping** (`SPRING = 0.2`,
  `DAMP = 0.82`). That is where the flow comes from: neighbours trail behind the
  pointer, overshoot very slightly, and settle — typically within a third of a
  second, capped at `SETTLE_MS`.
- **Short-range repulsion** shoves apart anything that would land on top of
  something else.
- **The dragged cards are pinned** to wherever xyflow has just put them, read
  fresh at the top of every frame.

The behaviour falls out of the model rather than being written into it: a card
on one link swings around to keep its distance, a card held by four barely
stirs, force travels along chains so a grandchild moves too, and a card with no
relationships does not move at all. Measured on a two-node board: hub dragged
364px, link length 500 before and 491 after — the spring returned to its rest
length, and the child swung 130px around it rather than sliding along behind.

The commit waits for the board to stop. `onNodeDragStop` no longer writes
positions; it hands the simulation a callback that runs when the motion dies,
which diffs every card against the store and writes one `node.move` op with
everything that actually moved. One undo, one arrangement.

---

## 19. Placing beats physics (2026-08-30)

§18 made the spring simulation the default for every drag. An hour later:
*"they are still like sticking on canves and i can't organize them as i want,
it's very hard"*.

Both halves of that are fair, and they are the same complaint. A card you drag
is a card you are **placing**; if its neighbours crowd in behind it, if the
board keeps drifting after you let go, and if a repulsion term shoves things
apart as you try to put them near each other, then the canvas is arguing with
you. A simulation is lovely to watch and hopeless to arrange things with.

So the default is now the plain thing: **dragging moves the card you grabbed,
exactly where you put it, and nothing else moves.** The springs are still
there — hold **`Alt`** and the whole model from §18 runs, settle and all. The
gesture that was "move one card alone" now means "bring the graph with you";
everything else about it is unchanged.

In place of physics, dragging got what arranging actually needs: **lining up**.
As a card comes within a few screen pixels of matching another — left, centre or
right across; top, middle or bottom down — it settles onto that line. Move a
little further and it lets go. Nothing is snapped to a grid: a board arranged by
eye should stay arranged by eye, and a grid is its own kind of stickiness.

This first drew a hairline through both cards to say what it had met, the way
design tools do. On seeing them: *"when i move nodes i see those two blue
line and they are very annoying"* — fairly, because when the card it matched was
across the board, so was the line. The lines are gone and the snap is silent;
the reach came down from eight pixels to six at the same time, so it takes hold
a little less eagerly.

Implementation note: the snap happens in `onNodesChange`, which is the only
place a position change can be adjusted before it lands, and it is skipped while
the simulation is running (lining up with one card is noise when the whole graph
is in motion). Verified by dragging one card's top edge onto another's: it
committed at exactly the same y, to the pixel, with the guide visible at the
moment of release.

A measurement worth keeping for future tests: xyflow spends the first ~10px of
pointer travel starting the drag, so a Playwright drag lands ten pixels short of
where the arithmetic says. That is the drag threshold doing its job, not a bug —
but it invalidated three test runs before it was spotted.

---

## 20. Two small things (2026-08-30)

### The console stopped shouting

Working on a board filled the launcher window with tracebacks:

```
Exception in callback _ProactorBasePipeTransport._call_connection_lost()
ConnectionResetError: [WinError 10054] An existing connection was forcibly
closed by the remote host
```

Nothing was wrong. A page that navigates, a video that seeks, a tab that
closes — each ends a request the server was still writing to, and Windows
reports that as 10054. asyncio's proactor transport raises it from inside
`_call_connection_lost`, which no caller can wrap, so it surfaces as an
unhandled-callback traceback. `runtime.quiet_disconnects()` wraps exactly that
method, on Windows only, swallowing `ConnectionResetError`,
`ConnectionAbortedError` and `BrokenPipeError` and letting everything else
through. It is idempotent and both `serve` and `serve_background` call it.

Honest limit: this cannot be reproduced on Linux, so the test exercises the
wrapper against a fake transport — a reset is swallowed, a `ValueError` still
escapes, and applying it twice does not stack wrappers.

### The keys are on screen

A `Shortcuts` panel sits in the bottom-left of every board: ten rows, key pills
and plain descriptions, folded away with a ✕ into a small **Keys** chip that
remembers its state in `localStorage`. It is hidden from image exports along
with the rest of the chrome.

It is not a menu — it acts on nothing, it only says what the canvas already
does — which keeps the original "no extra menus" rule intact.

One trap worth recording: the first version took the pointer. A 236×278 panel
with a background sitting over a corner of the canvas meant a card underneath it
could not be dragged, which a drag-into-group test caught immediately.
`pointer-events: none` on the panel, `auto` on its close button.
---

## 21. Pictures, full screen (v2, 2026-11-04)

A picture on a card is shown at card size, which is fine for a photo and
useless for a screenshot of a page: the text in it is too small to read. **A
click on the picture now opens it full screen** — the same rule link covers
already followed, where a click opens the thing itself.

The viewer fills the window on near-black, fitted but never blown up past the
picture's own pixels (a small picture enlarged to fill the screen only looks
worse). Scroll zooms around the pointer, dragging moves the picture,
double-click flips between fitted and actual pixels, `0` / `1` / `+` / `-` do the
same from the keyboard, **F** takes it to the whole screen, and `Esc`, the ✕, or
a click on the dark around it closes it. The file is the one the card already
loaded, so opening it fetches nothing. It is interface state in `ui.ts` — never
an op, never saved, nothing to undo — and lives in `Viewer.tsx`.

**What it cost.** Double-clicking the picture used to start writing on the card
(§8). It cannot do both: the first click of a double-click has already opened
the viewer. Writing on a picture card is now a double-click on its caption, or
`Enter` with the card selected.

### Traps, and what holds each one shut

- **The click is measured, not listened for.** xyflow suppresses the `click`
  after *any* pointer movement — a hand that wobbles by one pixel would never
  get the viewer. The picture records where the press began and opens on
  release if the pointer travelled less than `CLICK_SLOP` (6px), exactly as a
  port does. More than that is xyflow dragging the card, which still works from
  anywhere on the picture. A modifier click is selection, and a click while
  connecting is finishing the connection; neither opens anything.
- **Modal in fact, not just in looks.** The canvas listens for keys and the
  clipboard on `window`. With a card selected behind the viewer, `Delete` would
  remove it, `Esc` would also leave the group, `Ctrl+X` would cut it, `Ctrl+V`
  would paste onto the board, `Ctrl+E` would export. The viewer stops
  `keydown`, `keyup`, `copy`, `cut` and `paste` in the capture phase at
  `window`, before any other listener sees them. One guard in the one place
  that knows a modal is open, rather than a check in every handler that might
  one day be written.
- **Mounted outside the stage.** The viewer is a sibling of the canvas, not a
  child, so its pointer, double-click and drop events never bubble into the
  canvas's own handlers. It also cancels drops itself — otherwise a file dropped
  on it would make the browser navigate away to the file.
- **The second click of a double-click lands on the viewer.** Left alone it
  would close it (a click on the dark) or zoom it (a double-click on the
  picture). For half a second after opening the viewer ignores both — the same
  rule, and the same 500ms, as a link cover.
- **Pointer capture moves every click to the root.** Dragging holds pointer
  capture, so the browser reports clicks and double-clicks as landing on the
  viewer itself, never on the picture. "Was that on the picture?" is answered
  from geometry, not from `event.target`.
- **Sized, not scaled.** The picture is zoomed by setting its width and height
  and moved with `translate`. A CSS `scale()` lets the browser raster the
  picture once and stretch the result, which blurs exactly when you zoom in to
  read. From 300% up it renders as pixels rather than as a smear.
- **The wheel is a native listener.** React's wheel handler is passive, so it
  cannot `preventDefault`, and a trackpad pinch (which arrives as ctrl+wheel)
  would zoom the whole page instead of the picture.

### Verified

In a browser against a scratch board (a 2400×1600 page of small text, a
260×170 picture, a picture inside a group): click opens fitted at 39%; the
small picture opens at exactly 100%; scrolling zooms around the pointer;
double-click goes to actual pixels at the point clicked and back; drag pans
without closing; a click on the dark closes. With the card selected behind
the viewer, `Delete`, `a`, `Ctrl+E`, a `cut` and a `paste` changed nothing —
same node count, same board revision. `Esc` inside a group closed the viewer
and stayed in the group. Connecting by clicking a picture made the edge and
opened nothing; dragging a card by its picture moved it and opened nothing; a
double-click on the picture opened the viewer once and left it open.

**Not verified:** true full screen. The automated browser's input never counts
as a user gesture, and the Fullscreen API refuses anything else. It is the
standard API and a real key press qualifies, but that check was left to a
person.

---

## 22. A PDF opens in your own reader (v2, 2026-11-04)

Dropping a PDF already made a `file` card holding the board's own copy (§16).
What it could not do was the obvious thing: **a click on a PDF card now opens it
in the PDF reader this computer already uses.** The card's top became a
*document face* — a turned-page badge with the file's kind, its name, and
"Opens in your PDF reader" — and that face takes the click, by the same
pointer-travel rule as a picture (`useClickToOpen` in `nodes.tsx`, shared by
both).

**The server opens it, because the browser cannot.** No web page may start an
app. Mindboard's server can, because it runs on the same machine as the person
using it: `POST /api/boards/{id}/assets/{sha}/open` hands the file to the
system's own opener — `os.startfile` on Windows, `open` / `xdg-open` elsewhere —
the same path the launcher's *Open the data folder* has always used
(`runtime._launch`). Nothing leaves the machine, and it is not an op: opening a
file changes nothing on the board.

**Linking the original was never possible.** An earlier plan offered to point
at the file where it lives on disk, so a reader's annotations would land in the
person's own copy. A browser never tells a page where a dropped file came from
— a deliberate privacy rule — so a board can only ever hold its own copy, which
is what §16 wanted anyway.

### A read-only copy, never the board's file

The board's file is named by its hash. A reader that saved annotations into it
would make that name a lie: the bundle checksum would fail on import, and every
card sharing those bytes would change at once. So the reader gets a copy:

```
<data dir>/opened/<board>/<first 16 of the hash>/<original name>.pdf
```

- **Its real name**, so the reader's title bar says `Siting report.pdf`, not 64
  hex characters. The hash in the folder keeps two different `notes.pdf` apart.
- **Read-only**, so a reader asks for *Save as* rather than writing over it.
  Notes made in a reader therefore stay out of the board for now; bringing
  them back as a new version of the card (one undoable op) was considered and
  left for a later step.
- **Outside the board folders.** A reader holding a file open stops Windows
  deleting it; under the board folder, that would stop the *board* being
  deleted. Copies are regenerable: `mindboard gc` clears them, deleting a board
  clears its own, and either skips any a reader still has open.
- **Made once, then reused**, copied beside its destination and renamed into
  place, so a reader never opens half a file and two quick clicks never write
  the same temporary file.

### What will and will not be handed over

Only types listed in `assets.OPENABLE` — today just PDF — and three checks have
to agree:

1. **The type sniffed at upload.** A picture or a CSV is refused (415).
2. **The file's own first bytes.** Unknown bytes fall back to the declared type
   at upload, so a program renamed `invoice.pdf` *is* stored as a PDF. It does
   not begin with `%PDF-`, and that is what decides.
3. **The extension is ours.** Windows chooses what to run by extension alone, so
   the copy's name ends in the extension from `OPENABLE`, never whatever the
   upload claimed. Whatever reaches the opener can only reach a PDF reader.

The file is found by hash in the board's own records; the request never carries
a path. Names are cleaned for every OS — separators of both kinds, the
characters Windows refuses, reserved names like `CON` and `LPT1`, and anything
past 120 characters. With no PDF app installed the answer is a plain "no app on
this computer opens PDF files" (409), not a traceback.

New file cards also carry their `mime` in `content`, so the card knows a PDF
without guessing; older cards fall back to the `.pdf` on their name, and files
fetched from a pasted link now get a `filename` as well.

### Verified

`tests/test_open.py`, 17 cases, with the system opener replaced so the suite
never opens a window: the copy has the real name, identical bytes, is read-only
and lives outside the board, whose own file keeps its hash and stays writable;
reopening reuses the read-only copy; two files called `notes.pdf` stay apart;
a PNG and a program dressed as a PDF are refused and nothing is launched; an
unknown file, another board's file, a missing board and a missing key are
refused; no PDF app gives the plain message; names are cleaned; `gc` clears
copies and keeps the board's file; deleting a board takes its copies.

In a browser, against a server whose opener only logged what it was given: a
click on a PDF card handed over one read-only `Siting report.pdf` and said so;
a card made before this change (no `mime`) opened the same way; a double-click
opened it once and did not start editing; a CSV card showed its badge and
opened nothing; dragging a card by its face moved it and opened nothing;
connecting to a PDF card made the edge and opened nothing; the caption still
takes a double-click to write. The picture viewer still opens after the click
handling became shared.

**Not verified here:** an actual reader window. That needs the real opener on a
real desktop — including whether Windows lets the reader come to the front, or
opens it behind the browser.

---

## 23. A model on this computer (v2, 2026-11-04)

Ask your board answers from the person's own cards — text and pictures both —
and points out what their thinking is missing. It needs a language model, and
the rule that nothing leaves the machine (§1) means that model runs here. This
step installs it and proves it answers; nothing on the canvas uses it yet.

### Which model: the largest that fits the computer it is on

Asked for first: "Qwen3 9B". There is no Qwen3 9B (Qwen3 has an 8B; the 9B is
Qwen3.5-9B), and by now Qwen has shipped Qwen3.6 and Qwen3.8. Then: the best
quality possible. And then the correction that settled it — *anyone* will use
this, not one laptop. A single model sized for a 16 GB card is right for very
few computers and miserable on the rest.

The rule that decides it: **a model held whole in graphics memory is fast; one
that spills into system RAM reads its spilled layers at RAM speed**, several
times slower. A smaller model that fits beats a larger one that does not. So
Mindboard pins a few sizes and installs the largest that fits:

| Size | Model | Download | For |
|---|---|---|---|
| full | Qwen3.5-9B, Q8_0 | 10.4 GB | a 12 GB card or more; a Mac with 24 GB or more |
| compact | Qwen3.5-9B, Q4_K_M | 6.6 GB | an 8 GB card; a 16 GB Mac |
| light | Qwen3.5-4B, Q4_K_M | 3.4 GB | everything else, graphics card or not |

All are one family — one architecture, one chat template, all of them read
pictures — so everything built on top behaves the same on each; only depth and
speed differ. All Apache-2.0. Thresholds sit just under each class of card as
its driver reports it: a "12 GB" card shows about 11.9 GiB.

**Reversed: the 27B.** The first top size was Qwen3.8-27B (IQ4_XS, 15.2 GB), on
the argument that finding what a map is *missing* leans on what the model
itself knows, where size pays. In use it was far too slow to think with —
minutes before a first word on a whole board (§27) — and the decision was
plain: **Qwen3.5 9B, not 27B.** Speed is what makes asking your board worth
doing; the 27B is no longer offered at all, and a test holds that.

**Sizing happens before anything downloads**, so the person sees the choice and
why: NVIDIA cards through `nvidia-smi`; other cards on Windows through the
registry's `qwMemorySize` (WMI's `AdapterRAM` cannot count past 4 GB); AMD on
Linux through sysfs; a Mac as two thirds of its memory, which is what macOS lets
its graphics use. Anything under 2 GB is a processor's built-in graphics
borrowing system memory, and counts as no card. If the guess is off, llama.cpp's
`--fit` still places what fits; the model only runs slower than it could.
`--model <size>` overrides the choice.

Turned down along the way: Q6_K of the 27B (22 GB — slightly better answers at
about a quarter of the speed on a 16 GB card); Qwen3.8-Flash-Next (180B, not
Apache, fits nothing ordinary); Qwen3.6-35B-A3B (fast, but older, with 3B
parameters active per token). The files are unsloth's community GGUFs — Qwen
publishes none for these — so each is pinned by commit and checksum rather than
trusted by name.

**The card may be shared.** Other programs use the same graphics memory. The
model still loads, slowly; so before loading, `memory_shortfall` compares free
graphics memory with what this size needs and says so in words — *"Mindboard's
model needs 10.4 GB of graphics memory and only 3.6 GB is free, so answers will
be much slower. Free up the graphics card for full speed."* It speaks of memory
only. An earlier version named the programs holding the card, and read as if
Mindboard depended on them; Mindboard runs its own llama.cpp and relies on
nothing else, so it names nothing else.

### Installing: pinned, verified, resumable, never automatic

`llm.py` names every file: one llama.cpp build (`b12102`) per kind of computer,
the model, and its vision part, each with exact URL, byte size and SHA-256. The
model is fetched from a fixed commit of its repository, never `main`; llama.cpp
from a fixed build, never `latest`. Updating either is a change to that file.

- **Nothing downloads unless asked.** Only `mindboard ai install` or menu
  option 8 does, and both list what will come down, from where, and ask first.
  Opening a board never touches the network.
- **Hashed as it arrives, renamed only once it checks out.** A file goes to
  `<name>.part`; a mismatch is deleted, not kept. A cut-off download is kept
  and resumed with an HTTP `Range` request; if a server ignores the range, the
  file starts over rather than being stitched together wrong.
- **Checked once, not every start.** A `.sha256` mark records that a file was
  verified, so a 14 GB model is not re-hashed each time it loads.
- **Its own folder, beside the install.** `.llm/` in a source checkout
  (gitignored), the data folder for an installed package, `MINDBOARD_LLM_DIR`
  over both. Not among the boards: backups never carry 15 GB of model.
- **The right build for the hardware.** CUDA for an NVIDIA card — its runtime
  ships in a second archive and is unpacked beside the server, so nothing is
  installed system-wide — Metal on Apple silicon, Vulkan for any other card,
  the processor otherwise. Only the CUDA path has run for real.

### Running: a separate process, on a short lead

llama-server is its own process, not a library inside Mindboard: a model that
runs out of memory takes down that process, not the app and its boards.

- **127.0.0.1, a free port, a fresh key every start.** Localhost is not a trust
  boundary (§6), so the server demands a key no other process can know. Its
  built-in chat page and its slot monitor are switched off.
- **Loaded when first needed, unloaded after 20 idle minutes**, so the card's
  memory is not held all day for a question asked at breakfast.
- **It dies with Mindboard, however Mindboard dies.** This is the trap: kill
  Mindboard outright — a crash, the launcher window closed with its ✕ — and a
  child process holding 15 GB of graphics memory would run on, owned by
  nobody. On Windows the server joins a job object marked kill-on-close; the
  handle is held by Mindboard alone, so when Mindboard goes, for any reason,
  Windows takes the server with it. On Linux the child asks for SIGTERM on its
  parent's death. A test kills a process holding a server outright and checks
  the server is gone; run with the job object switched off, the same test
  leaves the server running — so it tests the fix, not just itself.
- **Layers are left to `--fit`.** llama.cpp places as many layers on the card as
  fit beside the chosen context (32k tokens, KV cache in q8_0) and only adjusts
  what is not set by hand — so `--n-gpu-layers` is deliberately not passed.
- **The vision part runs on the processor.** On the card its ~0.9 GB would push
  layers of the model itself off it, which slows every answer; on the
  processor it slows only the reading of a picture.

A trap in the tests, recorded because it nearly gave a false pass: on Windows a
venv's `python.exe` is a launcher that starts the real interpreter as a second
process. Killing "the process" in a test then kills the launcher, and a test
about orphaned children measures the wrong child. The crash test runs on the
base interpreter so that each piece is one process, as llama-server is.

### Verified

`tests/test_llm.py`, 37 cases, none of which downloads anything or loads a
model — downloads run through a fake transport, and llama-server is played by
`tests/fake_llama_server.py`, which takes the same flags and answers the same
endpoints. Pinned files are exact and never float; sizes run largest first and
each fits the card it is for; folders never hold two different files of one
name; the size chosen for nine typical computers (16, 24, 12, 8 and 6 GB cards,
none, and 32, 16 and 8 GB Macs); the biggest of several NVIDIA cards is read;
built-in graphics do not count; a fresh download is checked and marked; a
partial one resumes with `Range`; a server that ignores the range starts the
file over; a wrong file is deleted; an archive cannot write outside its folder;
a full disk is refused before anything starts; the CUDA runtime lands beside the
server; the server listens on 127.0.0.1 only, demands its key, has a new key
each start, explains a failed load, unloads when idle, and dies when the process
holding it is killed outright; the memory warning reports free memory; the
setup screen draws for every size, with and without a card.

That last test exists because the screen crashed the first time it was drawn
for real: a closing markup tag written without its `f` prefix, on exactly the
path a first-time person takes. Nothing had drawn that screen with downloads
still to do.

On the machine this was built on (RTX 5080 Laptop, 16 GB): `large` chosen; 15.8
GB downloaded in 383 s with every checksum matching and the archives removed
afterwards; the model loads in 12 s, answers, and reads the test picture — *"The
word "MINDBOARD" is written next to a red circle"* — in 1.6 s.

**A lesson about laptops, recorded because it looks like a Mindboard problem.**
The first check measured 11–13 tokens a second, a fifth of what the card
should manage. llama.cpp's own benchmark with every layer forced onto the card
gave the same 11.3, so nothing had been placed on the processor. Sampling the
card while it worked showed why: 55 W, the cores at 320–390 MHz of a possible
3,090, the driver reporting a software power cap. The laptop was in its maker's
*Silent* profile. A laptop's power mode can hold its graphics card to a fraction
of its speed, and that is the person's setting, not Mindboard's to change.

**Not verified:** speed at full power on that machine (the card was busy with
another program when the profile changed, and a measurement beside it would
have measured the competition); the Vulkan, Metal and processor builds; the
smaller sizes in use; sizing on AMD, Intel and Apple hardware.

---

## 24. Asking about exactly these cards (v2, 2026-11-07)

The shape of Ask your board was set by the person who will use it: every card
gets **Add to context**, questions are about exactly the cards added — text and
pictures — and an answer can be saved back as a card, connected where they
choose. An earlier plan let the scope follow the selection; it was dropped for
this, which is deliberate and stays put while you click around. This step is
the part under the canvas: `ask.py` and `POST /api/boards/{id}/ask`. The chat
panel comes next.

### What the model is shown

**Exactly the cards in context** — not their neighbours: "exact context" was the
request. A **group brings everything inside it**, at any depth (asked, and
answered yes): asking about a group means asking about what it holds. Each card
is written out with its id, its kind and the groups it sits in; links bring
their address and excerpt, files their name, every card its note. **Pictures go
in as pictures** — the image itself, shrunk to 1,024 px and labelled with its
card — and a video by its cover frame. **Relationships between cards in
context** go in verbatim, kind and label, because they are the person's own
claims about how ideas connect.

The context travels with the *newest* question, so it is always the cards in
context now; earlier turns keep only what was said. History can only carry
user and assistant turns — it cannot smuggle in a system message.

### Two voices

The rules the model answers by keep the person's map and the model's knowledge
apart. What comes from a card is cited straight after, `[[card id]]`. Anything
else — missing ideas, gaps, corrections, what to read — goes after a line that
reads exactly **Beyond your board:**, uncited, because it can be wrong. If the
cards do not answer the question, it says so first.

Every citation is checked against the cards in context. One naming any other
card is removed and **counted** — that count is the measure of how often the
model invents a source.

### The route

`POST /api/boards/{id}/ask` with `{question, cards, history, think}` answers in
server-sent events: `context` (what went in, and an estimate of its size),
`thinking` when asked to think deeper, `text` as the words come, then `done`
with the checked answer split into its two voices. Everything that can be known
before the model starts is answered with a status, not a broken stream: no
such board 404, no cards 400, too much to fit 413, still answering 409, not set
up 503 with how to set it up.

- **Too much is refused, not trimmed.** The model is started with 32k tokens;
  if the cards, pictures and history would not leave room for an answer
  (3k tokens, plus 8k when thinking), the person is told the size and asked to
  take cards out — never handed an answer from a context silently cut short.
- **One question at a time**, as the server runs one slot.
- **Walking away stops the model.** Closing the answer closes the connection to
  llama-server, which stops writing an answer nobody will read; the next
  question is not told it is busy.
- **It only reads.** Nothing here writes to the board. Saving an answer as a card
  will be an op with `provenance: "ai"`, undoable like any other.

`mindboard ai ask --board … --card … "question"` asks the same way from a
terminal, finding boards and cards by title — the tool this step was verified
with, and one a script (the n8n prototype of `docs/agent-n8n.md`) can use.

### Two bugs the tests found

- **A key that looked like a flag.** The per-start key came from
  `token_urlsafe`, which can begin with `-`; `--api-key -abc…` is then read as a
  flag. One start in 64 — it broke one test run in four once there were enough
  starts. Keys are now hex. A test makes 200 keys and checks none could pass as
  a flag.
- **A stand-in that hung up too early.** The fake llama-server refused an
  unkeyed request before reading its body; on Windows the client then saw
  "connection aborted" now and again. llama-server reads first, and now so
  does the stand-in.

### Verified

`tests/test_ask.py`, 15 cases against the stand-in: a group brings everything
inside it, in order, once; only chosen cards and the relationships between them
go in; every kind of card says what it holds and where it sits; pictures go in
shrunk and labelled, a video by its cover; an empty context is refused; the
context travels with the newest question and history cannot add a system
message; too much is refused up front; invented citations are removed and
counted; the route streams `context`, `text` and a checked `done`; thinking is
shown; each refusal has its status; one question at a time; not set up says
how; walking away stops the stand-in mid-answer and frees the model. The whole
suite ran six times in a row clean.

Against the real model (Qwen3.8 27B, with another program holding 6 GB of the
card, so slowly — 2–4 tokens a second, as the memory warning predicted): asked where
the lidar should go, with *Sensors*, *Corner mount* and *Site sketch* in
context, it cited the corner-mount card, the Lidar card from inside the group,
and the sketch — and read *"LIDAR HERE 4.2 m"* above pole 3 from the picture
itself. Asked what the plan is missing, it named three gaps grounded in cited
cards (no lidar cost, an unknown number of junctions, nothing for installation)
and put its own suggestions after *Beyond your board:*. No citation had to be
removed in either answer. One soft spot: a cited point also carried general
knowledge ("typically required") — the line between the voices holds, but not
perfectly.

---

## 25. The panel, and the Stop that did not stop (v2, 2026-11-07)

### On the canvas

Every card's toolbar gets a bubble: **Add to context**, pressed again to take
the card out. The bar over a multiple selection gets **Ask**, which adds them
all. A card in context carries a small bubble in its corner and a faint accent
ring, so what the model will read is visible on the board itself; a group's
entry in the panel says how many cards it brings (*Sensors · 3 cards*).

The panel sits on the right, opened by adding the first card, by the bubble in
the dock, or by `Ctrl+J`; `Esc` or ✕ folds it away. Top: the cards in context.
Middle: the conversation — every `[[card]]` in an answer becomes a chip with
the card's title that goes to the card, entering its group if it has to; the
model's own part sits under *Beyond your board — the model's own knowledge*,
dashed and dimmer; removed citations are counted under the answer. Bottom: the
question box (Enter sends), **What am I missing here?** as a first question,
**Think deeper**, and **Stop**. Before the first words it says what it is doing
— reading the cards, loading the model, thinking — and a memory warning, when
there is one, sits above the answer it explains.

An answer is never HTML: only `**bold**`, line breaks and citations are
interpreted, so nothing a model writes can become markup on the page.

### Screen state, not the board's

The context and the conversation are never ops and never saved with the board
(asked, and answered: only while open). They live in `askStore.ts`, mirrored to
`sessionStorage` per board, so a refresh keeps them and closing the tab ends
them. What is worth keeping becomes a card — the next step — so the map stays
the record, not a chat log beside it.

Typing in the panel never reaches the canvas: its keys go to a text box, which
the canvas already ignores. `Esc` from anywhere in the panel closes the panel
and stops there — it must not also walk the canvas out of the group it shows.

### The Stop that did not stop

The first Stop looked right and was not. In the browser the answer halted and
said *Stopped*; on the server the model wrote on — and the next question was
told *still answering the last question*, for good.

The cause was two facts together. **This server never learns that a browser
hung up**: uvicorn, with nothing left to read from the connection, keeps
sending into it, and the send waits forever once nobody reads. And **the answer
was pulled by that send**: each piece of the model's output was fetched only
when the previous one had gone out. So the stuck send froze the model's stream
mid-answer, inside the one slot, holding it. Shielding the clean-up from
cancellation, and asking `is_disconnected()` between pieces, both changed
nothing — a trace showed the endpoint simply parked inside the send.

Two changes made it right, and both stay:

- **The answer is made on its own thread** and handed over through a queue. The
  response can stall without stalling the model; an answer nobody is reading
  runs to its end and frees the slot.
- **Stop is said, not implied.** The panel's Stop calls
  `POST /api/boards/{id}/ask/stop` and then hangs up. The model checks between
  pieces, hangs up on llama-server, which stops generating, and the slot is
  free at once. Where a server does hear a hang-up, the same stop happens on
  its own.

The tests drive a real server, because the test client cannot hang up the way
a browser does: Stop frees the model straight away — the stand-in logs being
hung up on, and the next question is answered in seconds — and a hang-up nobody
hears no longer stalls anything: the answer finishes and the next question is
answered. The second test is the bug exactly as it was seen.

### Verified

In a browser against a server whose model was the stand-in: the toolbar bubble
adds a card and opens the panel, and the card carries its mark; a drag-selected
set added from **Ask** shows *Sensors · 3 cards* and does not list a card twice;
a typed question with `a` and `c` in it created no cards; the answer showed a
citation chip, the memory warning, the removed-citation count and the
beyond-the-board part; clicking a citation of a card inside a group opened the
group; `Esc` in the panel closed only the panel, inside a group; `Ctrl+J`
brought it back with the conversation; a refresh kept the context and all ten
turns; **Stop**, clicked, halted the answer, the stand-in logged being hung up
on, and the next question was answered. 103 tests, three runs clean.

Two things about the test tooling, so the next session does not lose time to
them: in the browser pane a Shift-click does not add to a selection — xyflow
listens for the Shift key itself, not the flag on the click — so multiple
selection was made with a drag box; and the pane's frame changes shape when the
window does, so click positions taken from the page must be scaled by its
current width, not an earlier one.

**Not verified:** the panel against the real model (the card was held by LM
Studio throughout; the real model answered the same route in §24); the
not-set-up and too-much messages in the panel itself (the route's answers are
tested; the panel shows whatever reason it is given).

---

## 26. "Reinstall / update" deleted the program it was running from (2026-11-07)

Choosing **7 Reinstall / update** in the launcher menu left Mindboard unable to
start: *"Mindboard installed but will not run"*. Three faults lined up.

1. **The menu ran the installer from inside the environment it updates.** The
   menu is `.venv\Scripts\mindboard.exe`; Windows will not delete or replace a
   program that is running. The installer got halfway — `pyvenv.cfg` and part
   of `Lib` gone, `mindboard.exe` locked in place — and stopped.
2. **The installer replaced instead of updating.** It ran `uv venv .venv` every
   time. Older uv reused an existing environment; newer uv asks whether to
   replace it, and in a script answers *yes* by itself.
3. **The launcher only checked that `mindboard.exe` existed.** A half-deleted
   environment still has it, so double-clicking would have run a broken program
   rather than repair it.

And the fixes, one per fault:

- **Option 7 on Windows hands over.** The installer opens in its own window and
  the menu closes, so nothing runs from `.venv` while it works. (On macOS and
  Linux a running program can be replaced, so it still runs in place.)
- **A working environment is updated, never replaced** — the package is
  reinstalled into it. Only a missing or broken one is rebuilt, and then with
  `--clear`, which says what is meant instead of letting uv ask. Before
  touching anything the Windows installer waits up to 30 seconds for programs
  running from `.venv` to close, then says plainly that Mindboard is still
  open rather than half-delete it.
- **The launchers check that Mindboard runs** (`mindboard version`), not that a
  file exists, and set it up again if it does not.

Verified on the machine where it broke: the fixed installer rebuilt the
half-deleted environment (the broken remains could not be used, and its
rebuild-from-scratch fallback took over); run again on the working
environment it updated in place and left `pyvenv.cfg` as it was; with a
program running from `.venv` it waited, then updated once it closed; with one
that never closed it gave up after 30 seconds with that message and left the
environment working. Boards and the downloaded model were never touched — they
live outside `.venv`. 103 tests pass on the rebuilt environment.

Not verified: option 7's hand-over itself (it opens a window on the person's
screen; the installer's waiting, which it relies on, is verified above).

---

## 27. The first real question (2026-11-07)

The first question asked on a real board — twelve cards, some with pictures,
*"what we know about design patterns?"* — sat on *"Loading the model…"* and
looked dead. Read from the server's log, it was not dead; it was slow, and the
panel was saying the wrong thing:

- The model had loaded in 13 s. It was then **reading 13,700 tokens at 60–70 a
  second** — 3½ minutes — because another program had taken part of the graphics
  card ten seconds after Mindboard's model started, and `--fit` moved layers into
  system RAM. The panel called all of it "loading".
- Every follow-up would have been as slow: the cards travelled *after* the
  conversation, so the start of the prompt changed each turn and llama-server
  could reuse none of what it had read.
- The memory warning named the other program, and read as if Mindboard ran on
  it — when Mindboard runs its own llama.cpp and the other program was only
  sharing the card.

And one request on top: with nothing added, a question should be about the
**whole board**.

What changed:

- **Cards first.** System, then the cards (and their pictures), then a short
  "I have read these cards", then the conversation, then the new question. The
  start is identical from one question to the next, so llama-server reads only
  what is new. A test holds the first three messages equal across turns.
- **The panel says what is happening.** llama-server is asked for
  `return_progress`; the panel shows *Starting the model* only while it starts,
  then *Reading your cards — 45% of 13,700 tokens*, noting how much was already
  read before; without progress it still gives the size.
- **The warning speaks of memory only**, and names no other program: *Mindboard's
  model needs 10.4 GB of graphics memory and only 3.6 GB is free, so answers will
  be much slower. Free up the graphics card for full speed.*
- **Nothing added means the whole board** — every card, groups' contents and
  relationships included, shown as *Whole board · N cards*; adding cards
  narrows it. Pictures come with the whole board only up to 6,000 tokens'
  worth, so a board of screenshots stays askable; a left-out picture's card
  still goes in by its title and caption, the panel says how many were left
  out, and cards chosen by hand always bring their pictures.

106 tests. **Not yet verified against the real model:** the progress fields
(`total`, `cache`, `processed` — the build carries `prompt_progress` and
`processed`; the panel falls back to the size if they differ) and the reuse of
the cards between questions; both show in the server log on the next real
question.

---

## 28. Qwen3.5 9B, and the picture that was on the wrong chip (2026-11-07)

Decided: **Mindboard runs Qwen3.5 9B through its own llama.cpp, and relies on
nothing else.** The 27B is gone from the list (§23), and the memory warning
names no other program (§23).

Measured on the machine it is built on (RTX 5080 Laptop, 16 GB), the 9B at
Q8_0, entirely on the card: **5,400 tokens a second reading, 80 writing**
(llama.cpp's benchmark), loaded in 5 s, 8.9 GB of graphics memory. The 27B had
been reading a whole board at 60–70.

Then a whole-board question on a real board — *RAG Deep Knowledge*, 135 cards,
43 pictures, of which the whole-board budget takes 11 — ran at 695 tokens a
second, not 5,400. The pictures: the vision part had been put on the processor,
a choice made when the 27B left the card no room. With the 9B there is room, so
`vision_fits()` now places it at each start — on the GPU when free memory holds
the model, the vision part and a margin, on the processor when it does not (and
on a Mac always, the memory being one). The same question:

| | vision on processor | vision on GPU |
|---|---|---|
| reading 20,094 tokens | 29 s (695/s) | 5.9 s (3,415/s) |
| first words | ~30 s | 6.1 s |
| a follow-up's first words | — | **0.5 s** (862 new tokens read; the rest reused) |

The follow-up figure is §27's cards-first order at work: llama-server read
only the new turn. The answers themselves were good — nineteen citations on the
first, every one a real card, none removed — and told on themselves usefully:
twice the model said the details were *"in the associated image"*, of cards
whose pictures the whole-board budget had left out. That is the case for what
comes next: pictures described once, when they are added, so a question can
find what is in them without reading every image every time.

`mindboard ai ask --all` had sent every card by hand, which skips that budget;
it now asks the whole board exactly as the panel does.

---

## 29. GraphRAG: searching a board too big to send (2026-11-07)

Sending the whole board works while the board is small. It stops working twice
over as a board grows: the prompt outgrows what the model can read in one go,
and the pictures stop fitting — on a 135-card board with 43 pictures, 32 were
left out and the model said so (*"the details are in the associated image"*).
So a board too big to send is **searched**, and only what the question is about
goes to the model (`retrieve.py`).

### The strategy

1. **Small boards are still read whole.** Under 4,000 tokens of card text
   (pictures come on top, within their own budget) nothing is searched: the
   most complete answer is the one that sees everything, and at this size it
   is also fast. Cards added to the context by hand are never searched either
   — they are exactly what was asked about.
2. **Two searches, fused.** *By meaning*: every card is embedded — title,
   where it sits, its words — with Qwen3-Embedding 0.6B (Qwen's own GGUF,
   0.64 GB, pinned like everything else), run by Mindboard's llama.cpp as a
   second, small server. The question is embedded with Qwen's retrieval
   instruction and compared by cosine. *By wording*: the full-text index the
   app already keeps (FTS5, BM25). The two rankings are merged by reciprocal
   rank fusion — no scores to calibrate between them, and a card near the top
   of both rises most.
3. **A walk along the person's own links.** From the best ten cards, one step
   along their relationships in either direction, and from a matching group
   into its cards. A neighbour scores half the card that led to it, nudged up
   if it matches too — so *Ragas*, which shares no words with "how do I check
   retrieval quality?", arrives because the person linked it to the card that
   does. This is the graph in GraphRAG, and the graph is theirs: typed claims
   about what connects, not edges a model guessed.
4. **A budget, then the outline.** The strongest cards are taken in order
   until 5,000 tokens are spent, their pictures within the picture budget, the
   relationships among them, and an outline of the board's groups (each with
   its count of cards) so a broad question still sees the board's shape.
5. **Follow-ups are searched with what led up to them.** The search text is
   the last two questions and the new one — "which of those are open source?"
   alone would find nothing.

### The index

Card vectors live in `node_embedding`, the table v1 reserved for exactly this
(§11), with one column added: the hash of the text each vector was made from.
Before a search, cards that are new, edited or embedded by another model are
embedded again — the rest never are; a deleted card's row goes with it. It is a
derived index like the full-text one: regenerable, never exported, never an op.
Without the embedding model — not installed, or not answering — search falls
back to wording alone rather than fail, and the panel says which it used.

### Measured

On *RAG Deep Knowledge* (135 cards): the embedding model loads and embeds the
whole board in under 2 s the first time, 0.6 s for 135 cards; a question about
open-source evaluation tools went to the model with 42 of the 135 cards
instead of all of them. Found on the way: the "send it whole" limit first
counted only text (8,000 tokens), so this board — 7,100 tokens of text, plus
pictures — was always sent whole; it is now 4,000.

`tests/test_retrieve.py`, 15 cases with a stand-in embedder: what a card is
embedded as; only new, edited or re-modelled cards are embedded; a deleted card
takes its vector; the best match comes first; a linked card with no words in
common comes in above unrelated ones; a matching group brings its cards; the
budget holds; no embedder, or a broken one, means wording alone; the outline;
a small board is read whole, a big one searched; a follow-up is searched with
the question before it; chosen cards are never searched.

### Two fixes from using it

- **Going to a card did nothing for a card off screen.** It used `fitView` on
  the card; the canvas renders only cards in view, and xyflow can only fit a
  card it has measured. It now centres the card from the board's own data,
  zoomed so it reads, clear of the Ask panel, ringed twice so the eye finds
  it. Verified with every card panned off screen: the cited card came to the
  middle of the space beside the panel at 115%.
- **An answer being written pinned the panel to the bottom.** It now follows
  only while the reader is at the bottom; scrolling up lets go, scrolling back
  down picks it up, a new question comes back down. Verified mid-answer: at the
  top it stayed at the top while the answer kept coming.

**Not yet:** pictures described once when added, so what is *in* a picture can
be searched — the next step.

---

## 30. A real search engine, and measuring instead of guessing (2026-11-07)

Asked for: something more capable than a SQLite table for search; exact
search, reranking and RRF beside vector search; and answers to complex,
multi-hop questions that need planning before retrieval. Planned as three
steps, each measured: (1) the search engine and a way to measure it, (2) a
reranker and graph ranking, (3) planning and multi-hop retrieval. This is (1).

### The engine

Chosen after checking what is local, embedded and silent:

- **Tantivy** (0.27, MIT, a 4 MB wheel with no dependencies) — a Rust search
  engine in the Lucene family. It brings what the FTS5 table did badly:
  English stemming ("evaluating" finds "evaluation"), phrase queries, per-field
  boosts and typo-tolerant terms.
- **Exact vector search with numpy.** At mind-map scale — hundreds to a few
  thousand cards — comparing a question with every card is about a millisecond
  and loses nothing. An approximate index pays only at millions of items, and
  pays in accuracy.
- Turned down: **LanceDB** — an 86 MB wheel plus pyarrow, and no way to be sure
  from here that it sends nothing anywhere.

`search_index.py` keeps one index per board in `<data>/search/<board>/`: a
Tantivy index of three fields — **title** (boosted ×2), **body** (the card's
note, a link's address and excerpt, a file's name) and **path** (its groups,
×0.5) — and `vectors.npz` with each card's vector beside a hash of the passage
it was made from. SQLite stays the record; the index is derived from it,
re-synced whenever the board's revision moves on (text in full —
milliseconds; vectors only for passages that changed, or all of them for a new
model), never exported, never written by a feature. It lives outside the board
folder for the reason `opened/` does: an index Tantivy holds open must never
stop a board being deleted, and deleting a board drops its index.

A person's question is never handed to Tantivy's query language — a stray
quote or colon is syntax there. It is cut into clean words first, and
phrases and typos go through Tantivy's parser rather than its hand-built
queries: those take words as they are, while the index holds them stemmed, so
a phrase built by hand never matched.

### Measuring

`evaluate.py` and `mindboard ai eval` score retrieval against a **test set**:
questions about one board, each with the cards a good search returns. It is
the person's own and names their cards, so it lives in `<data>/evals/`, never
in the repository. Four numbers per strategy: **hit@5** (a right card in the
first five), **recall@10** (share of the right cards in the first ten),
**MRR** (how high the first right card ranks) and **sent** (share of the right
cards among those that reach the model within the budget) — overall and by
kind of question.

The first set: twenty questions about *RAG Deep Knowledge* — twelve simple,
two whose wording differs from the cards', two in several parts, four that
need a path through the person's links — drafted from the board's cards and
150 relationships, and theirs to correct.

### What the numbers said

The baseline, before any change — SQLite FTS5 plus vectors kept in SQLite:

| | hit@5 | rec@10 | MRR | sent |
|---|---|---|---|---|
| wording (FTS5) | 0.90 | 0.70 | 0.88 | 0.76 |
| meaning (embeddings) | 1.00 | 0.72 | 1.00 | 0.82 |
| fused + walk, as sent | 0.95 | 0.76 | 0.92 | 0.96 |

After the new engine:

| | hit@5 | rec@10 | MRR | sent |
|---|---|---|---|---|
| keyword (Tantivy, stemmed) | 0.95 | 0.74 | 0.87 | 0.80 |
| meaning (exact cosine) | 1.00 | 0.72 | 1.00 | 0.85 |
| keyword + meaning, fused | 1.00 | 0.72 | 0.91 | 0.89 |
| fused + walk, as sent | 1.00 | 0.76 | 0.91 | 0.97 |

The keyword search is better, as intended — questions worded unlike their
cards went from 0.00 to 0.50, questions in several parts to 1.00 — and the
search as sent never misses outright now (hit@5 1.00). Overall it is level
with before, honestly so: meaning was already carrying most of the load. The
remaining weakness is plain in the per-question detail — cards reachable only
through the person's links ("which chunking methods keep context across
boundaries?" finds *Sliding Window* at 0.20; multi-hop questions 0.68) — and
that is what step 2's graph ranking and reranker are for.

**Exact phrases, reversed by measurement.** Every pair of neighbouring words
was first searched as a phrase and fused with the rest. Measured: pairs like
"rag system" match many generic cards, and at every weight tried (1, ½, ¼)
they made the ranking worse — fused recall fell to 0.67. Exact search now
takes only phrases the person put in quotes; the planner of step 3 is where
exact names belong.

**The vision part was kept off the card again** — and pictures read at 373
tokens a second instead of 2,160. The memory estimate assumed 1.5 GB over the
model's weights; measured, the 9B takes almost exactly its file size loaded,
and the embedding server, started first, took its share before the check ran.
The estimate is now 0.6 GB over the weights with a 0.5 GB margin; a cold
whole-board question went from 51 s to 22 s, model loading included.

Migration 002's `text_hash` column on `node_embedding` is no longer used —
vectors moved to the index — and stays, harmless, as migrations do. 133 tests,
`tests/test_retrieve.py` rewritten for the engine: stemming, title boost,
group paths, quoted phrases, typos, exact meaning order, incremental and
re-modelled embedding, surviving a restart, dropping with the board, fusion,
walk, budget, wording-only fallback, and the evaluator itself.

## 31. A reranker that reads, and relevance that spreads (2026-11-07)

Step 2 of the three in §30: a reranker, and ranking by the person's graph.
Built as pieces, then measured against each other on the same twenty
questions before any of it was wired in.

### The pieces

- **Qwen3-Reranker 0.6B** (ggml-org's own GGUF, Q8_0, 0.64 GB, Apache-2.0),
  pinned like every other file and installed with the rest. A cross-encoder:
  it reads the question and a card *together* and scores how well the card
  answers it - far more precise than comparing two vectors made apart. It runs
  as a third llama-server (`--rerank`, `/v1/rerank`), with the same lifecycle
  as the other two: a fresh key each start, unloaded when idle, stopped with
  Mindboard.
- **A card is read with its links.** A card that is only a title means little
  alone: for "which chunking methods keep context across chunk boundaries?"
  the reranker scored *Sliding Window* 0.013. Read as "Sliding Window /
  Linked: is an example of Context-Preserving Chunking", it scored 0.998.
  `retrieve.relations` writes each card's relationships in words from its own
  side - "measures Retrieval quality", "Ragas measures this".
- **Personalized PageRank** over the person's graph (`retrieve.graph_rank`),
  the idea HippoRAG uses: relevance starts on the best cards, in proportion to
  their scores, and flows along every relationship (either way) and every
  group membership, a quarter returning to the start each round. A card that
  several good cards lead to - one link away or three - rises; one nothing
  relevant connects to stays at zero. Here the graph is the person's own,
  typed by them, not one a model extracted.

### What the numbers said

| | hit@5 | rec@10 | MRR | sent | multi-hop | ms/q |
|---|---|---|---|---|---|---|
| fused + walk (step 1, as sent) | 1.00 | 0.76 | 0.91 | 0.97 | 0.68 | 29 |
| fused, then PageRank | 0.95 | 0.80 | 0.62 | 1.00 | 0.89 | 31 |
| fused, reranked (30) | 1.00 | 0.84 | 0.90 | 0.87 | 0.64 | 421 |
| fused + PageRank, reranked | 1.00 | 0.92 | 0.91 | 0.99 | 0.80 | 545 |
| **reranked, then PageRank from the best read** | **1.00** | **0.94** | **0.94** | **1.00** | **0.89** | **504** |

PageRank alone finds what the links reach (multi-hop 0.68 → 0.89) but ranks
it badly (MRR 0.62): everything near a match rises, relevant or not. The
reranker alone ranks well but can only read what the words found. Together, in
this order, each covers the other:

1. candidates from the fusion (§30) *and* from PageRank spread from the
   fusion's best ten - so a card the words missed but the links reach is read;
2. the reranker reads up to 30 of each, with their links;
3. PageRank spreads again from the ten it read best, and the two orders - read
   and spread - are fused by reciprocal rank.

Linked text mattered on its own: reranking with plain cards scored rec@10
0.82, with their links 0.84 - and with the graph's candidates added, 0.92.
Restart 0.25 beat 0.15 and 0.40; reading 40 of each was no better than 30
and took longer; 20 was a little worse. If the reranker is missing or fails,
search falls back to step 1's walk, unchanged.

### Three models on one graphics card

Each search model took about 1.1 GB on the card at a 2,048-token context
(1.9 GB at 8,192, where it started: neither ever reads more than a card and a
question). Measured together on this laptop: the 9B with its vision part 10.0
GB, the reranker 1.1, the embedder 1.05 - 12.1 GB, against 12.4 GB free beside
the other programs on the card. Too close to rely on, and a card with 12 GB
or less would have pushed part of the 9B onto the processor, where it answers
several times slower.

So the card goes to the models in the order they matter to an answer
(`llm.helper_on_gpu`), and each search model takes it only if what is left
still holds every model ahead of it that has not loaded yet:

1. the **chat model** - it writes every answer;
2. the **reranker** - it reads up to 60 cards every question: half a second on
   the card; on the processor about 0.15 s a card, so there it reads 10 of
   each list instead (about 3 s);
3. the **embedder** - on the processor it embeds a question in 50 ms and a
   changed card in 0.1 s, so it loses least.

On this laptop that put the 9B, its vision part and the reranker on the card
(11.2 GB) and the embedder on the processor. A computer with more room puts
all three on the card; one with less keeps the 9B whole and the search models
off it. Where free memory cannot be read, llama.cpp's own `--fit` decides.

### What it changed, and what it did not

The right cards now rank higher, but on *RAG Deep Knowledge* that changes
little of what the model is sent: the board is about 5,100 tokens, just over
the 4,000 at which a board is searched rather than sent whole, and the 5,000
budget holds 131 of its 135 cards. Measured on the same questions, with the
reranker a 2,000-token budget still sends every right card (sent 1.00, about
55 cards), and the first words come 1.8 s sooner (7.2 s → 5.4 s); the walk
could not have been cut that far, as its order was weaker (§30). The budget
stays at 5,000 until that trade - less of the board for broad questions, a
quicker start - is decided. Most of what remains before the first word is
the pictures.

`mindboard ai eval` now compares nine strategies, each stage on its own and
together. 149 tests: PageRank reaching linked and grouped cards and nothing
else, relationships in words, the reranker's order deciding, candidates from
both lists, a long question and card cut to fit the reranker, the walk taking
over when the reranker fails, ask passing it through; the reranking server's
answer kept in card order, fewer cards on the processor, the placement order,
and `--device none` for a model kept off the card.

## 32. Ten thousand cards: the whole board is searched, never sent (2026-11-07)

Asked for: a board may hold 10,000 cards, so sending the whole board to the
model is never an option; a question offered after each answer; and no false
warning about memory.

### Always searched

A question with nothing added used to send the whole board when it was under
4,000 tokens and search it otherwise. Now it is always searched. The same
question behaves the same way on a board of 90 cards and of 10,000, and the
threshold - and the mode it switched between - is gone. Cards chosen by hand
are still sent exactly as chosen.

Measured on a 10,000-card board: the 135 cards of *RAG Deep Knowledge*, ids
kept so its test set still applies, among 9,855 cards on fifteen other
subjects, in 120 groups, with 14,730 links:

| | time |
|---|---|
| read the board | 0.13 s |
| index its text, first time | 0.18 s |
| bring the index up to date after one edit | 0.25 s |
| keywords + meaning, fused | 0.03 s |
| PageRank over the whole board | 0.02 s |
| read closely and spread | 0.48 s |
| everything before the model reads | 0.72 s |
| **embed every card, first time (graphics card)** | **92 s** |

Recall@10 0.94 and MRR 0.94 - the same as on the 135 cards alone. Each card's
size in tokens is now worked out once per change of the board and kept with
the index, not for every card at every question.

The one thing that does not scale yet is the first embedding of a big board:
92 s on the graphics card, and on the processor - where the embedder goes
when the card is short (§31) - about 0.1 s a card, some 17 minutes. It
happens at the first question and holds it. Moving it to the background, so
a question never waits for it, is the next piece of work.

### The question offered next

The model ends each answer with one line, `NEXT: <a question>` - one step
further from what was just asked, answerable from the cards or its own
knowledge. `ask.split_next` takes the line off when the answer is done, and
only a last line counts, so a "NEXT:" in the middle of an answer stays part of
it. The panel shows the question under the latest answer, after "Would you
like to know", and asks it when clicked; while the answer is still being
written the line is kept out of sight. It is one generation, not a second
request: about twenty tokens more.

### The warning that was the model itself

"Mindboard's model needs 10.4 GB … only 2.3 GB is free" appeared on every
question after the first. The check read the free memory on the card while
the model was loaded - and the model's own 10 GB was what was missing.
llama.cpp places the model by the free memory at the moment it loads, so the
same check, made just before loading, says how it was actually placed;
`LocalModel.memory_note` keeps that answer for as long as it stays loaded.
The warning appears only when the model truly went on the card short.

Seen on the 10,000-card board in the app: the model sometimes writes the line
as **Beyond your board:** in bold, and splitting on the plain words left a
stray `**` on either side. Bold, underlined or as a heading, it is now read
as the plain line, on the server and while it streams.

157 tests.

## 33. Indexing in the background, and sharing the card by priority (2026-11-07)

§32 found the one thing that did not scale: embedding every card of a big
board - 92 s for ten thousand on the graphics card, about a quarter of an hour
on the processor - happened at the board's first question, which waited for
all of it.

### A question never waits

The index now has two halves, kept up to date differently
(`search_index.BoardIndex`):

- the **text** index is rebuilt whenever the board changes - 0.2 s at ten
  thousand cards - at every question (`sync_text`);
- the **vectors** are made a batch at a time (`embed_some`), outside the
  index's lock so searches go on meanwhile, and saved every 20 s while it
  works rather than after every batch (40 MB a write at ten thousand cards).
  A search uses the vectors made so far. A card whose text changed keeps its
  old vector until the new one is made; a card written while its vector was
  being made keeps waiting for one made from its new text.

`indexer.py` does the embedding: one thread for the app, one board at a time,
sixteen cards per request so a question's own embedding never queues behind a
long batch. A board is queued when it is opened and after every change, and
indexed two seconds after the last one, so a burst of edits is indexed once.
It stops between batches when the board changes again, and picks up from the
new cards. It reads boards and writes only the search index.

A question still embeds, on the spot, up to 32 cards just written - what one
person writes between two questions - so they can be found by meaning at
once; anything more is left to the indexer. While the indexer is still at
it, the panel says so: "meaning (learned for 3,200 of 9,990 cards so far)".
The terminal and the evaluator have no background, and embed everything first.

### Taking the card while it is free

§31 kept room on the graphics card for every more important model, loaded or
not. That left the embedder on the processor for exactly the work the card
speeds up most: a board's first indexing. Now a model takes the card while
there is room on it, and a more important model takes it back when it needs
it (`llm.make_room`): less important ones are stopped, least important first
- the embedder, then the reranker, never the chat model - once their request
in flight is done. Requests that come meanwhile wait, then start the model
again wherever there is room by then (`LocalModel._enter`, `evict`). Models
are placed and loaded one at a time (`_placing`), so none reads the free
memory while another is still filling it; that lock is always taken before a
model's own, never while holding it, so making room can never deadlock.

The order is the same as before - chat model, reranker, embedder - but it is
enforced when it matters instead of reserved ahead of time.

### Measured, through the app, on the 10,000-card board with no index

| | plenty of memory | tight (12.1 GB free) |
|---|---|---|
| first question, 8-10 s after opening: cards chosen in | 2.4 s | 2.3 s |
| ... with meaning learned for | 512 of 9,990 | 720 of 9,990 |
| the whole board indexed | 134 s, on the card | on the card until the second question, then the processor at 19 cards/s |
| second question: cards chosen in | 0.9 s | 2.4 s (the reranker took the card back) |
| memory warning | none | none |

In the tight case the chat model, loading, stopped both search models; the
embedder came back to the card beside it and went on indexing there at about
95 cards a second, until the next question's reranker took the card back from
it and it carried on, on the processor.

### Themes

Six more themes with a feeling, beside midnight, slate, ink and daylight:
forest (calm), ember (warm), ocean (focused), dusk (dreamy), paper (warm
daylight) and mist (airy). The picker shows each as a small preview - its
canvas, a card and its accent - with its feeling in words.

166 tests.

## 34. The conversation: one bar, expand, Explain more, Add to board (2026-11-08)

Asked for over several messages, and built as one simple whole - simplicity
and a clean interface come first, so it should not feel like many features.

- **The chat bar.** The one way in: a slim bar centred at the bottom of the
  board, always there, with a slow light running along its edge so it is easy
  to find (it rests while typing or answering, and stops for anyone who asked
  their system for less motion). The corner button is gone; `Ctrl+J` goes to
  the bar. Asking opens the conversation above it. The bar fits between the
  shortcuts and the board's controls; on a narrow window it rises above the
  controls.
- **Expand.** One button in the conversation's header: it fills the screen
  and the board dims. Going to a cited card steps back to the board; `Esc`
  steps back one level at a time.
- **Explain more** (a switch in the expanded view). While it is on, a
  question is not searched: it goes to the model with the conversation and
  its cards, asking for a full explanation in plain words, an example or two,
  and a follow-up - and to explain from its own knowledge where the cards fall
  short, rather than say they cannot. The instruction goes with the question,
  not in the system prompt, so the start of the prompt and its cache stay put.
  First tried, the model held to its rule "if the cards do not answer, say so"
  and declined; the instruction now says plainly that it should explain.
- **The conversation remembers its cards.** Cards earlier answers cited stay
  in context, first (where the prompt cache keeps them), and each search fills
  the rest of its budget.
- **Add to board.** Under each answer. The model drafts a card - a short title
  and the knowledge in a few sentences, not the chat - and picks, from the
  cards the answer cited, the one it belongs with and how (example of, part
  of, ...). It can only link to a card the answer used; if its reply is not
  usable, the draft falls back to the question and the answer. The person
  edits the draft, changes the link, and presses **Add**: one undoable step
  through the op-log, card and link marked `provenance: "ai"`, placed beside
  the card it links to, in its group. The indexer makes it searchable a moment
  later. Drafting writes nothing.

Also: an id the model copies in single brackets, as the RELATIONSHIPS list
writes them, is read as the citation it is meant to be.

Checked in the app on the 10,000-card board: asked through the bar, expanded,
drafted and added a card (one card and one link, both "ai"), and asked with
Explain more on (4 cards from the conversation, no search, a full explanation
with an example). 173 tests.

The unused 27B model files were deleted (15 GB).

## 35. What was left rough, measured (2026-11-08)

- **Relationship lines now count against the search budget.** A card costs
  its text plus 25 tokens for each relationship it has with a card already
  chosen. On *RAG Deep Knowledge* a search now sends about 4,900 tokens of
  text (85 cards) where it sent 8,900 (131); every right card on the test set
  still gets through (sent 1.00, recall@10 0.94).
- **Pictures in a search: 2,000 tokens, not 6,000.** Pictures come best match
  first. Measured, first words: 5.0 s with 6,000 (ten pictures), 3.0 s with
  2,000 (four), 1.8 s with none. The four kept are the best-matching cards';
  the others' titles and captions still go in. Cards chosen by hand bring
  every picture, as before.
- **The canvas at 10,000 cards, measured - and not fast enough.** The
  10,000-card board opens in 0.7 s (0.5 s to fetch its 7.3 MB), because its
  groups keep each level small: 58 cards drawn at the top. But 3,000 cards on
  *one* level open in 2.9 s and zoom at 2 frames a second (frames up to 0.7
  s), drawing 741 cards and 3,380 relationships. Only what is in view is drawn
  already; the cost is each drawn relationship's HTML label and its
  subscriptions to selection state, and cards that are not memoised, so every
  zoom step re-renders thousands of components. The fix - memoised cards,
  relationship labels hidden when zoomed out, cheaper rendering far out - is
  its own piece of work (TODO.md).
- The 1.3 s restart of a search model after it gave the card back stays: it
  is paid once, on the question after.

174 tests.

## 36. A faster canvas on big levels (2026-11-08)

The canvas component read the zoom with `useViewport()` only to show the zoom
percentage - which made the whole canvas render again on every frame of a
zoom or pan, handing React Flow new handlers and every card and relationship
a reason to render. The percentage now subscribes on its own (`ZoomLevel`).
Cards and relationships are memoised; a relationship's label is left out
below 45% zoom (unreadable there), unless it is selected or being named; a
card below 35% draws only its title.

Measured on 3,000 cards on one level, zooming: **2 frames a second before,
18 after** (slowest frame 737 ms -> 142 ms). The rest is the browser painting
some 2,300 relationship curves each frame; thinning or simplifying them far
out, or drawing them on a canvas, is the next step if big flat levels matter
(TODO.md). Boards organised in groups were never affected: the 10,000-card
board draws 58 cards at its top level.

## 37. Planning before searching - measured, and not adopted (2026-11-08)

Step 3 of §30: the model plans a question before searching - one to three
search phrases (one per part, or per link in a chain) and whether it is broad
- and the searches are merged by reciprocal rank. Measured on the 20
questions, against today's search (reranked, then PageRank):

| | rec@10 | MRR | multi-part | multi-hop | per question |
|---|---|---|---|---|---|
| today's search | 0.94 | 0.94 | 0.83 | 0.89 | 0.65 s |
| the plan's searches only | 0.87 | 0.81 | 0.92 | 0.89 | 1.76 s |
| the question twice + the plan's | 0.95 | 0.87 | 0.92 | 0.89 | 2.16 s |

Split into phrases, a question loses what made it specific, and the first
answer ranks lower (MRR). Keeping the whole question beside the phrases wins
back recall but not the ranking, and every question pays 1.5 s more: the plan
itself takes 0.6 s, and the model splits almost every question, even simple
ones. Questions that cross links gain nothing - the reranker reading cards
with their links, and PageRank spreading from them, already follow the chain.
It never judged a question "broad". So the planner is not used, and its code
was removed; search was split into `score` and `fit` on the way, which stays.
Worth trying again only with a test set where multi-part questions matter
more, or a planner that leaves simple questions alone.

## 38. Pictures found by what they show (2026-11-08)

A picture card was found only by its title and caption, though the 9B reads
pictures. Now each picture is described once - what it shows, the words and
numbers written in it, what kind of picture it is - and the description joins
the card's searchable text: its keywords, its meaning vector and what the
reranker reads.

- **Derived, not written to the board.** Descriptions are kept by picture
  hash in `<data>/search/pictures.json` (`search_index.PictureNotes`), beside
  the indexes - like the vectors, regenerated if deleted, never exported,
  never an op. Your cards are unchanged.
- **Made by the background indexer, with the chat model - only while it is
  loaded anyway** and not answering: opening a board never loads a 10 GB
  model, and a question never waits behind a description. After each answer
  the board is queued again, so its pictures are described in the moments
  the model is idle. A new description re-indexes the text and gives its card
  a new vector.

Measured on *RAG Deep Knowledge*: 43 pictures described in 83 s (1.9 s each).
The descriptions read the text inside screenshots ("a comparison table titled
'Bi-Encoder vs. Cross-Encoder: The Core Difference'"). "Where basic RAG
breaks: symptoms and likely causes" now finds *Common RAG Problems and
Provided Solution* first - a card whose content is only its picture, and was
not in the top three before; "RRF score formula" moves *What is RRF* to first.
176 tests.

## 39. The last rough edges (2026-11-08)

- **A picture left out of a question still goes in, as words.** A search
  sends only the best matches' pictures (§35); the others' cards now carry
  "picture shows: ..." from their description (§38), so the model knows what
  they show for a few dozen tokens instead of hundreds.
- **Relationships far out are plain lines** - below 45% zoom, no arrowhead,
  dashes or wide invisible click area. Measured, zooming 3,000 cards on one
  level stayed at 17-18 frames a second: painting was not the limit. Drawing
  every card and relationship instead of only those in view made it worse (8
  frames a second), so only-in-view stays. What is left is the browser moving
  some 3,500 elements each frame; beyond this, a far-out view drawn on a
  canvas instead of as elements would be needed. Boards kept in groups - each
  level small - are not affected.
177 tests.

## 40. Experts (2026-11-08)

A separate page, `#/experts`, from the **Experts** button on the start page:
talk to experts made of your boards.

- **An expert** is a name, a brief and the boards it draws on - the same
  local 9B, so no extra memory. Experts span boards, so they live in the
  library, `<data>/experts.json`, written atomically and carried in backups
  (restored in skip and replace modes; not in copy mode, where every board
  gets a new id). Nothing a conversation says is remembered - each visit starts
  fresh, as asked.
- **Asking an expert** searches each of its boards with the board search
  (`retrieve.score`), merges them by reciprocal rank so every board's best
  cards come near the top, and fits one budget (`retrieve.fit`). The model
  reads its name and brief with its system prompt and answers in that voice;
  every card it cites says which board it is on, and a click opens that
  board at the card. The streaming is shared with a board's answers
  (`stream_answer`).
- **The guide**: "Not sure who to ask?" in the overview. It searches every
  expert's boards, lets the reranker judge how well each one's best four
  cards answer the question, keeps the leaders (within 60% of the best, at
  most two), and has the model say in a sentence or two who can help and why.
  Tried on the scratch library: for "How does re-ranking improve retrieval?"
  it chose the Retrieval Engineer and the Generalist, and said why.

### The page

A night desert in three.js - bundled, never fetched, and loaded only when the
page opens (the board interface stays 452 kB; the Experts page is its own
511 kB part). Each expert stands as a **monument of glowing sand**: grains
blow off its edges on a wind that wanders and gusts; its **crown** floats
apart, glows and blinks, and under the pointer sends grains of light slowly
upward; resting the pointer on its body gathers its **sign** inside, in 3D;
thinking whips the sand into a storm; speaking sends waves up it. Going in,
it comes apart and the camera flies into its sand, where you talk to it.

| Monument | Crown | Sign inside | Unlocks with |
|---|---|---|---|
| Ankh | its loop | feather of Maat | - |
| Obelisk | pyramidion | sun disc of Ra | 2 boards of 20+ cards |
| Pyramid | capstone | Eye of Horus | 3 boards of 50+ cards |
| Step pyramid | top block | djed pillar | 5 boards of 100+ cards |
| Temple gate | winged sun disc | winged scarab | 8 boards of 150+ cards |

Monuments are **earned, not chosen freely**: each opens as the library grows,
which makes growing your boards a little like a game. Locked ones show what
they need and your progress; a newly opened one is announced. An expert keeps
its shape if the rule changes. A monument's **size is how much it knows** -
the cards on its boards, on a log scale - and all share one scale, the camera
looking down across the desert so sizes compare. Only the one in front turns.
Names only under them; a pen beside the name, on hover, edits.

Fixed on the way: the page could be scrolled by focusing its box (now
`overflow: clip`); a narrow window steps the camera back.

Not yet: the voice - Kokoro, local, with the sand following the audio - is
part 2. 184 tests.

## 41. A voice for the experts (2026-11-08)

Asked for: realistic and human, not the Windows voices, and no heavy
infrastructure. **Kokoro-82M** (Apache-2.0) through **kokoro-onnx 0.6.1**
(MIT), checked current before pinning: one 325.5 MB model and a 28.2 MB voices
file, each pinned by address, size and SHA-256 like every model here. It runs
on the processor with onnxruntime, so it never takes graphics memory from the
9B: after a one-time 3.4 s load, a sentence takes about 0.55 s to make for
3.2 s of speech - six times faster than real time.

Its pronunciation step uses espeak-ng and phonemizer, which are **GPL-3.0**.
Fine to run on your own computer; not something an MIT program should ship.
So the voice is an optional extra (`pip install -e .[voice]`, or
`mindboard ai voice`, which also fetches the pinned files), never a core
dependency, and Mindboard works without it.

- `POST /api/voice/speak` returns one sentence as WAV; nothing leaves the
  computer. `GET /api/voice` says whether it is set up.
- Each expert has a voice of its own (eight in turn, warm to deep), kept on
  edits.
- The page reads an answer when it is done, sentence by sentence, fetching the
  next while one plays - no gaps. A Web Audio analyser measures how loud it is
  each frame and the sand moves with the real voice. A speaker button in the
  bar turns it off; a new question or leaving stops it.

Speaking *to* an expert would need speech recognition on this computer
(whisper.cpp): the browser's own sends audio to a server. Not built. 188 tests.

## 42. Speaking to the experts, and the black hole (2026-11-08)

- **Listening.** A microphone button in the Experts bar: press, speak, press
  again. The browser records; the page brings the recording down to 16 kHz
  mono WAV and posts it to `/api/voice/listen`, where **whisper.cpp** (MIT)
  turns it into words on this computer - the browser's own speech recognition
  would send the audio to a server. The official processor build (release
  `b5454`, beside v1.9.5) and the English base model (148 MB), both pinned by
  size and SHA-256, installed by the same `mindboard ai voice`. Measured: a
  spoken question made by Kokoro was heard back word for word in 1.3 s. The
  question is then asked as if typed - to the guide in the overview, or to the
  expert you are with. The audio is not kept.
- **The black hole** eats only loose sand. The drifting sand keeps per-grain
  state in the page: near the pointer a grain is drawn in at its own depth
  and swirled; at the centre it is eaten, and 1.5-5.5 s later a new grain is
  born somewhere else and fades in. The monuments are never touched.
- The voice no longer moves the sand, and reads the suggested question after
  the answer ("Would you like to know: ...").

190 tests.

## 43. The first page: two signs in sand (2026-11-08)

Opening Mindboard (`#/` or no hash) now shows a welcome page before anything
else: the Experts' night desert, the title, one line - *Write what you learn.
Find your own gaps.* - and two signs of glowing sand, the two ways in:

- **The Eye of Horus, knowledge** - *Your experts* (`#/experts`).
- **A papyrus scroll and reed pen, writing** - *Your boards* (`#/boards`, the
  board list that used to be the start page). The scroll is a new sign in
  `signs.ts`: a gently curved sheet with its ends rolled, five lines of signs
  (strokes, circles, uprights, hooks) and the pen across it.

The signs gather grain by grain out of the dust over six seconds (by the
clock, not by frames, so a slow or background tab gathers at the same pace),
turn a little so their depth shows, and brighten under the pointer with
grains rising from them. Clicked, the sign comes apart into dust while the
camera glides into it, and its page opens 1.7 s later. The click finds its
sign from where it lands, so a tap with no pointer move before it works.
Side by side on a wide window; one above the other, the camera stepped back,
on a narrow one. The pointer eats the drifting dust as on the Experts page -
`eatSand` and the sand helpers are now shared from `scene.ts` - and the signs
are never touched.

Routes: closing a board goes back to your boards; the Experts' *Mindboard*
crumb goes to this page. The menu's *Open my boards* opens the board list
directly, *Talk to my experts* the Experts.

193 tests.
