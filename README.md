# Mindboard

Map your mind to a board. A local-first, multimodal graph canvas — text, images
and video as nodes, with named relationships between them, meta-nodes for
hierarchy, and a single portable file that carries the whole thing.

Everything stays on your machine. No account, no cloud, no telemetry.

## Install

```bash
uv tool install mindboard      # or: pipx install mindboard
mindboard start
```

That's the whole setup. The wheel ships the interface prebuilt, so you never
need Node to run it.

## Use

| | |
|---|---|
| New text node | double-click empty canvas |
| New media node | drag a file in, or paste an image or URL |
| Connect | drag from a node's edge onto another node |
| Connect **and** create | drag from a node and drop on empty canvas |
| Name a relationship | click the edge |
| Group into a meta-node | select nodes, `Ctrl+G` |
| Enter / leave a meta-node | double-click / `Esc` |
| Everything else | `Ctrl+K` |

## Commands

```
mindboard start [--port 7788] [--data-dir PATH] [--no-open]
mindboard new "Title" [-d "description"]
mindboard boards
mindboard export <board-id> -o board.mindboard
mindboard import board.mindboard
mindboard where | doctor | gc | version
```

## Developing

```bash
uv venv && uv pip install -e ".[dev]"
uv run mindboard start --dev          # backend on :7788
cd web && npm install && npm run dev  # interface on :5173, proxying /api
```

`npm run build` writes into `src/mindboard/web/`, which the wheel picks up.

Design and architecture: [DESIGN.md](DESIGN.md).

MIT.
