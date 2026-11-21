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
crumb goes to this page. The menu's *Open Mindboard* opens this page - it is
the choice between boards and experts - and *Talk to my experts* goes
straight to the Experts. (Option 1 first opened the board list directly,
from before this page existed; opened that way the first page was never
seen.)

193 tests.

## 44. Talking with the experts: hands-free, interruptible, kept (2026-11-08)

**Hands-free, with Smart Turn.** The microphone button now starts a
conversation instead of a recording: press it once and just talk.
- The microphone stays open, through the browser's echo cancellation, noise
  suppression and gain control. It is taken in through an AudioWorklet and
  brought to 16 kHz in the page.
- A light voice detector runs on each 20 ms of sound, against a noise floor
  it follows slowly. 80 ms of speech starts a turn, keeping the 400 ms before
  it, so the first word is not cut.
- While you speak, the words so far are written into the bar about every
  1.2 s (whisper.cpp), and the bar shows a level meter.
- When you pause, **Smart Turn v3.2** (pipecat-ai, BSD-2-Clause, 8.7 MB
  int8 ONNX, pinned by revision, size and SHA-256) decides from how your
  voice ended whether you have finished or are only taking a breath:
  `POST /api/voice/turn`, 13-26 ms on the processor.
  - It is asked after 400 ms of quiet, and again at 1.2 s if it said not
    yet. 2.5 s of quiet ends the turn anyway.
  - A finished turn is written down in full and asked, with nothing to press.
- Smart Turn reads a Whisper log-mel spectrogram. Its reference code makes
  that with `transformers`; `turn.py` does the same arithmetic in numpy. It
  matched the reference to within 6e-6, and a test pins reference values.
  Measured on Kokoro speech: a finished question scored 0.99, a trailing
  "how reranking, um, and" 0.006.
- Without Smart Turn installed, 1.2 s of quiet ends a turn.
- Smart Turn is installed with the rest of the voice (`mindboard ai voice`,
  about 520 MB in all). The menu and the command now check for it too.

**Interrupting.**
- Stop is offered whenever the expert is writing *or* speaking. Stop, or Esc,
  ends both: the answer is cut where it was and marked *Stopped*, and the
  model is told directly (`POST /api/experts/ask/stop`).
- Hands-free, you can simply speak over the expert. While it is busy, the
  detector asks for a clearer, longer sound (240 ms) before it treats it as
  you; then it stops the expert at once, listens, and asks what you said.
- Measured in the preview with a synthetic microphone: the answer stopped,
  and "Tell me about chunking instead." was heard and asked.

**The rest of the conversation.**
- **Speed:** a chip in the bar sets how fast the expert speaks: 0.8× to
  1.25×, remembered. It is passed to Kokoro, and the next sentence uses it.
- **Scrolling:** the conversation follows a growing answer only while you
  are at the bottom. Scroll up to read, and it stays where you put it.
- **Coming back from a card:** opening a cited card keeps the conversation
  for up to two hours. Coming back (Back, or closing the board) finds it
  where it was, inside the same expert. Closing a board opened from a
  conversation returns to the Experts rather than the board list.
- **Add to board:** offered under every answer, as on a board (§34). The
  model drafts a card from the answer, and you choose which of the expert's
  boards it goes on (the one it cited most, by default) and which cited card
  it links to, and how. Add writes it through the op-log with provenance
  `ai`, beside that card. The indexer makes it searchable.
- A citation the model got wrong (not one of the given cards) is left out
  rather than shown as raw brackets, and is not read aloud.

**Looks.**
- The unlock notice no longer covers anything. On the empty desert it sits
  above the words with a gap. Elsewhere it sits at the top, never inside a
  conversation, and steps aside after 9 s.
- The bar's icons are gold line drawings, not coloured emoji.
- Only the conversation and inputs can be selected, so no selection
  highlight is left behind the crumbs or buttons.

200 tests.

## 45. Listening like CLARA: Silero VAD on the server, a reopen window, laid-out answers (2026-11-08)

Tried with a real microphone, §44's listening fell short. A chime became a
question ("(chiming)"). A pause mid-sentence sent half of it. The expert's
own voice came back as fragments. Speaking over an answer gave "still
answering the last question". §44 decided turns in the page by loudness
alone. CLARA, the person's voice project, gets this right on the
speech-to-speech pipeline (huggingface, v0.2.12, Apache-2.0), so this
follows its design and its tuning.

**Turns are decided on the server** (`listen.py`, `/api/voice/stream`, a
WebSocket).
- The page only carries sound: the microphone, through echo cancellation,
  brought to 16 kHz in an AudioWorklet and sent in 32 ms blocks.
- **Silero VAD v5** (MIT, 2.3 MB ONNX, pinned) gives each block a speech
  probability. Speech starts at 0.6 and holds down to 0.45.
- A sound counts as you only after **384 ms** of speech; resuming a turn
  needs 192 ms. **250 ms** of quiet ends a stretch, and **Smart Turn** reads
  the whole turn (finished at p >= 0.55). If it is not finished, the turn
  waits up to **2 s** of quiet. 500 ms before speech is kept. These are
  CLARA's `configs/serve-gpu.json` values.
- **The reopen window:** when Smart Turn says finished, the turn stays open
  for **900 ms**. If you go on within it, the same turn continues; nothing
  was asked yet. The words are written down (whisper.cpp) during the window,
  so it costs no time. Upstream uses this for exactly the misjudged pause.
- Your words appear about every second while you speak. Whisper's notes on
  sounds, "(chiming)", "[BLANK_AUDIO]", "*music*", are removed; a turn that
  was only those is not asked.
- The socket checks the token itself (the HTTP gate does not see
  WebSockets) and refuses pages from other origins.
- Measured on Kokoro speech with a chime before it: the chime made no turn.
  "I would like to know how reranking", a 0.7 s pause, and "actually works
  on my board." made one turn: speech, end, reopen, end, commit. Listening
  costs 0.25 s of processor time per 10 s of sound.

**Echo.** The expert's voice now plays through the same AudioContext as the
microphone, as in CLARA, so the browser's echo canceller has the exact
signal to take out. §44 used two contexts.

**Speaking over an answer.** "speech" arrives only after 384 ms of real
speech; then the page stops the answer and the voice. A new question to an
expert now takes over: the server cancels the answer in progress and waits
up to 6 s for the model, instead of refusing with "still answering".
Measured in the preview: the answer stopped, "Wait, stop. Tell me about
chunking instead." was heard whole, and it was answered with no error.

**Answers, laid out** (`Prose.tsx`, used by the expert conversation and the
board's own).
- The model's markdown is read into blocks and drawn: headings as headings,
  bullet and numbered lists at their real depth (worked out from each
  list's own indentation), bold, italics, code, quotes, tables and rules.
- Citations stay chips, with no gap before the full stop after them.
  "**Beyond your board:**" in bold or as a heading is recognised.
- Nothing is ever treated as HTML.
- Measured: an answer with two sections and 26 list items, 18 of them
  nested, showed no raw `#` or `*`.

**The microphone is never hidden.** It shows whenever listening is installed.
If hands-free talking is not ready, pressing it says why, just above the
bar, inside an expert as well as on the overview. Either Mindboard was
updated while running and needs restarting (an older server does not report
`talk`), or the small download is missing (Set up the AI). §44 hid the
button instead, which looked like the option did not exist. Messages about
the microphone used to appear only on the overview.

210 tests.

## 46. Experts read the question first, and offer three next questions (2026-11-08)

Asked "which architecture for a RAG system for a school, where students ask
about their lectures?", an expert answered for "a school project" and
recommended the simplest thing, weighing nothing. It answered in one pass,
and its rules pushed it to summarise whichever cards matched.

- **A reading before the answer.** One short call works out what kind of
  question it is (fact, explain, advice, compare, design), the person's
  situation in one sentence in their own terms, and, for advice, comparisons
  and designs, the 3 to 6 points that decide the answer. Points the person
  left open are named as open, never assumed. The reading is shown above the
  answer, so a misreading is seen at once. §47 turned it into the plan.
- **Weighed answers** come in a fixed order: the recommendation; one line
  per deciding point; why another approach does not fit; what would change
  the advice. Fact questions get one to three sentences. The instructions go
  with the question, not the system prompt, so the cached cards at the start
  of the prompt stay put.
- **Three next questions** end every answer, as buttons: one going deeper,
  one putting it to use, one on a neighbouring idea from the cards. "Next:"
  in any case is understood, and kept out of sight and out of the voice.
- **The voice speaks while the answer is written**, one finished sentence at
  a time, instead of after the whole answer.
- The install test (`test_not_set_up_until_every_piece_is_there`) left 6 GB
  in the temp folder on every run: on Windows `truncate()` writes zeros. It
  now writes only the last byte, and the file stays sparse.

Measured on RAG Deep Knowledge, the same question gave three different main
recommendations in three runs (Naive RAG + Reranking, Hybrid Search +
Reranking, Modular RAG): the answer is sampled at temperature 0.7, and the
reading changed what it thought was open.

## 47. A plan, run step by step, before the answer (2026-11-08)

The person asked for a planner that writes the steps needed to answer a
question - what to look up, what to work out - then runs them one after the
other and answers from what they found. §37 measured a planner that split
every question into search phrases: it made search worse and slower. This
one differs in three ways: it leaves questions about one thing alone, it adds
its searches beside the question's own instead of replacing it, and it works
something out at every step rather than only searching.

**The plan** (`ask.make_plan`). The reading of §46 now also returns up to 4
steps. Each step has `find` (a search phrase for the cards, or "" for a step
that only works from earlier ones), `work_out` (one sentence) and `uses` (the
earlier step whose result it needs). Advice, compare and design questions get
one step per option worth checking, then a step that weighs them. Fewer than
two steps, or steps that look nothing up, count as no plan
(`ask.clean_plan`): one look is the question's own search. A reply that
cannot be read is logged, and the question is answered as it stands.

- **Planned from what the notes hold.** Planned blind, the first plan asked
  about embedding models, vector databases and LLMs; the boards hold none of
  that, and all four steps came back "Not in the cards". Now the question is
  searched first and the planner sees the titles of the 30 best-matching
  cards. Its steps then name real cards: "Modular RAG", "Common RAG Problems
  and Provided Solution".
- **Two options at least.** Told "2 to 4 options", the 9B checked one. The
  prompt now says checking one option weighs nothing, and gives an example
  outside RAG, so it does not bias these boards.

**The steps** (`experts.run_plan`). Each step searches the expert's boards
(2,500 tokens of cards), then the model writes 1 to 5 lines of notes from
those cards and from what the earlier steps found, citing cards. Citations of
cards the step was not shown are removed. A step that `uses` an earlier one
searches its phrase plus the titles of the cards that step cited: a chain is
followed by name, with no extra model call. A weighing step has no cards and
works from the findings above it.

**The answer** is the normal streamed answer. Its cards are the question's
own search, plus each step's phrase, plus every card the findings cite, put
first so the answer can cite them. The findings and the weighing go with the
question.

**One run, one hold.** The plan, the steps and the answer are separate
model calls but one question. The endpoint now streams at once and holds the
model's single slot for the whole run (`stream_answer(hold=True)`; inside it,
`LocalModel.stream(hold=False)`), so nothing slips in between the calls. A
Stop given during the run stays set: every later call ends at once, and a
new question still takes over between steps or mid-step.

**On screen.** The plan appears above the answer as a checklist: waiting,
running (pulsing), done, each with what it looked for, how many cards it
read, and a "What it found" that opens, its citations chips like the answer's.
While it works, the note says "Working through step 2 of 4…".

Measured on RAG Deep Knowledge (10,000 cards), with the model loaded:

| question | steps | plan | steps done | answer done |
|---|---|---|---|---|
| What is reranking? | none | — | — | 8 s |
| Which architecture for students asking about lectures? | 4 | 4.5 s | 17-19 s | 28-31 s |
| Add multimodal capability to that | 4 | 4.7 s | 18 s | 28 s |

- The school question was asked three times with the plan; each answer
  recommended Naive RAG + Reranking and said why Modular, self-correcting or
  agentic RAG were not needed. Three runs is too few to call it steady, but
  §46's three runs gave three different answers.
- The multimodal follow-up planned from the conversation (the Naive RAG +
  reranking pipeline it had recommended) and said plainly that the boards
  hold nothing on multimodal approaches beyond captioning.
- A cold start is slow because loading is: the 9.5 GB model took 4.5 minutes
  to read from a disk shared with downloads, and the plan waits for it.

Not done: the plan is not measured on the retrieval test set the way §37
was; it is not used for Ask your board, only for experts; and a follow-up
like "why did you choose that?" sees the earlier answer, not its plan.

218 tests.

## 48. Pictures and PDFs, shown and offered with the answer (2026-11-08)

Search already found a picture by what it shows (§38), and the model read the
best pictures themselves, but an answer showed a cited picture only as its
title, a chip. Seeing it meant going to its board; from an expert, that meant
leaving the conversation. PDFs were stored and opened in the person's
reader, but their text was never read: a PDF was found only by its name and
whatever note was written on its card.

**Pictures in the answer** (`AnswerPictures.tsx`, board chat and Experts).
- Each picture card an answer cites appears under it as a thumbnail, in the
  order cited, at most 6. The server already made and kept thumbnails
  (`/assets/{sha}/thumb`).
- A click opens it full size over the page, in the canvas's own viewer
  (`Viewer.Open`, now reusable: zoom, drag, full screen, Esc), with "Show on
  its board" for its place in the map. It opens in a portal on the page's
  body, so it sits above the Experts page as well.
- Experts send each card's picture with `where` (`experts.where`: board,
  title, `pic`); board chat takes it from the board's cards, which the page
  already has.
