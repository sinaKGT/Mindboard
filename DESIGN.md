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
{ "url": "https://…", "title": "…", "favicon": "…", "excerpt": "…" }
// file
{ "sha256": "…", "filename": "notes.pdf" }
// meta
{ "summary": "Everything about sensor placement", "child_count": 12 }
```

Keeping the payload in one JSON column means a new node type in v2 is a
frontend component plus a schema entry — **no migration**.

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
| New text node | Double-click empty canvas → node appears in edit mode, cursor blinking |
| New media node | Drag a file onto the canvas, or paste an image / URL |
| Connect | Hover a node and four **⊕ ports** appear. *Click* one, a line follows the cursor, click any node to finish. Dragging from the same port still works for anyone who prefers it |
| Connect + create | Drag from a port — or, while linking, click **empty canvas** → creates a node and connects it. This is the fast idea-flow path — the single most important interaction in the app |
| Label a relationship | Click the edge → inline input on the line |
| Group into meta-node | Select → `Ctrl+G` |
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
your size.

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

**3. Media is always stored locally, and always plays on the canvas.** A video
arrives either as a dropped/selected file or as a pasted URL; either way the
backend stores the bytes in the board's asset directory and the node plays it
inline. No streaming from the internet at view time, which keeps a board
watchable offline and keeps every export complete.

- Pasted URL pointing at a media file (`.mp4`, `.webm`, `.gif`, images) →
  fetched server-side, hashed, stored, becomes a video/image node.
- Pasted URL pointing at a page (YouTube, an article) → becomes a `link` node
  with title, favicon and excerpt. It cannot be stored as bytes without a
  platform downloader, which is out of scope; the node states plainly that it
  is a reference, not a local copy.
- A poster frame is captured client-side at 0.5s and stored as its own asset, so
  video cards look right without shipping ffmpeg.

**4. Text nodes are plain text, styled well.** No markdown, no rich-text editor.
What a node gets instead is typography: font size (a scale, not free numbers),
weight, alignment and colour, per node, from the hover controls. `content_json`
holds `{ "text": "…" }`; presentation lives in `style_json`. This keeps nodes
short — which is what a mind map wants — and removes an editor dependency and a
week of schedule.

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
