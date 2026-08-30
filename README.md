# Mindboard

Map your mind to a board. A local-first, multimodal graph canvas — text, images
and video as nodes, with named relationships between them, meta-nodes for
hierarchy, and a single file that carries the whole thing.

Everything stays on your machine. No account, no cloud, no telemetry.

## Install

**Windows**

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**macOS / Linux**

```bash
./install.sh
```

The installer builds an isolated environment inside this folder, installs
Mindboard into it, builds the interface, and prepares your storage. Nothing is
installed system-wide.

Then:

```
.venv\Scripts\mindboard run     # Windows
.venv/bin/mindboard run         # macOS / Linux
```

Already have the package on your path? `mindboard install` then `mindboard run`.

## Use

| | |
|---|---|
| New text node | double-click empty canvas |
| New media node | drag a file in, or paste an image or URL |
| Connect | drag from a node's edge onto another node |
| Connect **and** create | drag from a node and drop on empty canvas |
| Name a relationship | click the edge |
| Group nodes into a meta-node | select them, `Ctrl+G` |
| Ungroup | `Ctrl+Shift+G` |
| Enter / leave a meta-node | double-click / `Esc` |
| Tidy the current level | `Ctrl+L` |
| Undo / redo | `Ctrl+Z` / `Ctrl+Shift+Z` |
| Everything else | `Ctrl+K` |

## Backing up

```
mindboard backup                     # every board, media included, into one zip
mindboard restore backup.zip         # put them all back
```

A backup is an ordinary zip you can open and inspect: a `manifest.json` plus one
`.mindboard` bundle per board. Restoring keeps each board's identity, so
restoring the same backup twice does **not** duplicate your library — boards
already present are left alone. Use `--replace` to overwrite them from the
backup, or `--copy` to bring them in alongside what you have.

The app takes backups too: **Back up all** on the launcher, or `Ctrl+K` →
*Back up every board*. Dropping a backup zip into **Import** restores all of it.

## Commands

```
mindboard run [--port 7788] [--data-dir PATH] [--no-open]
mindboard install [--with-demo]
mindboard backup [-o FILE]
mindboard restore FILE [--replace | --copy]

mindboard boards
mindboard new "Title" [-d "description"]
mindboard export <board-id> -o board.mindboard
mindboard import board.mindboard
mindboard delete <board-id>

mindboard doctor        # check the install
mindboard gc            # drop media no node references
mindboard where         # print the data directory
```

## Developing

```bash
./install.sh
.venv/bin/mindboard run --dev         # backend on :7788
cd web && npm install && npm run dev  # interface on :5173, proxying /api
```

`./build.sh` writes the built interface into `src/mindboard/web/`, which the
wheel ships. `.venv/bin/python -m pytest tests` runs the suite.

Design and architecture: [DESIGN.md](DESIGN.md).

MIT.