- A picture whose file is not on this computer shows as "picture file
  missing" rather than a broken image. The 10,000-card test library has no
  files at all, which is how this was found.
- The rules tell the model that cited pictures are shown, so it cites them
  when asked to show or find one. Told this, it once also wrote a markdown
  image with an address it made up; images it writes are now taken out
  (`ask.MD_IMAGE`), and the rules forbid them.
- An expert's pictures left out of the question now go in as words, "picture
  shows: …", as a board's always did (§39), in the answer and in each step.

**PDFs found by what they cover.**
- `assets.pdf_text` reads a PDF's first 40 pages, up to 12,000 characters,
  with pypdf (pure Python, BSD-3-Clause, the one new dependency). A PDF with
  no text layer, a scan, gives "".
- The background indexer reads each board's PDFs not yet read, before it
  indexes, with no model: a few tenths of a second a file. The text is kept
  by file hash in `<data>/search/files.json` (`FileNotes`), beside the
  pictures' words, derived and regenerated like them. Your cards are
  unchanged.
- The text joins the card's searchable body ("File holds: …"): keywords,
  meaning and the reranker all see it. The model reads the first 700
  characters under the card ("file holds: …"), so it knows what the file
  covers, and the rules tell it to cite a file that covers the question.

**PDFs offered with the answer** (`AnswerFiles.tsx`): "Read more in your
files", up to 3. Each has Open, which hands the file to your PDF reader as a
click on its card does, and "Show on its board". The files offered are the
PDFs the answer cites, then PDFs among the question's best matches even if it
did not cite them. In a planned expert answer the steps' cards come first and
pushed a matching PDF out, so the PDFs among the question's own 8 best
matches (the glimpse taken before planning) are kept in the answer's cards
and offered by name (`Context.offer`).

Measured on the test library, with a 5-page PDF on chunking named
`reading-list-03.pdf` (its name says nothing of its subject):
- Opening the board read it at once (`files.json`, 5 pages).
- Board chat, "How much overlap should I use between chunks for lecture
  notes?": answered from it in 16 s ("10 to 20 percent… 300 to 500 tokens"),
  cited it, and offered it.
- The Retrieval Engineer, same question, planned in 4 steps: before
  `Context.offer` it used the same facts but offered nothing; after, it cited
  the PDF and offered it.
- "Show me a diagram of how reranking fits into retrieval": board chat and
  the expert both showed the diagram (a picture card added for the test) in
  10-11 s; it opened full size over the Experts page and closed with Esc,
  leaving the conversation as it was.

Not done: other file types (Word, slides) are still found by name only; a
scanned PDF has no text to read; and Open was not pressed in the test, as it
starts the PDF reader on this computer.

226 tests.

## 49. Every file a card points to is in the backup - or named (2026-11-08)

A backup carries every file any card points to (§22: found by the shape of
a hash, whatever key it hides under). But a file missing from disk was
skipped without a word: the backup finished normally, and the gap showed
only after a restore. The test library is that case - its cards point to 49
pictures whose files were never copied. The rule now: every file a board's
cards point to goes in, or the backup says which one did not and why.

- **Taken from another board's copy** (`bundle.find_file`). A file is named
  by its hash, so when one is missing from its own board, the same file kept
  by another board is the very same bytes. It goes into this board's bundle,
  and a restore puts it back where it belongs.
- **Found nowhere: named, never passed over** (`board_files`, `check_files`).
  The backup is still made - everything else, and every card, is in it - and
  it says what it lacks: `MISSING-FILES.txt` inside the zip, by board, card
  and file; `missing_files` in its manifest; a warning in the terminal
  (`mindboard backup`) and the menu; the count in the download's
  `X-Mindboard-Missing-Files` header.
- **Asked before the download in the app.** "Back up all" and the palette's
  backup check first (`GET /api/backup/check`, which writes nothing and takes
  a moment). If a file is found nowhere, they name it - board and card, up to
  eight, the rest in the zip - and ask whether to download the backup anyway.
  If the check itself fails, the backup is never refused for it.
- A single board's export (`.mindboard`) follows the same rule, and counts
  its missing files in its manifest.
- Files no card points to any more are still left out, as before: they are
  what `gc` removes.

Measured on the test library: the backup took the 2 files it has (the
diagram and the PDF of §48), named all 49 missing ones in the terminal, in
`MISSING-FILES.txt` and in the manifest, and restores. In the app, "Back up
all" named the same 49 before downloading; cancelling downloaded nothing.

230 tests.

## 50. Listening on a Mac, through Homebrew (2026-11-09)

On a Mac, everything ran except speaking to an expert: setting up the voice
said "not available on this kind of computer yet". whisper.cpp publishes
programs for Windows and Linux only; for macOS it ships a framework for
apps, not a program. Homebrew builds it for Apple silicon, with Metal.

- **A Mac listens through Homebrew's whisper-cpp** (`voice.py`). Setting up
  the voice runs `brew install whisper-cpp` (with Homebrew's auto-update
  off, which would add minutes), then downloads the same pinned, checked
  model, Smart Turn and Silero VAD as everywhere else.
- **Found where Homebrew puts it.** `whisper-cli`, or `whisper-cpp` (its name
  before whisper.cpp 1.7), on PATH or in `/opt/homebrew/bin` and
  `/usr/local/bin`: a double-clicked `Mindboard.command` may start with a
  PATH that lacks them.
- **Said before, and when it fails.** Setup says on a Mac that listening is
  installed with Homebrew. Without Homebrew it says to install it (brew.sh)
  or run `brew install whisper-cpp`; experts still speak, and nothing is
  downloaded for a voice that cannot listen. A failed install says Homebrew's
  own last line ("No space left on device").
- **Not pinned here.** Homebrew checks its own downloads, but the program's
  version is Homebrew's, not ours. Only its long-standing flags are used
  (`-m -f -nt -np -l -t`), the same as the pinned Windows and Linux builds.
- Windows and Linux are unchanged.

Tested as a Mac would see it (the platform reported as darwin, a stand-in
Homebrew): found on PATH or in Homebrew's folders, by either name; installed
once, then not asked again; the right messages without Homebrew and when it
fails. Not yet run on a real Mac.

236 tests.

## 51. Pictures described sooner, with their text written out (2026-11-09)

Asked about something inside a picture without naming it, Mindboard found the
picture only through its description (§38), and two things limited that.
Pictures were described only while the chat model was loaded anyway, so a
picture added before the day's first question was found by its title alone.
And a description was 1-3 sentences: a word in one table cell or a corner
label was not in it, so a question about that detail could not find it.

- **Sooner.** A picture never described now loads the model, a couple of
  seconds after it is added (or when its board is opened), and is described
  then. The load happens outside the question slot: a question asked while
  it loads waits for the load and then goes first, as before. The model is
  unloaded after 20 idle minutes, as always.
- **Not onto a full graphics card.** When the card has less free memory than
  the model needs (`llm.memory_shortfall`), it is not loaded for this: it
  would load slowly and partly on the processor, for work nobody is waiting
  on. Then pictures wait for the next question, as before. A Mac's shared
  memory is never "short", so a Mac always describes soon.
- **Not for files that are missing** (§49): a picture whose file is not on
  this computer cannot be described, and must not load the model each round.
- **Its text written out.** The description now has one or two sentences on
  what the picture shows, then "Text:" and every word, number and label in
  it, in reading order: headings, table cells, axis names, captions, code.
  Up to 600 tokens, kept up to 3,000 characters, all searchable (keywords,
  meaning, reranker). Where a picture does not fit into a question and goes
  in as words, only the first 700 characters are read (`ask.seen_line`), so
  a long description cannot crowd out other cards.
- **Old descriptions are redone, not dropped.** New ones go in
  `search/pictures-2.json`; the short ones in `pictures.json` stay in use
  until each picture is described again, so nothing becomes harder to find
  meanwhile. Redoing them never loads the model: only while it is loaded.

- **A picture the answer drew on, shown even if not cited.** Asked a detail
  from a table, the model answered from the picture and did not cite it, so
  the picture strip (§48) stayed empty. The rules now say that anything taken
  from a picture is cited like a card's text, and the strip also shows
  pictures among the question's 3 best matches (`picturesToShow`), as files
  are offered. Such a "likely" picture whose file is missing is hidden rather
  than shown as missing.

Tested with stand-in models: a new picture loads the model and is described
once; a short graphics card, a busy model or a missing file does not load it;
an old description keeps finding its picture until it is redone, and then a
detail written in the picture ("scale 1:500") finds it.

Tried in the app, with a table picture titled only "IMG_0423" added while the
model was not loaded: opening its board loaded the model with no question
asked, and about 15 s later both pictures were described with their text
written out - "Kestrel-Embed-3 768 0.93 chosen for lectures" - the older
diagram's short description redone too. Asked "Which embedding model did we
choose for lectures, and what recall did it get?", search put IMG_0423 first
of 78 cards and the answer was right ("Kestrel-Embed-3 … Recall@5 of 0.93");
the picture showed under it, cited or not.

239 tests.

## 52. Themes of your own (2026-11-09)

A board could take one of ten built-in themes and have its colours changed
with pickers, but those colours belonged to that board alone: liking a look
meant setting the same nine colours again on every board.

- **Name it and save it.** Under the colour pickers in the Theme panel: a
  name and Save. It keeps the built-in theme the colours started from and
  all nine colours as they are on screen, and the board then uses it.
  Saving again under the same name (case aside) updates that theme, so
  tweaking a colour and pressing Update keeps one theme, not two.
- **Offered on every board**, under "Your themes", with a preview in its own
  colours. Picking one copies its colours onto the board (its settings, with
  `custom` naming the theme), so the board looks the same in an exported
  bundle, or after the theme is deleted. Picking a built-in theme again
  removes those colours.
- **Kept by Mindboard, not the browser** (`themes.py`, `<data>/themes.json`,
  `GET/POST /api/themes`, `DELETE /api/themes/{id}`), like the experts: they
  survive a change of browser and travel in backups. A restore adds them in
  every mode, and restoring twice does not double them.
- Only the nine known colours, as `#rrggbb`, and a known base are kept; a
  name is up to 40 characters; up to 50 themes.

Tried in the app: "Desert night" saved on one board was offered on the other,
applied, kept after a reload; Ocean replaced its colours; deleting it asked
first and left the board in its colours.

245 tests.

## 53. Two fixes: writing on a group, and Add to board that finds its place (2026-11-10)

**Writing on a group.** Double-clicking a group always opened it, wherever
the click landed, so its title and description could not be written that
way. Now, as on a picture card, double-clicking the words edits them;
double-clicking the rest of the group card (its count, its little map) opens
it, as does its Open button. Enter on a selected group still edits.

**Add to board finds what is new, and where it goes.** It drafted from the
one answer and could link only to the cards that answer cited. Explain more
answers from the conversation and cites nothing, so the mode it is most used
with had nothing to link to; the relation was one of six fixed words; and the
card was added from a form, with no way to say afterwards it was misplaced.

- **What is new** (`ask.draft_card`). The drafter is given the conversation
  before the answer and every card the conversation brought up - what earlier
  answers cited and what their searches found, the latest first, up to 40 -
  each with its title and its first 160 characters, so it judges by their
  words. It writes only what the answer adds that those cards do not already
  say, most often what came from general knowledge.
- **Where, and how.** It picks the one card it belongs with, a kind (the six
  built-in ones), and where a kind is too coarse a label of its own of up to
  5 words ("sets the bar for"), with a one-line reason. A link it names that
  the conversation did not bring up falls back to the first one.
- **Placed, then asked** (`PlacedCheck.tsx`). The card goes on the board at
  once beside its card, linked, in one undoable step marked as made by AI;
  the view goes to it, and the chat steps aside unless docked on the left.
  Above the card: "Right place? <relation> <card>" with ✓ and ✗. ✓ keeps it.
  ✗ removes the suggested link, keeps the card, and starts a connection from
  it: the next click on a card links it there (Esc leaves it alone).
- The Experts' own add-card form is unchanged.

Tried in the app with a stand-in reply from the model (the real model was in
use by another copy of Mindboard): an Explain more answer citing nothing sent
the three cards its conversation had found; the card landed beside the table
picture, linked "supports · sets the bar for", with the prompt above it; ✗
removed the link, and a click on another card linked it there. A group's
title and description were written by double-clicking them, and a
double-click on its count opened it. Not yet tried with the real model.

247 tests.

## 54. The first page: a constellation, tile colours, summaries on hover (2026-11-10)

- **A constellation that draws itself** (`Constellation.tsx`), top right,
  behind the header, in the Experts' gold above two faint dune lines - their
  night desert without its sand. Each round places 12 stars at least 42 apart,
  then joins them one line every 0.42 s into a spanning tree, nearest first,
  plus two short links that close small loops; holds 4.5 s, fades, and draws a
  new one. Lines and stars only, all CSS - no particles, nothing per frame in
  script. Less motion asked for: one constellation, drawn, still. Hidden on
  windows narrower than 900 px.
- **A colour per tile.** A swatch button on each tile: the seven card colours,
  the Experts' gold, any colour, or none. It colours the tile's edge, a strip
  along its top, its glow and its preview. Kept in the board's settings as
  `tile`, merged in, as settings are saved whole and also hold the theme.
  Like any change to a board it moves the tile to the front of the list.
- **A summary on hover** (`summaries.py`, `GET /api/boards/{id}/summary`).
  One or two sentences, at most 40 words, written by the local model from the
  board's title, description, top-level groups and its 30 most connected
  cards, starting with the subjects, never "This board". Shown over the tile's
  preview after the pointer rests a moment. Kept in
  `<data>/search/summaries.json` (derived, not backed up) and written again
  only when the board is renamed or changes by 15%, and by at least 5 cards.
  Written only while the model is loaded and free - by the tile's request, or
  by the background indexer after a board's round - and never loaded for a
  hover; until then the tile says it is not summarised yet.

