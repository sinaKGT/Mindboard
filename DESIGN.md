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