Tried in the app: the constellation drew, held and redrew in a new shape; two
tiles took gold and pink, the boards' themes and colours untouched; a summary
showed on hover below the tile's buttons, and a board without one said it was
not summarised yet. A summary has not yet been written by the real model: it
was in use by another copy of Mindboard.

251 tests.

## 55. Add to board from the Experts, the same way (2026-11-10)

The Experts kept their own form: draft, choose a board and a card, press Add.
Now they work as board chat does (§53).

- **Across the expert's boards** (`POST /api/experts/{id}/draft`). The
  drafter reads the cards of all the expert's boards, is given every card the
  conversation brought up (what answers cited and what their searches found)
  and the conversation, and the route says which board the chosen card is on.
- **Placed there, then asked there.** The card goes on that board beside its
  card, linked with the kind and label, marked as made by AI; then you are
  taken to it, and the canvas shows "Right place?" (`PlacedCheck`). With no
  card to link to, it goes on the board the answer cited most, to the right.
  The conversation is kept, as when opening a cited card, and is there when
  you come back.
- **The board's own chat steps aside** for the prompt, once that chat has
  loaded its saved state, and once per card: a chat you reopen stays open.
  Docked on the left, it stays.
- Cards added from the Experts go in through the board's op-log on the
  server, so the canvas's undo does not reach them; ✗ and deleting the card
  work as usual.

Tried in the app with a stand-in reply from the model: an answer citing
nothing sent the conversation's two cards, the card landed on the board beside
the table picture, the prompt showed with the board's chat out of the way, ✓
kept it, and the Experts page brought the conversation back.

252 tests.

## 56. Dates, a loop in Think deeper, fixing the writing; summaries removed (2026-11-12)

- **Dates.** On the SWIFT Lab Visitors board, asked for the latest visit, the
  model kept naming 3 Nov though the newest date was 11 Nov: it was never told
  today's date, and a 9B model compares dates written in words unreliably.
  Every question now carries today's date, said with the question so the
  cached start of the prompt stays put. A question about time (when, latest,
  next, month names...) also carries the dates found in its cards, worked
  out in code - "3 Nov 2026", "22 Sep and 29 Oct 2026", "Nov 3, 2026",
  ISO dates; a date with no year takes the year after it - one line per date,
  newest first, with the cards it is on (`ask.dates_note`). On that board the
  list starts "11 November 2026: Professor Daniel King; NTU Business School
  and Research Partnerships; Sarah Malone".
- **Think deeper looped.** It thought "Wait, I need to check if I should
  mention the 3 Nov meeting in the NEXT section. Yes. Okay." again and again
  until its limit. Thinking ran with the answering settings; it now uses
  Qwen's settings for thinking - temperature 0.6, top_p 0.95, and the
  presence penalty 1.5 they advise against endless repetition. And a guard
  (`ask.LoopGuard`) watches the thinking: the same real line a third time
  ends it, the model is hung up on, and the question is answered without
  thinking, saying so.
- **Fix the writing** (`ask.proofread`, `POST /api/boards/{id}/proofread`).
  A wand in each card's toolbar: the local model corrects spelling, grammar
  and punctuation in the title and text - the person's words, not a rewrite;
  lists, links, names and line breaks kept - and the card changes in one
  undoable step. A reply that drops much of the note is not taken. Tried
  with the real model: "Meting with Sara / We discused the budjet and the nex
  step is to send the reprot to Paul / - book the lab for tuesday" became
  "Meeting with Sara / We discussed the budget and the next step is to send
  the report to Paul / - book the lab for Tuesday" in 2 s; Ctrl+Z undid it.
- **Clear the conversation** asks above the chat bar in Mindboard's own style,
  Keep or Clear, instead of the browser's dialog.
- **Summaries on hover (§54) are removed** at the person's request: the
  route, the background writing, the page code and `search/summaries.json`'s
  writer are gone.

## 57. Groups of boards, and the first page's own look (2026-11-12)

- **Groups** (`home.py`, `/api/home...`). "New group" on the first page, or
  "New group..." from a tile's folder button. A board goes into a group by
  dragging its tile onto it, or from that folder button; it is in at most one,
  or in none. A group shows as a fanned stack of up to three of its boards'
  previews in their own colours, its name and how many boards it holds, in
  its own colour. Clicking it opens it: "All boards / AI work - 1 board", and
  its boards. Deleting a group keeps its boards. The first page shows groups,
  then the boards in none.
- **The page's look.** "Look", at the right of the actions: the ten themes,
  and six backgrounds - dots, grid, lines, dunes, stars, plain - each with a
  preview. A board keeps its own theme while open; the first page sets its
  own again when you come back.
- Groups and look are kept in `<data>/home.json` and travel in backups;
  restoring adds groups not here (not in "copy" mode, where boards get new
  ids), and the look only when replacing.
- **The tile colour picker** is redrawn in the page's style: a titled panel,
  eight distinct colours (a teal in place of a gold too close to the yellow),
  a tick on the current one, "Any colour" and "None". A tile lets its panel
  out instead of cutting it off. Groups use the same picker.
- **Deleting a board** asks in the page's own style too.
- Found while trying it: the board grid, empty where the Look panel opens,
  sat above the panel and took its clicks; the action row is now above it.

## 58. `mindboard update` (2026-11-12)

Menu option 9 only reinstalled: it never fetched anything new. Now it, and a
new `mindboard update`, get the latest version from GitHub first
(`updater.py`): `git fetch`, then `git pull --ff-only` on the branch the copy
follows - nothing merged, nothing overwritten - saying what came ("got 3 new
changes" and their titles), then run the installer, which installs new
packages and rebuilds the interface. Changes of your own to Mindboard's files
stop it and are named, to keep with `git stash`; a copy not cloned with git,
or no git, says what to do. Already up to date, it asks (menu) or stops
(`--force` reinstalls anyway). Mindboard must not be running. Boards are never
touched: they live in the data folder.

260 tests.

## 59. A mark for Mindboard, and dates read with their words (2026-11-12)

- **The mark** (`Logo.tsx`, `public/favicon.svg`): an ankh drawn as a small
  graph. Its teardrop loop is the mind; its arms and foot end in round nodes,
  cards linked on a board - the Experts' ankh and the boards' graphs in one
  sign. A round loop read as the ♀ symbol; the teardrop of a real ankh does
  not. It replaces the blinking dot before "Mindboard" on the first page, in
  the page's accent: the loop draws itself once, then the three nodes breathe.
  The browser tab shows it in the Experts' gold on a dark square; it reads at
  16 px.
- **Dates with their words.** On the SWIFT board the top card says "Updated
  12 Nov 2026", and the date list (§56) gave the model only the date and the
  card, so a plain answer took 12 Nov for a visit. Each date now comes with
  the words around where it is written - "...with role and email. Updated 12
  Nov 2026." beside "Visit: Lab visit on 11 Nov 2026" - and the model is told
  an updated or reported date is not a visit. With the real model, Think
  deeper answered "The most recent specific lab visit recorded is 11 November
  2026... The master visitor list was updated on 12 November 2026", in 49 s
  and without looping; the plain answer named 11 Nov as the latest visit too.

260 tests.

## 60. A question waits its turn instead of being refused (2026-11-16)

Asked on one board, answered in full, then asked on another: "still answering
the last question". Nothing was answering. When an answer ends, and when a
board is opened, the indexer gets a moment with the free model to describe
the board's pictures (§49, §51) - longer descriptions now, old short ones
redone while the model is loaded. It held the one question slot for each
picture, and a question arriving meanwhile was refused at once: the board
route said 409 if the slot was taken, the model's stream said busy if it
could not take it that instant.

- **A question waits** (`LocalModel.take_turn`, `TURN_WAIT` = 120 s): it
  counts itself in `waiting` and waits for the slot. The board's answer is
  held for its whole run by `stream_answer` - the answer and, if it loops, the
  plain retry (§56) - like an expert's. Only a model that never comes free is
  "still answering". Fixing a card's writing, drafting a card and the Experts'
  guide wait up to 30 s (`SHORT_WAIT`).
- **Background work steps aside.** The indexer describes one picture at a
  time and starts none while a question is `waiting`, so a question waits at
  most for the picture already under way, then goes first. The rest wait for
  the next quiet moment.
- **Experts**: a new question still stops the answer being written (§45), and
  now waits for the slot rather than giving up after 6 s - a picture being
  described cannot be stopped mid-way, only finished.

260 tests.

## 61. Formulas drawn as maths (2026-11-16)

A picture of a formula is described in LaTeX (`$\arg\max_{\text{prompt}}
P_\theta(Y \mid \text{prompt})$`), and that text reached cards and answers as
code. Worse, an answer's light formatting took the formula's underscores for
italics, and half a line went slanted.

- **KaTeX, bundled** (`web/src/Math.tsx`): about 280 KB and its fonts, built
  into the interface, so it works offline. `$$...$$` and `\[...\]` are drawn
  on their own line, `$...$` and `\(...\)` in the sentence.
- **Maths first.** In an answer (`Prose.tsx`) formulas are found before bold,
  italics and citations, so a formula's `_` and `*` stay maths. On a card the
  title and text are drawn with their formulas too.
- **Prices stay prices.** A `$...$` has no space just inside either dollar,
  is not followed by a letter or digit, and is not only a number: "$5 and
  $10" and "$5-$10" are text. A formula KaTeX cannot read is shown as the text
  it was, never an error. Hovering a formula shows its LaTeX; Copy copies it.
- **KaTeX's HTML** is made by KaTeX from its own reading of the formula, with
  trust off - no links, styles or raw HTML from the text.
- **Asked for**: the picture reader writes any formula in LaTeX between single
  dollar signs, and answers write formulas that way too, so both come out in
  the one form that is drawn.
- Cards break long words anywhere and keep line breaks; inside a formula that
  put `\text{prompt}` one letter per line. A formula keeps together, and a
  long one on its own line scrolls.

260 tests.

## 62. Pictures pasted into the chat (2026-11-16)

Paste a picture into a chat box (Ctrl/⌘+V), drop one on it, or choose one
with the picture button, and ask about it - "explain this", "who is this
visitor" - answered from the picture and the board together. Board chat and
an expert's chat alike (not the Experts' guide, which only picks who to ask).

- **In the box** (`Pasted.tsx`): up to four pictures wait in a row on top of
  the bar, each removable, with a note that they go to this computer's model
  only. They are shrunk in the browser first (1600 px JPEG; a phone photo is
  megabytes), and a 180 px copy is kept to show in the question - in the tab,
  like the rest of the conversation. A picture with no words asks "Explain
  this picture." A pasted picture is never put on the board; Add to board
  turns the answer into a card as usual.
- **On the server** (`ask.take_pasted`): read again with Pillow, shrunk to
  1024 px and re-encoded as JPEG - whatever was pasted, the model gets a plain
  picture; anything else is refused at once (400).
- **Looked at, then searched by.** "Explain this" says nothing a search can
  use, so with the model held the picture is first described in two or three
  sentences (`describe_pasted`, 220 tokens) and the question is searched and
  planned with that description (`with_pasted`). The page is told what the
  model saw (`pasted`) and keeps it with the question, so a follow-up still
  knows the picture without sending it again. Stop during the look stops.
- **Answered with the picture itself**, placed with the newest question after
  the cards (`ctx.attached`), so the start of the prompt and llama-server's
  cache stay put, and counted in what fits.
- With the real model a pasted visitor badge was read ("Dr. Maria Keller, ETH
  Zurich, 11 Nov 2026"), the board searched by it - no card for her, but the
  visit on the same day found and cited; an expert answered in 22 s.

263 tests.

## 63. Board off: the model alone (2026-11-16)

Some questions are not about the board - a general explanation, a pasted
picture to explain, a quick fact. Explain more (§34) skips the search but
still sends the cards the conversation used, and the instructions still put
the board first. **Board off**, a switch in the bar beside Think deeper,
leaves the board out entirely:

- **Nothing from the board** (`own: true`, `own_context`): no search, no
  cards, no date list from cards - today's date only. Explain more and chosen
  cards are ignored while it is on.
- **Its own instructions** (`OWN_SYSTEM`): a plain assistant that does not
  talk of cards or a board, still short by default, formulas in `$...$`, and
  the three next questions. The prompt is tiny, so it answers quickly.
- **Only Board off turns as the conversation.** The first try sent the whole
  conversation, and an earlier answer from the board brought its facts back
  in: asked "who visited on 11 Nov?", it named the visitor from the board
  under the label "not from your board". Now a Board off question carries
  only earlier Board off questions and answers; asked afresh, the model had
  no idea who visited.
- **A pasted picture** goes straight to the model - with nothing to search,
  it is not described first.
- **Marked**: each answer is headed "Not from your board - the model's own
  knowledge", in the dashed frame of "Beyond your board"; the context strip
  says the board is not searched or sent; the box says "Ask without your
  board…". It stays on (per board, in the tab) until switched off, like Think
  deeper. Add to board still turns an answer into a card.
- Board chat only: an expert is its boards, so it has no Board off.

264 tests.

## 64. Asking one file: prepared, then searched on its own (2026-11-16)

A file is never put into the model's context whole. To ask a PDF, Word
document, Excel workbook, CSV or text file, it is **prepared** first and
then **searched inside itself only**, the way a board is searched - in a
conversation of its own beside the board's.

- **Ways in**: the file button in the chat bar, or a file dropped or pasted
  on it (a file added this way goes on the board only with "Add file to
  board"); "Ask this file" beside a PDF an answer offers; the ask button on a
  file card on the board.
- **A branch of its own**: each file is a tab above the conversation, next
  to "Board", with its own questions and answers; switching keeps both
  (`askStore`: `branches`, `active`, `boardTurns`). Board off and Explain more
  do not apply in a file's tab. Add to board works as before, and links the
  new card to the file's card when the file is on the board.
- **Preparing** (`filechat.py`, on its own thread, progress in the strip):
  read into passages of about 1,100 characters with some overlap - a PDF by
  page with pdfium, a Word document under its headings plus its tables, a
  sheet or CSV row by row with the header's names in every row; figures
  described by the model once (a PDF page with pictures, many drawn lines or
  little text - charts and scans - and a Word document's pictures, 24 at
  most), so a question finds them by what they show; a keyword index (BM25,
  Tantivy, in memory) and a vector per passage.
- **Asking**: exact phrases, keywords and meaning fused by reciprocal rank,
  the best 30 reranked, then passages taken best first up to 5,000 tokens
  (12 at most), sent in the file's order, each labelled where it is. "What
  is this about?" also takes the file's start. The model cites "(p. 12)",
  which opens the file at that page.
- **Multimodal, smartly**: a page that is mostly a figure, a table, formulas
  or a scan goes to the model as a picture of the page when one of its
  passages is among the best (2 pages at most); text pages go as text. A
  picture costs about 1,300 tokens and some seconds, so only pages that need
  it pay. Under the answer, the pages it was given, small, the ones it saw as
  pictures outlined.
- **Temporary**: in memory and under `<data>/tmp/filechat`, removed when its
  tab is closed, after six hours unused, and whenever Mindboard starts. A
  board's file is read where it is, never changed. Boards and their search
  are untouched.
- New dependencies: pypdfium2 (pages drawn and counted), python-docx,
  openpyxl.
- With the real model, a four-page report with a bar chart drawn as a
  picture: prepared in seconds; "which month had the most visitors?" was
  answered from the chart page (November, 16), and "how much does the depot
  cost?" with "(p. 2)" linked to page 2. Closing the tab removed it on the
  server.

Not yet: Experts' conversations, older .doc and .xls files, and sums or
averages over a whole column (a sheet is searched row by row).

270 tests.

## 65. A spreadsheet's tools, chosen by the model (2026-11-16)

Searching a workbook row by row (§64) finds what a row says, but a total over
three hundred rows is in no passage. So an Excel or CSV file asked on its own
is also loaded, while it is prepared, into an in-memory SQLite database - a
table a sheet, every row up to 200,000, a column numeric when 80% of it reads
as numbers ("1,800", "£4.20" and "12%" included), dates as YYYY-MM-DD so they
compare as dates (`sheets.py`).

- **The model decides.** Before each answer it is shown the sheets' columns,
  types and a few example values, and the question, and answers in JSON:
  which tools, if any (`decide`). Most questions about one row need none.
  It is told which operation fits which wording - "which region had the
  most visitors" is a sum per region, not the largest single visit; the
  first real try picked max and the answer, rightly, said it could not tell.
- **Three tools, at most three calls**: `calculate` (sum, average, min, max,
  count, count distinct, median; filters; per group, sorted), `rows` (the
  rows matching filters, sorted, 30 at most), `distinct` (a column's values,
  most common first). Structured, never SQL from the model: every sheet and
  column is checked against the file, every value passed as a parameter, so
  nothing it writes can change or escape the data - a filter of "'; DROP
  TABLE" is refused, a "contains" of "%' OR 1=1 --" matches nothing.
- **Facts only.** Results go to the answer marked CALCULATED over the whole
  sheet, and the model says "(calculated)" after figures it takes from them.
  A tool that fails, and a choice that cannot be made, are listed as NOT
  CALCULATED with the instruction not to work those figures out from the
  passages - they are only part of the sheet. For every file, the model is
  told never to present a total or "all of them" from passages, and never to
  fill a gap with a guess. Under the answer, what was calculated, and what
  could not be, in its own box.
- **Slow or failing steps**: choosing tools is cut off after 120 s (the
  reply is hung up, so llama-server stops) and the answer goes on from the
  passages, saying no calculation was made; a calculation is stopped after 5
  s; a figure that fails or takes over 120 s to describe while preparing is
  skipped and said in the strip; a preparation that has not moved for two
  minutes says so and offers to stop; the page tries again, more slowly,
  when the server does not answer for a moment, and only "not found" ends a
  file's tab (after a restart it says to prepare or add the file again).
- With the real model on a 300-row visits workbook (true totals known): "how
  many visitors came from Europe?" - 365 (calculated), exact; "which region
  had the most visitors?" - East Midlands, 971, exact; "what did MIT's
  visitors do?" - the sheet has no such column, and it said so.

274 tests.

## 66. Understanding the request, then doing it in steps (2026-11-16)

"Summarize this for me" with a pasted slide got a description of the slide:
the chat sent the question to the model in one shot, and nothing worked out
first that a summary was asked for. Experts planned (§47); the board's chat,
Board off and a file's tab did not. Now every question there goes through
three stages (`tasks.py`), inside the run that holds the model:

1. **Understand** (one call, JSON, 60 s at most): the task - answer,
   summarize, explain, compare, extract, analyze, rewrite, translate, plan -
   what it is about, the output asked for ("short", "a table", "in
   Persian"), the request read back ("You want ..."), and steps only when one
   look is not enough. The model is told what is already in front of it and
   which actions exist here.
2. **Chain the steps** (ReAct-style, 4 at most): `read` the pasted pictures
   in full (every line, figures described), `search` the board or the file
   (a board search's cards join the answer's, so they can be cited), `work
   out` something from the earlier steps - each step's result handed to the
   next. After the last of two or more, a check: is anything the task needs
   plainly missing? One more step may be added, once. A step that fails is
   marked and hands on nothing; one that takes over 150 s is cut off (hung
   up, so llama-server stops).
3. **Write** with what the steps found and the task's shape said with the
   question (`ctx.task_note`, after the cached start): a summary as its gist,
   the key points under the source's own headings, and what it means; a
   comparison as a table; a list with each item's source; a plan as numbered
   steps; and the output asked for over all of it. Every part answered, every
   source cited, and no empty "Beyond your board: None".

Tuning, with the real model: a plain question at first got steps - "when was
the latest visit?" planned a search, which only repeated the search already
made, took 17 s and lost the citations. The planner is now told most
requests need none, and a plan of one search is dropped. Then the
"extract" shape's "no commentary" cut the answer to a bare date; it now
lists each item with its source and answers every part. Now: the slide -
Summary, short, 2 steps (read it, work out its points) - came back as a gist
line, Objective / What to monitor / Implementation / Outcome, and what it
means; the latest-visit question - no steps, about 5 s more than before -
gave the date, both visitors and their cards; comparing two visits ran three
steps and gave a cited table.

The page shows the task above the answer - its kind, the output asked for,
"You want ..." - and the steps live ("Step 2 of 3: work out ..."), folded
into "2 steps" once the answer is written. If the request cannot be
understood in time, it is answered as it stands, as before. Experts' planner
knows the same kinds and answers them in the same shapes.

281 tests.

## 67. Back from a board returns to the group it was opened from (2026-11-16)

Opened from inside a group on the first page, a board's back led to the
first page, not the group. Two causes:

- **The open group was forgotten**: it was the first page's own state, lost
  when the page gave way to the board. It is now kept for the tab
  (`sessionStorage` `mb_home_inside`) and restored when the first page comes
  back; "All boards" clears it, and a group that no longer exists is let go.
- **Opening a board replaced the page in the browser's history** instead of
  adding to it (`history.replaceState` in `App.tsx`), so the browser's back
  button skipped the first page and landed on the welcome page. Opening a
  board is now a step forward (`pushState`, marked `mbBoard`), and leaving by
  the board's own button steps back through it (`history.back()`), so both
  ways back agree. From an expert's conversation the same holds: back
  returns to the Experts.

Checked in the browser: group "AI work" → board → browser back, and → board →
the board's back button: both land inside "AI work"; "All boards" leaves it.

281 tests.

## 68. Scrolling while it writes, panels that put themselves away, new boards light (2026-11-16)

- **Scrolling during an answer.** The chat follows an answer as it is
  written, and let go only when the scroll position was well away from the
  bottom. A smooth wheel scroll moves a few pixels at a time; the next words
  arrived first and pulled the view back down, so while an answer was being
  written it could not be scrolled at all. Now the reader's own moves upward
  let go at once - wheel up, touch, the arrow, Page Up and Home keys, a press
  on the scrollbar - and only coming back near the bottom (or wheeling down
  near it) picks the answer up again; the page's own scrolls never let go. A
  first try that ignored the page's own scroll events by a flag got stuck
  when two scrolls arrived as one event, and the answer was never picked up
  again; dropped. The Experts' conversation had the same trouble and the
  same fix.
- **A click on the board puts panels away.** The board's look (theme panel)
  and the chat close when the empty board is clicked. The chat has a pin in
  its header to stay open while working on the board (kept in
  `localStorage`); the expanded chat still closes by its dimmed backdrop.
- **The theme panel saves as you change it** - it always did: every pick is
  an op on the board at once. "Done" made it look otherwise; it is replaced
  by a close button, "Saved as you change it", Escape, and the board click.
- **New boards start light**: `DEFAULT_SETTINGS.theme` is `daylight`
  (near-white canvas, white cards). Boards made before keep their theme.

Checked in the browser: scrolled up while an answer was written and stayed
there; back at the bottom it followed again; an unpinned chat closed on a
board click and a pinned one stayed; the theme panel closed on a board click;
a new board opened on daylight.

282 tests.

## 69. Markdown in cards, and the canvas's pattern (2026-11-16)

- **Markdown in a card.** A card showed paragraphs and simple lists only.
  Its text is now drawn by the chat's own renderer (`Prose`), so a card takes
  what an answer does: headings, **bold**, _italics_, `code` and code blocks,
  quotes, tables, rules, numbered and nested lists, formulas - and three
  things added to the renderer for both: links (`[words](https://...)` and
  bare addresses, http and https only, opening in a new tab, a press on one
  not taken as a press on the card), ~~strikethrough~~, and task items
  (`- [ ]`, `- [x]`, drawn as boxes, done ones struck through). A card keeps
  its line breaks as written (`breaks`); an answer's lines still run on as a
  paragraph. A card named in a card's text (`[[id]]`) shows as its title and
  goes to it. Sizes are in em, so a card's text size carries to all of it.
  Editing is unchanged: the card's text is written as plain text, and drawn
  when it is not being edited.
- **The canvas's pattern** is chosen in the board's theme panel, beside the
  themes: dots (as before, the default), grid, large grid (a fine grid with a
  stronger line every five squares), crosses, or plain. Kept in the board's
  settings (`pattern`) like its theme; drawn in the theme's grid colour.

Checked in the browser: a card with a heading, bold and italics, a kept line
break, a done and an open task, a numbered list, a quote, inline code,
strikethrough, a formula, a table and both kinds of link drew each one; each
pattern drew on the canvas and the choice was saved with the board.

282 tests.

## 70. A long question takes its own row; no file button in the chat (2026-11-21)

- **A long question.** The open bar is one row: the ankh, the question box,
  then the tools (picture, Explain more, Board on, Think deeper, send). On a
  window 1,024 px wide the bar is 424 px, the tools take 280 px, and the box
  was left 77 px - a long question stood in a column a few letters wide
  inside a tall pill. Now a question that does not fit on one line beside
  the tools, or that has a line break, moves to a row of its own across the
  whole bar, with the ankh and the tools under it; the bar's ends round to
  22 px instead of a pill. The box still grows to five lines (120 px) and
  then scrolls inside itself.
  The test is the text's measured width against the room one row would
  leave (the bar less its padding, the ankh, the tools and the gaps), not
  the box's own height: the height depends on which layout is showing, so a
  first try that used it stuck in two rows - and while the bar opens from
  the small pill the box measured many lines. The test is taken again
  whenever the bar's width changes.
- **The file button is gone.** "Ask a file" sat beside the picture button
  and was clicked by mistake while working. A file is still asked by
  dragging or pasting it into the chat, or from a file card on the board.

Checked in the browser at 1,024 and 1,600 px wide: a 21-character question
stays on one row at 1,600 (two at 1,024, where one row leaves 77 px); an
89-character question and one with a line break move to two rows and come
back when shortened; a 2,000-character question shows five lines with a
scroll bar, the tools beneath; clearing the box returns the bar to its pill.

282 tests.

## 71. The card's tools: one compact bar above the card (2026-11-21)

A selected card opened a panel above it, three rows tall: colours and fill,
text size and lists, then fix the writing, context, connect, add connected,
move out, duplicate, delete. It covered the cards above the selected one and
grew with every new action.

Now one compact bar sits just above the selected card (`CardTools` in
`nodes.tsx`), four buttons, each opening only what belongs to it:

- **Colour** (a dot in the card's colour): six swatches, any colour, default
  colour, and the three fills (bar, tinted, solid).
- **Aa**: text size by name - S, M, L, XL (11, 13, 16, 20 px), the nearest
  marked, so a card with another size keeps it.
- **AI** (sparkles): the card's AI actions - fix the writing, ask about it,
  and since §72 summarise, explain, quiz, fun fact, break up, find related.
- **Connect** (a link): one click, as before; `c` still works.

Taken off the bar at the owner's word, to keep it to four: the "more" menu
(add connected card, duplicate, delete, open, ungroup, move out) and the two
lists. They remain where they already were - the ports add a connected
card, Ctrl+D duplicates, Del deletes, a double-click opens a group, Ctrl+G /
Ctrl+Shift+G group and ungroup -
and lists are to go into the card's editor. Not done yet: the lists in the
editor, and Move out has no button now.

Menus open above the bar, so the card stays in view while it changes. The bar
is in xyflow's `NodeToolbar`: one size at any zoom, shown only while that card
alone is selected, hidden in exported pictures. Its surface is the app's
floating panel (the selection bar's border and shadow, a faint lit top edge),
in every theme's own colours.

- **Any colour**: the rainbow swatch opens the system colour picker; the
  choice is kept in `style.color` and applied once, when the picker closes,
  not as an op for every step of a drag across it.
- **A fix found on the way**: any change to the board rebuilt the canvas's
  list of cards without their selection, so picking a colour deselected the
  card and put its tools away. The cards in hand are now kept selected when
  the level is rebuilt.

Tried and dropped, on the owner's look at each, the same day: a pill at the
top of the canvas for the actions with the look beside the card - first a
colour dot opening a strip down the card's right side (glossy "3D" beads,
then flat; it sat on the resize corner, covered the right port, and was
awkward), then a format row above the card. Two menus for one card were
confusing. One wide bar with everything, everything in the top pill, and
formatting behind an "Aa" button were drawn and not chosen. Text size as
smaller / number / larger, and then as a numbered list, was confusing; named
sizes replace both.

Keyboard and undo are as before; every change is still an op. Checked in the
browser on a dark and a light theme at 87 % zoom: each menu opened alone and
above the bar; a swatch, L then XL, and the fills changed the card while it
stayed selected; the AI menu listed its two items. The old panel, the top
pill and their styles are gone.

282 tests.

## 72. AI on a card, and on several (2026-11-21)

The card bar's sparkle button (§71) held two things: fix the writing, and put
the card in the chat's context. The owner asked for more that a learner does
with one card, and for some of it on several cards at once.

**One card** - the sparkle menu, in three groups:

- **Summarise, Explain this card, Quiz me on this, Fun fact** go through the
  chat: the card alone is put in context and the request is sent in words the
  person sees in the conversation, so the answer cites the card like any
  other. Each is sent *fresh* - without the conversation before it - and names
  the card by its title: sent with the history, "this card" was read as the
  card the conversation had last been about (a fun fact for the University of
  Derby card came back about David Astill).
  - Fun fact: only from what the card says; if it holds too little, one short
    funny line ending with an emoji instead.
  - Quiz: one question at a time, a cheer and a short explanation after each
    answer, the score, three questions (four for several cards). Asked as
    steps, the request reader (§66) took it as a plan and wrote a plan for a
    quiz, twice, however it was worded; so `quiz` is now a task of its own
    with its own answer shape (`tasks.SHAPES["quiz"]`): judge the last answer
    if there was one, then ask only the next question, never its answer, and
    make the three NEXT lines answers to pick from. The page names it Quiz.
- **Break it into cards** (`POST /cards/{id}/break`, `ask.break_card`): 2 to 6
  cards drafted from the card's own words, nothing added. They are listed in
  the menu with a tick each; "Add N cards" places the ticked ones beside the
  card, each linked to it as *part of*, in one undoable step. When the model
  says one idea or its reply cannot be used, the card's own list items, else
  its paragraphs, are offered; failing those, it says there is one idea.
- **Find related cards** (`GET /cards/{id}/related`, `ask.related_cards`): the
  board's own search (§35) run on the card's title and text, leaving out the
  card, the cards it already links to, the groups it sits in and anything it
  holds. Up to six, each with a Link button and a click to go to it. Uses the
  search models only, not the question slot.
- Fix the writing and Ask about this card (into the chat's context) as before.

**Several cards** - an AI button on the selection bar:

- **Summarise these, Quiz me on these** - through the chat, as above.
- **Merge into one card** (`POST /cards/merge`, `ask.merge_cards`, 2 to 8
  cards, not groups): one card drafted from their own words, every distinct
  point kept. It is shown, editable, with "Replace with this"; accepted, the
  merged card takes the place of the first, every link the cards had to cards
  outside them is moved to it (kind and label kept, a link two of them shared
  made once), and the cards go - one step, so Ctrl+Z brings them all back. A
  reply that drops most of the words, or cannot be read, gives the cards side
  by side instead.
- The selection bar now keeps one size at any zoom (it was drawn inside the
  board and shrank with it; at 12 % the merge draft was unreadable).

All drafts are offered, never written, until the person accepts (CLAUDE.md
rule 1); the model's JSON is parsed between the first `{` and last `}` and
every field checked (rule 8); each call has a time limit (90 s). A menu opens
above its bar unless the window's top would cut it off; then below.

Measured with the real model (Qwen3.5) on the SWIFT Lab Visitors board: break
15 s cold, 3 s warm, five cards taken faithfully from a four-line card; related
2.8 s, the other visitors and their groups; merge 7.6 s, all points of two
cards kept; Summarise 6.6 s, cited; fun fact 4 s, a playful refusal with 🤷 on
a card holding only a title; the quiz asked question 1, then judged "B"
right with a 🎉, gave 1/3 and asked question 2.

**Three weak spots, then fixed** (the same day):

- *A quiz question with three right choices*: "what is his official title?"
  over a card listing three roles. The quiz shape now asks for exactly one
  right answer by the cards, the wrong choices plainly wrong by them - never
  another true fact from them. Two runs after: "which is NOT one of his
  roles?" and "his role at Nottingham City Transport?" - one right each.
- *Hub cards among the related*: the board's root, "External visitors",
  "Internal visitors". Leaving out cards linked to six or more others was
  tried first and was wrong both ways: the root had 2 links and "External
  visitors" 4, while two meeting cards with real content had 6 and 7. What
  marks structure on a board is being held: a card that two or more cards are
  *part of* (`HOLDS`) - categories, organisations, meetings - is left out, as
  groups are. For David Astill the six are now all people.
- *Drafted cards placed without looking*: `actions.columnSpots` finds the
  first column to the card's right where all of them fit clear of every card
  on the level, starting level with it and stepping down past anything in the
  way, further right if crowded. Five cards from David Astill's: no overlap
  with any card, measured against their drawn sizes. `freeSpot` (pasting) read
  a self-sizing card's width as 0 (`??` on 0); it now uses the guess.
- Found on the way: a later run said "one idea" of the same four-line card;
  the fallback now offers a card's separate lines when it has neither list
  items nor paragraphs.

**The quiz, played through** (asked after the weak spots): the three NEXT
lines meant to be the answers to pick never came - told to stop and wait for
an answer, the model writes none. So the server reads them from the question:
an answer with no NEXT lines that ends with a run of two to four lettered
choices, A) to D) in order, close to the end (`ask.choices`), sends those
as its offers, marked `choices`; the page heads them "Your answer" and a tap
sends the choice as the answer. Played to the end with the real model: three
questions answered by tapping, a kind correction for a wrong pick, a score
after each, a final score; then the offers went back to questions. Two
questions in that run were not answerable from the card (what "MBE" stands
for; what kind of company Nottingham City Transport is - with no right
choice). The quiz shape now says to find the right answer written in the
cards before asking, and the request itself asks for exactly one right choice;
the next full run had three answerable questions, and three of four first
questions on two selected cards had exactly one right choice (the fourth run's
selection could not be confirmed). Summarise these, on the same two cards:
4.5 s, the people, the visits and the reports.

291 tests.

## 73. The app's own tooltip (2026-11-21)

Every button names itself with `title`, about a hundred of them, and the
browser drew each as its own plain white box - foreign beside the app's
panels, and the same on a dark board as on a light one.

Rather than edit each button, one small module (`tooltip.ts`, installed in
`main.tsx`) takes them over. On the first hover over an element with a
`title`, the title moves to `data-tip`, so the browser's box never appears;
an element with no other name keeps it as its `aria-label`, so screen readers
lose nothing. After 450 ms of resting on it, the words are drawn in a small
panel with the floating-panel surface, border and shadow (`.mb-tip`), in the
board's theme, below the element - above it when there is no room below -
and always inside the window. A shortcut written after two spaces in
brackets ("Fit to view  (Ctrl+0)") is set apart as a key. A press, a key, a
scroll, a wheel or leaving the element hides it; touch gets none. A title
React writes again later is taken over on the next hover.

Not chosen: removing the titles - the icon-only buttons (the card bar's,
the dock's) would then say nothing at all.

Checked in the browser on a light and a dark theme: hovering "Fit to view"
showed the panel above the dock with Ctrl+0 as a key, the button kept no
`title` and gained its `aria-label`; "Minimap" on the dark theme drew dark.

291 tests.

## 74. Node.js in the installers, and a guide for agents (2026-11-21)

**Node.js.** The interface is built from `web/` into `src/mindboard/web/`,
which git does not carry, so every install builds it - and building needs
Node.js. What the installers did:

- `install.sh` rebuilt a missing or out-of-date interface, and stopped with
  "brew install node" when Node.js was missing.
- `install.ps1` did not build at all; it left it to `mindboard install`, which
  builds only a *missing* interface. A fresh Windows copy without Node.js got
  no interface, and - found on the way - every update on Windows (which runs
  `install.ps1` again) kept the old interface over the new code.
- Building empties the output folder, including the tracked placeholder
  `src/mindboard/web/.gitkeep`; git then sees a deleted file, and `mindboard
  update` refuses to run over "changed" files. Nothing put it back.

Now both installers rebuild when the interface is missing or older than any
file in `web/src`, `web/index.html` or `web/package.json`, and put the
placeholder back after building. When Node.js is missing they offer to install
it - `winget install OpenJS.NodeJS.LTS` on Windows; Homebrew, apt, dnf, pacman
or zypper elsewhere - asked, never done unasked; `MINDBOARD_NODE=yes|no`
answers for someone not at the keyboard. Declined, with an older interface
present, they carry on with it and say so; with none, they stop and name the
command. Windows also looks for `npm` where winget puts Node.js, since a
window opened before the install does not have it on its PATH. `mindboard
install` names the command for the system it runs on. The README now says
Node.js is needed on every system, with the commands.

**AGENTS.md** - for an AI coding agent asked to install Mindboard: ground
rules (ask before anything large or system-wide, never enter the person's
password, never edit, commit or discard Mindboard's files, report what
happened); a table of what to check (Python 3.10+, git, Node.js, uv, disk,
graphics card) with the install command for each system; clone; install with
the questions answered in advance; check with `version`, `doctor` and
`where`; the AI as an optional, asked step (the three model sizes, `--yes`
only after the person agreed to the size the installer states, `ai check`,
the voice); starting and handing over; updating; and a troubleshooting table
of the installers' own messages.

Checked on Windows in a scratch environment and data folder: an out-of-date
interface was rebuilt and `git status` stayed clean; a second run said "up to
date"; with Node.js off the PATH, `npm` was found where winget installs it.
The Mac/Linux build step was run in Git Bash: declined with no package
manager it warned and carried on; with Node.js it built and put the
placeholder back. Not yet tried: the Mac and Linux installers on a real Mac
or Linux, the winget install itself on a computer without Node.js, and an
agent following AGENTS.md on a clean computer.

291 tests.

## 75. Card actions said plainly, a centred bar, and a missing route said (2026-11-21)

What the owner saw using the card AI (§72) on a running Mindboard:

- **"Understanding your request…" for Summarise, and "LIST" over a fun fact.**
  A card action went through the request reader (§66) like any typed
  question: a wait, and sometimes a wrong reading. A card action knows its
  task, so it now says so - `AskRequest.task` (`summarize`, `explain`,
  `quiz`, `funfact`, `tasks.KNOWN`) - and `tasks.run(..., known=)` takes it
  as it is: no reader call, the task event at once, the answer shaped for it.
  The page says what it is doing until the first word - "Summarising…",
  "Explaining…", "Making your quiz…", "Finding a fun fact…" (`Turn.doing`) -
  and labels the answer Summary, Explanation, Quiz or Fun fact.
- **The request shown as if the person had typed it** - a paragraph of
  instructions under the card's name. The chat now shows a short line ("Fun
  fact: Analogy", "Summarise: 2 cards") and keeps the request for the model
  (`Turn.prompt`, used in the conversation sent back too, so a quiz still
  knows what it is playing). The requests are short now ("One fun fact from
  the card \"Analogy\".") because the task's shape carries the rules.
- **The fun fact read the card back** ("Analogy is a central ability of human
  cognition..."). It has a shape of its own (`SHAPES["funfact"]`): the most
  surprising detail the cards hold - a number, a contrast, an odd name, a
  link - in one or two playful sentences beginning "Fun fact:", with an emoji,
  never the opening line or definition restated; a short funny line with 🤷
  when there is nothing to surprise.
- **Find related cards: "Unexpected token '<', "<!doctype"..."** The owner's
  Mindboard had been started before the route existed, and the interface's
  catch-all answered the unknown `/api/...` address with the page itself, which
  the page could not read as data. Addresses under `/api/` that no route
  answers are now a 404 saying Mindboard needs restarting, and the page says
  the same for any non-JSON answer, instead of a parser's words. A restart
  is what fixes the owner's case.
- **The bar sits centred above the card**, and every menu centred on the bar
  (`translate`, so the pop animation does not undo it). **The size menu is
  one tight row** - no "Size" heading, 151 × 37 px.

Checked with the real model on the SWIFT Lab Visitors board: Summarise showed
"Summarise: David Astill MBE FCILT" in the chat, "Starting the model…" then
"Summarising…", and a Summary; Fun fact showed "Finding a fun fact…" and an
answer labelled Fun fact beginning "Fun fact:" (2.3 s) - still plain, from a
card that lists only roles and dates. Card, bar and size menu share one
centre line at 80 % zoom.

293 tests.

## 76. Cards in soft glass (2026-11-21)

The owner found the cards basic: a flat box with a 2 px colour bar across the
top. Three directions were drawn - soft glass, a header strip naming the
card's kind with its link count, and a very round card with a coloured tag -
and soft glass chosen: it changes how a card looks without adding anything
to read on every card, which matters on a board of hundreds.

- **Rounder** (14 px) and **lit**: the surface brightens a little towards the
  top (the theme's own card colour mixed with 6 % white, so a light theme's
  white card stays white) with a faint light line along the top edge.
- **The colour down the left edge** in place of the bar across the top: a
  3 px edge inset from the corners, glowing softly when the card has a
  colour of its own; a card without one shows its type's colour, quieter.
  The bar, tinted and solid tones are unchanged.
- **Lifted under the pointer and while dragged** in the theme's larger shadow
  (it was a fixed black shadow, heavy on light themes); **selected** with the
  accent border and a soft 3 px ring.
- Text sits a little further from the edges (11-14 px; 16 px on the left,
  past the colour edge), titles 13.5 px.
- The card bar (§71) moved from 16 to 30 px above the card - the owner found
  it too close.

Checked in the browser at 103 % on a dark and a light theme: coloured cards
showed the glowing edge, the selected one the ring; on the light theme the
cards stayed white with a soft shadow.

293 tests.

## 77. AI results beside the card, a quiz as data, and drop to connect (2026-11-21)

From the owner, after using the card AI (§72, §75):

- **"The chat box is only for asking questions."** Summarise, Explain, Fun
  fact and the quiz now open in a panel beside the card (or the selection),
  in place of its AI menu - never in the chat. Summarise, Explain and Fun
  fact go through the same route as the chat with their task known (§75) and
  no conversation; the answer streams into the panel ("Summarising…" first),
  with Copy and Add to board (a card linked to the first one, its citations
  taken out). Going back or closing hangs up, so the model stops.
- **A quiz as structured data.** The model wrote its choices into the
  question's sentence - "A) Animal instinct B) Human cognition C) ..." - and
  a quiz played in prose had to be read back out of it. Now each question is
  asked for on its own (`POST /cards/quiz`, `ask.quiz_question`) as JSON -
  question, 3-4 choices, the right one's position, and why - checked (one
  right, none repeated, the answer in range; tried twice), choices without
  letters, and shuffled, since the model puts the right one first. The panel
  shows them as buttons, marks the pick at once - right in green, wrong in red
  with the right letter and the reason - keeps score, asks for the next
  question with the earlier ones listed so none repeats, and ends with the
  score and Play again.
- **Merge** showed Python's list syntax - "['Analogy is ...', "It has
  ..."]" - in the draft and then on the merged card: the model sent its text
  as a list. A field sent as a list is now joined into lines (`ask._plain`),
  in Break, Merge and the quiz alike. The draft's text box could not be
  scrolled: the wheel zoomed the board. The bars and their menus are now
  `nowheel`, so the wheel scrolls what it is over. Its button was grey: the
  selection bar's button style reached every button inside it; it is now
  limited to the bar's own buttons, and the main action button is solid
  white on the accent.
- **A menu that grows after it opens** - a quiz question arriving - ran off
  the top of the window; the above-or-below check now runs again whenever it
  changes size.
- **The card bar has Delete** after Connect.
- **Hover texts are short names**, not explanations: "Think deeper", "Explain
  more", "Pin open", "Ask this file", "Back up all boards" - about forty were
  shortened; a shortcut stays, set apart (§73).
- **Connecting:** a link dragged from a card's port and let go anywhere over
  another card - not only on one of its four ports - is made to that card.
  Let go over empty board, it still makes a new connected card.

Not done: "when I click on break it down it shows..." - the owner's sentence
was cut off; the list-text fix above may be what it was.

Checked with the real model on the SWIFT Lab Visitors board: the quiz gave
four separate choices, a wrong pick showed "Not quite - it is B." with the
card's words, and the chat gained nothing; Summarise streamed into the
panel in 5.9 s with Copy and Add to board; the merge draft came back as
lines, scrolled with the wheel, its button white on blue, and the merged
card showed a list; a drag let go over the middle of another card made one
link and no new card.

296 tests.

## 78. A plainer fun fact, and Beautify (2026-11-21)

**The fun fact** opened with "Fun fact:" under a panel already headed Fun
fact, or with "The most delightful detail is that ...", and once compared a
card's date with today's and called a past visit "from the future". Its shape
now says to state the fact itself, without a label or that opener, and to
work nothing out - no sums of dates, no comparing with today; the panel
strips such an opener if it comes anyway, and moves a full stop left alone
after a citation back before it. A fun fact is no longer given today's date
(`Context.no_dates`; the dates note is left out for it). Three runs after:
no label, no opener; one still called a date "future" from the card's own
words - the model's slip, said here.

**Beautify** - asked for by the owner: the card's text laid out as Markdown,
the same words (`POST /beautify`, `ask.beautify`). Separate items become a
bullet list, ordered steps a numbered one, items sharing the same fields a
small table, "Label: value" lines bold labels, a short heading only where
the text has parts; no emoji, no title. The model's text is checked against
the card's: at least 90 % of its distinct words still there and at most 15 %
new, or the card is left as it was - layout only, never content. The AI menu
shows it drawn as the card would draw it, with Apply and Cancel; Apply is one
op, so Ctrl+Z puts the old text back.

Checked with the real model: David Astill's four "Label: value" lines came
back as four bold labels and the two report files as a list, every word kept,
in 7.2 s; applied and undone.

299 tests.

## 79. Beautify only organises (2026-11-21)

"It just needs to organise the text, not change anything." Beautify (§78) had
the model rewrite the card as Markdown and then checked the words after the
fact - 90 % kept, 15 % new allowed - which still let rewording through.

Now the model writes no text at all. The card is cut into its own pieces
(`ask.beautify_pieces`): its lines, without any list marks or heading marks
they had; a line of items joined by semicolons - outside brackets - its
items, a "Label:" in front kept as a piece of its own; a paragraph over 220
characters, its sentences. The model is given the pieces numbered and replies
only with how to group them - `{"blocks": [{"kind": "heading" | "bullets" |
"numbered" | "text", "pieces": [...]}]}` - and the server builds the Markdown
from the card's own words. The plan must use every piece exactly once and in
order, with known kinds, or the card is left as it was. Pieces kept together
in a paragraph are joined as they were in the card ("; " stays "; ", a new
line a line break), so nothing runs into anything else; a "Label: value"
piece at the start of a line gets a bold label; a "heading" that is really a
long or "Label: value" line stays a line. Tables, which would mean moving
words, are no longer made.

Checked with the real model on four texts - David Astill's and Nigel's cards,
a five-sentence paragraph on analogy, and one sentence of steps joined by
semicolons: every one kept the same words in the same order; the cards came
back with bold labels and their semicolons; the paragraph was parted in two;
the steps became a list under "To set up the lab visit:" (0.9-6.8 s). Found
on the way and fixed: a first try ran a label's items into each other
("Nottingham City Transport Chair of ALBUM") and cut a bracket at its
semicolon; and the model once made Nigel's whole "Role: ..." line a heading.

299 tests.

## 80. Explain more gives what it is asked for, and never cites a card for it (2026-11-21)

Asked for "two examples" with Explain more on, the answer said the cards did
not have them. Checked with the real model on *SWIFT Lab Visitors*, after "What
is the Nottingham Digital Twin?", with the three cards that answer cited kept.
It did give examples in every run, but:

- **The request was read as a list to extract.** The task planner (§66) still
  ran in Explain more, and took "give me two examples" as *extract* in 4 runs
  of 4. That shape says "say plainly if some could not be found", and it was
  said *after* the Explain more instruction, so it won: every answer opened
  with a paragraph on what the cards lack. On "examples of how other cities
  use a digital twin" its steps searched the board three times (9 s), the
  board Explain more is meant not to search, and found nothing.
- **Made-up examples cited cards.** In 4 runs of 5 the examples after
  "Beyond your board:" carried a card citation, which passes the model's own
  knowledge off as the person's notes. One named a city project that likely
  does not exist ("Phoenix One").

Done:

- **Explain more is not planned.** No task, no steps: the request goes
  straight to the answer (`app.py`, both the plain and the pasted-picture
  path). The page already copes when the task events are absent.
- **The Explain more instruction is said last**, after the step notes and
  the dates, so nothing after it pulls the other way. It now says: give what
  is asked for (examples, an analogy, detail); cite a card only for what that
  card itself says; everything else goes after the line and cites nothing;
  before the line only what the cards say that answers the question - never
  claim a card says more; when they hold nothing, start with the line rather
  than say what they lack; do not make up names, places or figures.
- **Citations after the line are taken out** in `check_citations`, for every
  kind of answer: the system prompt already said "cite nothing there", and the
  model did not always keep to it. They are not counted as invented sources.
  A Beyond part that is only "None" (seen in a plain answer on the same
  board) is left out.

Three wordings were tried, each on the same six questions the board cannot
answer (two examples, twice; other cities; an analogy; a real-world use; how
the data gets in):

| wording | opens with what the cards lack | cites in Beyond (before removal) | board facts made up |
|---|---|---|---|
| "say so in one short sentence at most" | 6 of 6, two or three sentences | 0 | 0 |
| "never say what the cards lack" | 2 of 6 | 4 | 1 (an EMCCA use, cited) |
| kept: "only what the cards say ... start with the line" | 5 of 6, one or two sentences | 1 | 0 |

The second cut the opening line but pushed the model to fill the part before
the line by restating the cards, and once it invented what the EMCCA uses the
twin for and cited a card for it. That is worse than a line saying the cards
do not cover it, so the third was kept. A question the cards do answer ("Who
visited from the EMCCA?") still came back from the cards, cited. Answers
took 3-12 s with no planning (the planner alone took 1.4-1.9 s before).
Checked in the app: the Explain more turn shows no task chip, and the
examples sit under "Beyond your board".

Not done: the short opening about what the cards lack remains in most
answers. It comes from rule 3 of the system prompt ("if the cards do not
answer the question, say so plainly first"), which every board answer
shares. Changing it is a decision for the owner. The exact failure first
reported (a refusal with no examples at all) did not happen in 24 runs
today (5 before the change, 19 after).

303 tests.

## 81. Explain more drops "the cards do not cover it" (2026-11-21)

After §80 most Explain more answers still opened with a sentence or two on
what the cards lack ("The cards provided do not contain examples... They
only list visitors"). The owner chose to drop it: the page already marks
what is beyond the board.

- **Telling the model was not enough.** The Explain more instruction now
  says by number that rule 3 of the system prompt does not apply, and never
  to write that the cards do not contain, describe or cover something. On
  the six questions of §80, 4 answers of 6 still opened that way. The system
  prompt itself is left as it is: it is shared by every board answer, and
  its start is what llama-server keeps cached.
- **So it is done in code** (`ask.without_lack`), for Explain more answers
  only, before the Beyond line only, and only when there is a Beyond part to
  read instead. A sentence goes when it says the cards, notes, board or
  "they" do not contain / describe / list / mention ... something, only list
  / identify / mention ..., or have no specific / concrete ... examples or
  details; or when it opens "there is no". An opening "Based on the cards
  provided," is taken off a sentence that stays. Lines and list items are
  kept as they were. If nothing is left before the line, the answer starts
  with it, and the page shows only the *Beyond your board* part.

Measured on the same seven questions (the six of §80 and "Who visited from
the EMCCA?"): the model opened with what the cards lack in 2 of 6, both
dropped, so 0 of 6 answers show it; those two start with Beyond your board.
Every sentence that said what the cards hold stayed, in the other four and
in the EMCCA answer. Unit tests use the sentences the model actually wrote.
Checked in the app: asked for examples from other cities, the answer opens
with Beyond your board and nothing above it.

Not done: the line is still seen while the answer streams, and goes when it
ends (the page shows the checked answer then). Ordinary answers keep rule 3.
In Explain more the part before the line sometimes says a little more than
its card ("allows users to analyse real-time data", cited, where the card
names the domains only) - put on TODO.md.

309 tests.

## 82. Numbers in file answers: rows kept, a calculator, and Double check (2026-11-21)

Asked about a PDF, an answer now and then got a number wrong. The owner asked
for two things: any calculation done by a calculator tool, never the model;
and a separate step, asked for under an answer, that double checks its
numbers. Of three directions shown (a button under the answer, a switch for
the file, both), the owner chose the button.

**A test set first** (`scripts/number_eval.py`, committed: it names nothing of
the owner's). Three PDFs written by hand - a report with four tables, a lab
table of ten samples, and a ten-page council budget with a wide monthly table,
two-line headers, brackets for overspends, two council tax tables a year
apart, a table whose text comes out column by column, and a scanned table -
with numbers one swapped digit apart (3.48 beside 3.84). 43 questions with
answers worked out from the tables themselves: lookups, totals, differences,
averages, percentages, ratios. Run against a live Mindboard, it scores
whether the right number is in each answer; `--check` also presses Double
check.

**What was wrong**, two runs before any change: 39 and 40 of 43 right. Of the
seven wrong numbers, five came from the wrong row or column (S-02's
phosphorus answered with S-01's 34.8; Car parks in October with 895, not
282), two from sums done in the model's head (a "total" of 48,672 for
18,382; an average pH of 6.54 for 6.64). One answer cited p. 12 of a
four-page file.

1. **A PDF's lines are kept.** pdfium gives a page's text line by line - a
   table's row is a line - and the reader joined it all into one line,
   "Revenue 2.64 2.91 Cost of sales 1.12 ...". Now each line stays.
   39 and 42 of 43; wrong cells 5 to 3.
2. **The calculator** (`calc.py`), for every file but a spreadsheet (which
   has its sheet tools, §65), when the question reads as needing arithmetic.
   The model only chooses: an operation (sum, difference, percent change,
   percent of, ratio, average, product, min, max) and the numbers, each
   copied with its passage's label. The code checks every number is written
   in the passages, as a whole number (not the 1 of "Q1"), works the result
   out exactly with decimals, and hands it to the answer as CALCULATED. A
   number not found refuses that calculation, said as NOT CALCULATED. The
   file prompt now says to do no arithmetic: a figure not calculated is said
   to be not worked out.
   First tried, almost every calculation was refused: the model writes
   "Table 1" where the passage's label goes. A number now counts on the page
   cited, or else on the one page that has it (on two pages, none cited: it
   cannot be told which, and is refused). Then 31 of 32 calculations were
   right and one refused - but the answer twice did its own sum beside the
   calculator's, because the results sat before the passages. Said last,
   with the question (rule 6), the 15 arithmetic questions went 30 of 30 in
   two runs, where the same questions had 4 wrong in 60 before.
3. **Double check** (`doublecheck.py`, `POST /api/files/{fid}/check`), a
   button under an answer about a file that has a number in it. For each
   number - not a year, a page, a list's number, a label's ("Table 4.1",
   "Band D", "Q2", the "000" of "GBP 000"), nor one the question names -
   it finds the pages its sentence cites, else the answer's, else a search's
   (a page the file does not have is no page). Each page is read again: its
   text with rows kept and, for a PDF, a sharper picture (1,600 pixels a
   side). The model is shown each sentence with its number blanked out -
   "spent on Registrars in June is ___" - and copies the number the page
   gives, with its row and column. The code judges: where the table lines
   up (a header line whose cells match the row line's numbers), it reads the
   named cell itself; otherwise the copy must be on the page, on the line of
   the row named. It then matches the answer (checked) or replaces it
   (corrected). A number written on no page is worked out again by the
   calculator from the pages' own numbers. Everything else is marked not
   checked, with why. The answer comes back with each number marked, drawn
   by Prose from a text token (never HTML): green checked, amber corrected
   with the old number struck through, grey not checked; a box under it
   lists each number and where it was found.

How Double check got there, each on the full set:

| version | wrong before | wrong after | right numbers made wrong | check time (median / max) |
|---|---|---|---|---|
| shown the answer's number | 3 | 1 | 0, but the years 2022-2025 of a list "corrected" into values, "Table 2" into 6,840 | 7.8 s / 29.6 s |
| labels skipped, row checked, blind | 3 | 1 | 1 (Waste, Nov: 148 to 465, another month) | 7.2 s / 17.5 s |
| the named cell read by the code | 1 | 0 | 0 | 7.1 s / 13.7 s |

Shown the answer's number, the look found that number and agreed - told
the answer's 763 (December) for June, it copied 763 and wrote "Dec". Blind,
it found the right cells for three wrong answers out of three, but once
misread the month; reading the cell in code fixed that. In the last run 106
numbers were checked, one corrected (Registrars, June: 602 to 389) and 11
left not checked: the column-by-column staff table (no row line to hold a
number to), the scanned page (its text is a figure description, which the
check does not read), and a figure rescaled by the answer (0.36m as
360,000). Checked in the app on the soil results: asked for two values and
a difference, Double check said "Reading p. 2 again", then the calculator,
and marked all three checked, by row and column and by the calculator.

Not done: a scanned page's numbers are not checked (the figure description
could be held to like a page's text); tables whose cells do not line up
with their header by spaces ("Nitrogen (mg/kg)" is two words) fall back to
the row check; a corrected number's sums are not redone unless they are on
no page themselves; the board's answers have no calculator yet; the test
set is made by hand and is no real report - worth running on a real PDF the
owner has seen go wrong. While the answer streams, a number the model
labelled "(calculated)" may be its own sum; Double check is what catches it.

346 tests.

## 83. The card AI panel moves (2026-11-21)

From the owner, with a screenshot of an Explanation panel over its card:
"when we have this panel I want to be able to move that freely in canvas".
The panel lived inside the selected card's toolbar, so it could not move, and
it closed whenever the card was let go. Of three directions shown - floating
over the board, placed on the board (zooming with it), floating with a line
back to its card - the owner chose the first.

- **A layer of its own.** Summarise, Explain, Fun fact and Quiz panels, for
  one card or several, are now drawn in one layer over the page (`AiPanels`,
  mounted by the canvas), not inside the toolbar, so what a panel holds - an
  answer still streaming, a quiz half played - is kept when it leaves the
  card. The card's menu leaves an empty mark in its bar (`PanelSlot`); while
  the panel has not been moved, the layer places it against that mark every
  frame - above the bar, or below when there is no room, as menus do - so it
  follows the card as the board pans and zooms, and goes when the card is let
  go or the menu closed, as before. The back arrow still returns to the menu.
- **Dragged by its header** ("Drag to move"), it detaches: the card's menu
  closes, the back arrow becomes a close button, and the panel stays where it
  was put on the screen - at one size whatever the zoom - while other cards
  are selected, the board is panned, or the chat is used. It closes with its
  x, or Esc while it has focus. A press shorter than 4 pixels is a click, not
  a drag. Several can be open; the one last touched is in front. Another
  board closes them all.
- **Kept whole in the window.** First tried, a panel let go near the bottom
  edge grew off it as its answer arrived, taking Copy and Add to board out of
  reach. Now a moved panel is held inside the window every frame - as it
  grows, and as the window changes size - and a drag stops at the edges.
- **Add to board** from a moved panel still makes a card linked to the first
  card; if that card has been deleted meanwhile, the button is greyed out
  ("Its card is no longer on the board") rather than doing nothing.
- Beautify keeps its panel by the card: it changes that card, and is applied
  or cancelled there.

Checked in the app on the "Group test" board at 47% zoom: Explain opened by
the card; dragged while its answer streamed, it kept streaming and moved;
the card's menu closed; deselecting the card and panning the board left it
in place and full size; let go past the bottom-right corner it stopped
whole inside the window; Add to board made "Explanation: Chunking" (undone
after); Esc closed it. Unmoved, the back arrow returned to the menu and
deselecting the card closed the panel. With two cards selected, Summarise
these opened by the selection bar, moved, and stayed after the selection
was cleared. Interface type-checked and built; no server change.

Not done: Esc closes a moved panel only while it has focus (click it first)
- a board-wide Esc would also clear the selection; panels are not resizable;
where a panel was put is not remembered after it closes.

346 tests.

## 84. Card titles in plain words (2026-11-21)

In the owner's screenshot (§83) a card was titled "**Definition of Analogy:**",
and every citation chip naming it showed the asterisks. The likely way in:
Beautify (§78) writes a card's text with bold labels - "**Definition of
Analogy:** The theory..." - and Break it into cards then made one card a
line, its title from the model or, in the fallback, from the line's first six
words; neither took markdown off a title.

- **Titles the model writes are plain** (`ask.plain_title`): Break, Merge
  and Add to board from the chat (`draft_card`, used by experts too) take off
  a leading heading or list marker, bold, italics and code marks, and - only
  when such marks came off - a trailing colon, which belonged to the label.
  "Note: read later" keeps its colon; "5 * 3 = 15" is left alone. Fix the
  writing keeps the person's own title as it is.
- **Break's fallback reads a label.** A line that opens with a bold label
  becomes a card titled by the label, with what follows as its text:
  "**Base and target:** The two situations." gives "Base and target" and
  "The two situations.".
- **Titles already on a board are drawn plain** (`plainTitle` in text.ts,
  the same rules): on the card, in the far-out view, in both kinds of
  citation chip, in the related-cards list, and in the names an AI panel
  gives a card it adds. Only the drawing changes: the title is kept, and
  editing it shows it as written, so the asterisks can be deleted.

Checked in the app: a card titled "**Definition of Analogy:**" showed
"Definition of Analogy" on its face and in the two chips of its Explain
panel. With the real model, Break on a card in Beautify's form gave
"Definition of Analogy", "How Analogy Works", "Solar System Example" in three
runs of three (9.1 s, then 1.5 s each); what the model wrote before
cleaning is not shown by the route, so whether it wrote asterisks this time
is not known - the unit tests hold the cleaning to the cases seen.

Not done: titles typed with asterisks on purpose are drawn without them
too; the search index and what the model reads keep the title as written.

356 tests.

## 85. Make a table: an answer's own words as rows and columns (2026-11-21)

Answers were prose or lists, and the system prompt says "no tables". The
owner asked for a button under an answer that rewrites it as a table, in its
own model step. Of three directions shown (the table in place of the answer,
in a box under it, as a new turn), the owner chose in place.

- **The button** sits under a finished answer beside Add to board: *Make a
  table*. It shows when the part to lay out is 60 characters or more and is
  not a table already. The part is what the board says; when there is
  nothing before the Beyond line, what is beyond it. The other part stays
  as it was, under its own heading.
- **The step** (`ask.answer_table`, `POST /api/boards/{bid}/table`) takes the
  question slot like Beautify, reads only the answer it is sent - no search,
  no cards - and writes nothing. The model replies with columns and rows as
  JSON; 90 s, then it is hung up.
- **The code checks every cell against the answer.** Every number in a cell
  must be written in the answer, and 60% of its words (three letters or more,
  a plural's s taken off), so a cell may shorten "Nottingham City Council"
  but not bring in a name, a date or a total of its own; a cell that fails is
  left empty ("—"). Only the answer's own citations are kept, card and page.
  A source of the answer no cell carries is listed under the table ("Also
  from: ..."), so no citation is lost. A column left with nothing in it goes,
  and a column of citations alone goes into the row's first cell. Fewer than
  two rows, or rows whose first cells mostly repeat, is no table: the page
  says "This answer doesn't have rows to compare." The markdown is written
  by the code, from the checked cells.
- **In place**: the table replaces the part it lays out; *Show as written*
  brings the answer back, *Show as a table* the table. A note says it was
  laid out from the answer as written, and how many cells were left out. The
  table is kept with the conversation and survives a reload. Double check
  (§82) clears it, since its numbers may have changed.
- **Add to board takes the table**: with the table shown, the card's text is
  the table (citations taken off - a card does not draw them) and its title
  is the question. The drafter still picks the card it belongs with.

With the real model on *SWIFT Lab Visitors*: visitors from the EMCCA and the
university with their roles gave a 7-row table (Visitor, Organization,
Role - the model's own spelling) in 6 s; every visit with its date, 7 rows, with 7 cells
it could not hold to the answer left out (a column of purposes, dropped
whole); organisations and dates, 7 rows in 5 s. Asked "What is the
Nottingham Digital Twin?", the first version tabled what the cards lack -
"Cards | Do not contain the definition" four times. The prompt now says each
row is a different thing and never what is missing, and the code refuses
rows that repeat; after that, the same question twice and "Who is Michael
Gallagher?" all said no rows to compare, in 2-3 s. The first real table also
put its citations in a column of their own ("Citation"); they now go beside
what they name. Add to board first titled the table's card from the
conversation ("Real-time urban simulation capabilities"), since the drafter
names what is new; it now takes the question.

Not done: the model may still choose no table for an answer that has rows,
and say so; the board's answers are not offered tables on their own (the
system prompt's "no tables" stays); a cell's words are checked as words,
not as facts - "Nottingham" from one sentence beside "Derby" from another
would pass if both are in the answer; answers in a file conversation get the
button too but were not checked with the real model.

361 tests.

## 86. Explain more: the model's own knowledge and the conversation, nothing from the board (2026-11-21)

With Explain more on, the cards from earlier in the conversation were still
sent (§34, §80). Asked "compare graph knowledge with ontology for me", the
answer opened with a sentence restating cards ("They focus specifically on
reasoning, the Systematicity Principle..."), cited twice, and put the
comparison asked for under *Beyond your board*. The owner wants Explain more
to show nothing from the board: the model's own knowledge and the
conversation, shown the ordinary way.

- **Nothing from the board is sent.** `ask.build` returns an empty context
  for Explain more - no search, no cards, not even cards chosen by hand or
  kept from earlier answers - and the request is not planned (as in §80).
- **Its own system prompt**, `EXPLAIN_SYSTEM`, beside Board off's
  (`OWN_SYSTEM`, §63): answer from your own knowledge, building on the
  conversation; do not mention cards, notes or a board and cite nothing;
  explain step by step, give what is asked for (examples, an analogy, more
  detail), say when unsure, make up no names or figures. It takes the place
  of the long instruction said with the question (§80): with no cards there
  is no Beyond line to keep things apart.
- **The conversation goes in without card citations**: earlier answers'
  `[[card]]` marks are taken out (indentation kept), so they cannot pull the
  board back in. Citations the model writes anyway are taken out of the
  answer too, quietly - they are not counted as invented sources.
- **A pasted picture** goes in with a plain head ("Answer about it"), not
  the board's "bring in the cards", in Board off as well.
- **On the page** an Explain more answer is drawn like a Board off answer:
  an ordinary answer with a small heading, "Explain more - the model's own
  knowledge, not your board"; no chips, no Beyond part. The top of the chat
  says "Explain more: answers come from the model's own knowledge and this
  conversation - nothing from the board is sent."
- **Removed**: the Explain more instruction of §80 and `without_lack` (§81),
  which dropped sentences on what the cards lack from the part before the
  Beyond line. That part no longer exists.

With the real model on *SWIFT Lab Visitors*, after "Who visited from the
EMCCA?": "compare graph knowledge with ontology for me" with Explain more
on gave a 2,800-character comparison in 11 s, no citation chips and no
Beyond part. "Why might the EMCCA people you named care about a digital
twin? Give me two examples." answered from the conversation - Michael
Gallagher and his role, from the answer before - with two examples (flood
defences, and one more), no citations, in 8 s. Both ran beside the owner's
own Mindboard, which held the graphics card, so the page warned of slower
answers.

Not done: an Explain more answer cannot be checked against the board, since
it is not from it; Add to board still drafts a card from it and links it to
a card the conversation found (§53), as before.

355 tests.

## 87. Summarise beside Add to board, reading while it writes, no hover text in the chat (2026-11-21)

The owner asked for three things in the chat: a *Summarise* button beside
*Add to board*; to be able to scroll up and read while an answer is still
being written, not wait for it to finish; and no hover text over the chat's
buttons and chips.

- **Summarise** sits under a finished answer between *Add to board* and
  *Make a table*, shown when the answer is 400 characters or more (the NEXT
  line not counted). It works like Make a table (§85): its own step
  (`ask.answer_summary`, `POST /api/boards/{bid}/summary`) takes the
  question slot, reads only the answer it is sent and writes nothing; 60 s
  for a reply, then it is hung up. The two parts - what the board says and
  what is beyond it - are summarised apart, one call each, so the summary
  keeps them told apart; a part under 400 characters is kept as it is. The
  model is asked for 2 to 5 short sentences, or up to 5 points for a list,
  in the answer's own words, citations kept.
- **The code checks every sentence against the answer**, with the table's
  test (§85): every number written in the answer, and 60% of its words. A
  sentence that fails is left out, and the note under the summary says how
  many. Only the answer's own citations are kept; a citation alone after a
  full stop stays with its sentence. Headings, bold and a lone "Summary:"
  are taken off. Nothing kept is "Couldn't summarise this time - try
  again."; an answer whose parts are all short is "already short".
- **In place**, like the table: *Show as written* brings the answer back,
  *Show the summary* the summary. Only one of the two stands in the
  answer's place at a time - showing the summary hides the table and the
  other way round. Add to board, with the summary shown, drafts the card
  from the summary. Double check (§82) clears it, as it clears a table.
- **Reading while it writes.** §68 let go of the answer on a wheel move
  upward, but the scroll handler took it back whenever the view was within
  80 px of the bottom, so a small scroll up was undone by the next words,
  and the scrollbar and touchpad never let go at all. Now any move upward
  in the scroll position lets go (a shrinking answer, which moves the view
  up by itself, does not count), and only coming back within 24 px of the
  bottom follows again. The wheel no longer re-follows from 160 px away.
- **No hover text in the chat.** The `title` tooltips are gone from the
  chat's header buttons, context chips, bar chips, send and stop, the
  buttons under an answer, the suggested questions, the file tabs, page
  links and thumbnails, and the files and pictures under an answer. Buttons
  with only an icon keep their name as `aria-label`, which shows nothing on
  hover. Left as they were: a checked number's mark (§82) and a formula's
  TeX in an answer, which say something the page does not.

Not checked yet: the owner is checking it in the app; Summarise has not
been tried with the real model, and the scrolling was not tried in the
browser.

357 tests.

## 88. A board answer that cites no card says so (2026-11-21)

Asked "what is rag fusion?" on a 141-card board, the answer was read as an
explanation (§66) and searched the board (73 cards). Its four steps were the
words of the board's *Multi-Query & RAG-Fusion* picture card (§39), but it
cited nothing, and its example, which no card gives, had no Beyond line. It
read as the model's own knowledge and was not marked as either.

- **An uncited answer is said to be so.** When an answer from the board (a
  search, or cards chosen by hand), with cards in context, cites none of
  them, the done event carries `uncited`, and the page says under the
  answer: "No card is cited in this answer, so nothing shows it comes from
  your board - it may be the model's own knowledge." Not for Board off,
  Explain more or a file. It reads the finished answer only and changes
  nothing the model is asked.

Tried and dropped: a sentence after the task's shape (`tasks.task_note`),
for board answers - cite each card after what you use from it, a picture's
card too, and put an example of your own after the Beyond line. With the
real model on a copy of the RAG board, six shaped questions (explain,
compare, summarize), each once without and once with it, the same search
in both: cards cited 1/1/4/3/6/2 without, 4/1/5/3/13/1 with. Without it,
"what is rag fusion?" cited only the RAG-Fusion card and gave its steps
right; with it, the answer called RAG Fusion hybrid search (vector plus
BM25) and cited three Hybrid Search cards beside the right one - told to
cite each card, it drew in cards that were only near the question. Its
made-up examples still sat in the board part, cited, in both; what went
after the Beyond line was an added analogy. All six answers without the
sentence cited cards, so the uncited answer seen first was not the rule.
The sentence was taken out.

Not done: a made-up example in an explanation is cited as if a card gave
it, with or without the sentence (seen in five of six answers); a fix needs
its own real-model comparison. The warning also shows on an answer that
rightly says the cards do not cover a question, since that cites nothing.

358 tests.

## 89. Explain more: understand, plan, answer - in a file's conversation too; page lists as links (2026-11-21)

Two things the owner found in a conversation with *Context Engineering.pdf*.

**Page lists were plain text.** An answer cited "(p. 13)" as a link but
"(p. 4, p. 13)" as plain words, as if it cited nothing: the page pattern in
`Prose.tsx` took one page or a range only. It now takes a list of up to
twelve, "(p. 4, p. 13)", "(pp. 4, 12)" or "(p. 4; p. 12-13)", and draws each
page as a link of its own inside the one pair of brackets.

**Explain more was dropped in a file's conversation.** The page sent
`explain` only for the board's conversation (`!branch`), while the chip
still showed on; asked "tell me example for this workflow", the file was
searched as usual (12 passages of 668), the answer said the document has no
such example, and gave one under *Beyond this file*. The owner also asked
for Explain more to be a chain of its own: read the conversation, plan how
to meet what is wanted, then answer.

- **In a file's conversation** Explain more now sends nothing from the file
  (`POST /api/files/{fid}/ask` with `explain`): no passages, no pictures of
  pages, no calculator, the file's tasks not run - the conversation and the
  model's own knowledge, with `EXPLAIN_SYSTEM`, as on the board (§86). Page
  citations are taken out of the conversation and the answer, as card
  citations are. The answer's heading says "not this file".
- **Understand and plan** (`tasks.run_explain`), for the board's
  conversation and a file's: one JSON call (30 s, then hung up; no tools,
  so it cannot search) reads the last four turns, up to 1,500 characters
  each with citations out, and the newest message, and replies with what is
  wanted ("You want ...") and 2 to 4 points for the answer. Both are checked
  (`clean_explain_plan`); anything unusable answers as it stands, as before.
  The reading goes to the page as a `task` event ("explain", no steps), so
  the chip and "You want ..." line show as for other tasks; the plan is said
  after the question, last, so the cached start of the prompt stays put.
- **Answer** with `EXPLAIN_SYSTEM`, following the plan.

With the real model, against copies of the PDF and the RAG board, each case
twice without the plan and twice with it: in all 12 answers nothing from the
file or board was sent. The readings were right each time - "You want a
concrete example of the Context Engineering workflow to see how the three
phases work in practice"; "You want a concrete example of how RAG-Fusion
works in practice". For the file's workflow both gave a scenario through the
three phases (a researcher's crop data without the plan; a support agent
with a customer's ticket history, with it), in 11-13 s either way. For "give
me an example of this" after RAG-Fusion, without the plan the example was a
recipe search in 1,500-1,800 characters, 6-7 s, and described RRF as
favouring documents near the top of *any* list; with it, a running-shoe
query walked through each step with ranks in each list and RRF rewarding
documents high in several, 2,700-2,900 characters, 12 s. "Explain it like
I'm new to RAG" read the need right both times with the plan (1,700-3,200
characters, 8-12 s). The plan costs one short call: 0 to 6 s more.

Not done: the bracketed reference numbers a paper's text carries
("[1147, 405, 340]") are copied into file answers as if they were sources;
an Explain more answer is longer with the plan in most cases, which the
owner may or may not want.

361 tests.

## 90. A table with formulas keeps its columns (2026-11-21)

Asked for the differences between context engineering and prompt
engineering, a file answer compared them in a table whose cells held
formulas with a conditional bar - $P_\theta(Y | \text{prompt})$. The page
split each row on every "|", so those rows fell apart into extra columns of
raw LaTeX ("$\arg\max_{\text{prompt}} P_\theta(Y" in one cell, the rest in
the next). And the chat breaks words anywhere, so a narrow first column read
"Dimens / ion", "Definiti / on".

- **Cells** (`Prose.splitCells`): formulas (the same pattern the page draws
  maths with, §61), `code`, and "\|" are set aside before a row is split
  on "|", then put back. Checked on the row from the owner's answer: three
  cells, each formula whole; "$5 | $10" still splits in two, since a price
  is not a formula; "a \| b" stays one cell and reads "a | b".
- **Whole words in cells**: table cells in the chat wrap at spaces
  (`overflow-wrap: break-word`, at least 7 characters wide); a word longer
  than the column may still break, and a wide table scrolls, as before.

Not done: a formula the model writes without dollar signs is not known to
be one, so a bar in it still splits the row.

361 tests.

## 91. Add to board keeps a table the answer wrote (2026-11-21)

Asked for the differences between context engineering and prompt
engineering, the answer was a comparison table the model wrote itself (the
compare shape, §66), every cell cited, with a line on which fits when. Add
to board drafts "what this answer adds" (§34, §53) - made for Explain more
answers - and turned it into a short paragraph titled "Context Engineering
as System Optimization Science": the table was gone, and one clause ("ensuring
performance remains stable even as data scales") was not in the answer.
Make a table already put its table on the card as it is (§85); a table in
the answer as written did not.

- **The card holds the answer as written** when its part before the Beyond
  line has a markdown table (a row of cells and a separator row): the words
  around the table too, card citations taken off, as for §85. The title is
  the question; the drafter still chooses the card it belongs with and how
  they relate.
- **Which wins**: a Make a table table shown in the answer's place, then a
  summary shown (§87), then a table in the answer as written, then the
  drafted card as before. Answers without a table are drafted as before.

Not done: the drafter can still add a clause the answer does not say, in
the cards it drafts for answers without a table; nothing checks a drafted
card against the answer.

361 tests.

## 92. A picture of the board holds every card, however it is zoomed (2026-11-21)

Exporting a picture (§7c) while zoomed in gave an image sized for the whole
level that held only the cards on screen: a wide empty field, a few cards at
one side, and links running off to nothing. The canvas puts only the cards in
view on the page (`onlyRenderVisibleElements`, kept for speed on big levels),
and the picture is a copy of the page, so cards out of view were never in it.
At the zoom a board opens at, every card is in view, which is why it looked
right most of the time.

The export now turns that setting off for its own length: `runExport` in
`Canvas.tsx` sets `drawAll`, `waitForAllCards` (`exportImage.ts`) waits,
frame by frame, until every card of the level is on the page and measured by
xyflow (eight seconds at most, then it says so), gives the links one more
frame, and only then reads the level's bounds and renders. The setting comes
back on afterwards, whether the export worked or not. Measuring after the
cards are drawn also fixes the bounds: a card never drawn had no measured
size.

Checked in the browser on "SWIFT Lab Visitors" (41 cards): zoomed in with 5
cards on the page, the picture before the change held those 5 and their
links; after it, all 41, the same as an export at the opening zoom. Panned to
an empty corner with no card on the page, the picture still held all 41, and
the canvas went back to drawing only what is in view.

Not done: on a level with thousands of flat cards (no groups), drawing them
all for the export may take seconds; not measured.

361 tests.

## 93. @ in a card's text names another card and links the two (2026-11-21)

Wanted: while writing a card, a way to point at another card from the text,
the way a note-taking app does, with the board showing the same thing as a
link. Three directions were drawn (`docs/mockups/card-links.html`): the link
always added, offered after each choice, or a remembered switch in the list.
The owner chose **always added**.

What was done:

- **The mark is the one answers already cite with**, `[[card-id]]`. Card text
  already showed it as a chip with the other card's title that goes to the
  card when clicked (`CardRef`), so nothing new was needed to show it, and
  renaming the other card renames the chip.
- **Writing.** Typing `@` - at the start of the text or after a space or
  bracket, so `me@example.org` does not open it - opens a list of up to eight
  cards under the card (a `NodeToolbar`, so the card's `overflow: hidden`
  does not cut it off). Titles holding the words typed; those starting with
  them first, then this level's cards before the rest; a card in another
  level shows its group's name. ↑ ↓ and Enter or Tab choose, a click
  chooses (pressing the list keeps the caret in the card), Esc closes the
  list without leaving the card, and a space with nothing matching closes
  it. While the card is written a reference reads `@[Title]` (`toWriting`,
  `text.ts`); on saving, each `@[label]` the editor gave out becomes
  `[[id]]` again (`fromWriting`). One typed or changed by hand stays as
  written. Two cards of one title get `Title (2)`.
- **The link.** On saving, each card the text names that it did not name
  before, and that is not already linked to this card either way, gets a
  link from this card - in the same `store.run` as the text, so one Ctrl+Z
  takes back both. Deleting a reference later leaves the link; links are
  removed on the board, as before. A card in another level is linked as any
  link across levels is, drawn to its group.
- **What the model and search read.** `named_cards` (`search_index.py`)
  turns each `[[id]]` in a card's text into the other card's title in
  quotes, in the context (`ask.card_text`) and in the search index
  (`card_fields`). Left as an id, the model could take card B's id inside
  card A's text for a citation of A's words; as a title, search also finds
  the words. A card no longer on the board leaves nothing.

Checked in the browser on a five-card board: `@ba` listed Basics, Base and
target, and Baseline results (with "Experiments" beside it); choosing Base
and target put `@[Base and target]` in the text, stored
`[[01M800QR...]]`, showed the chip and drew the link; a second reference to
the same card added no second link; a card inside Experiments was linked;
choosing with a click kept the card in editing. Ctrl+Z took the reference
and the link back together.

Not done: breaking a card into cards takes card marks out, so its
references are dropped; the board's Find index (`ops.py`) still holds the
raw id; not tried with the real model - the prompt did not change, only what
a card's text says.

362 tests.
