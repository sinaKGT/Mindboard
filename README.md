# Mindboard

Map your mind to a board. A local-first, multimodal graph canvas — text, images
and video as nodes, with named relationships between them, meta-nodes for
hierarchy, and a single file that carries the whole thing.

Everything stays on your machine. No account, no cloud, no telemetry.

## Getting started

**Double-click `Mindboard.bat`** (Windows) or **`Mindboard.command`** (macOS /
Linux).

The first run sets everything up — an isolated environment inside this folder,
nothing installed system-wide. After that you get a menu:

```
  ● Mindboard 0.1.0
    3 boards · C:\Users\you\AppData\Roaming\Mindboard

    1  Open Mindboard          run it and open your browser
    2  Back up all boards      everything, media included, into one zip
    3  Restore from a backup   put your boards back
    4  My boards
    5  Check the install
    6  Open the data folder
    7  Reinstall / update
    q  Quit

  Choose (1):
```

Press `1` and Enter. Your browser opens on the board list; press Enter again in
that window whenever you want to stop the app. Nothing needs saving by hand.

### If you prefer a terminal

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1   # Windows
```
```bash
./install.sh                                             # macOS / Linux
```

then `.venv\Scripts\mindboard run` (or `.venv/bin/mindboard run`). Every menu
option has a command behind it — see **Commands** below.

## Use

| | |
|---|---|
| New text node | press **a** — it lands where your pointer is — or double-click empty canvas |
| New media node | drag a file in, or paste an image or URL |
| Link or video from the web | paste any link — its cover is saved locally; click the cover to open it in your browser |
| Connect | three ways: select a node and press **c**; the **link** button in its toolbar; or click a **⊕** port. Then click the other node |
| Connect **and** create | while connecting, click empty canvas instead |
| Name a relationship | double-click the edge |
| Remove a relationship | click it, then the **✕** on its label — or `Delete` |
| Move a node with what it is connected to | just drag it — connected nodes follow, less the further away they are |
| Move one node on its own | hold `Alt` while dragging |
| Copy / cut / paste nodes | `Ctrl+C` / `Ctrl+X` / `Ctrl+V` — relationships and group contents come too, across boards as well |
| Duplicate a node | `Ctrl+D`, or the ⧉ button in its toolbar |
| Write on any node | double-click it — including on a picture |
| Change text size | **A / A** in the toolbar, or click the number for a menu |
| Bullet or numbered list | the list buttons in the toolbar — Enter continues the list as you type |
| Recolour a node | pick a colour, then one of the three tones: bar, tinted, or solid |
| The node toolbar | click a node to open it. Hovering shows only the ⊕ ports, so a toolbar never sits over the card you were aiming at |
| Export a picture of the board | `Ctrl+E`, or the image button in the corner |
| Group nodes | select two or more — a bar appears above them with **Group** — or `Ctrl+G` |
| Put a node into an existing group | drag it onto the group; the group lights up as you cross it |
| Take a node back out | open the group, then the **↑** button in the node's toolbar |
| Ungroup | `Ctrl+Shift+G` |
| Enter / leave a meta-node | double-click / `Esc` |
| Tidy the current level | `Ctrl+L` |
| Undo / redo | `Ctrl+Z` / `Ctrl+Shift+Z` |
| Everything else | `Ctrl+K` |

## Where your things are kept

Everything you put on a board — a video you upload, a picture you drag in, the
cover of a link you paste — is **copied into Mindboard's own folder**. Nothing
points back at the file you dragged from, so you can move or delete the original
and the board is unaffected, and a backup carries the whole of it.

```
mindboard where          # prints the folder
```

On Windows it is `%APPDATA%\Mindboard`, on macOS
`~/Library/Application Support/Mindboard`, and each board is its own folder
inside it: `board.db` plus an `assets` folder of the files it shows.

## Backing up

Menu options **2** and **3**, or from a terminal:

```
mindboard backup                     # every board, media included, into one zip
mindboard restore backup.zip         # put them all back
```

Backups made from the menu land in a `backups` folder next to this file.

A backup is an ordinary zip you can open and inspect: a `manifest.json` plus one
`.mindboard` bundle per board. Restoring keeps each board's identity, so
restoring the same backup twice does **not** duplicate your library — boards
already present are left alone. Use `--replace` to overwrite them from the
backup, or `--copy` to bring them in alongside what you have.

The app takes backups too: **Back up all** on the launcher, or `Ctrl+K` →
*Back up every board*. Dropping a backup zip into **Import** restores all of it.

## Commands

```
mindboard menu                       # the screen the launcher opens
mindboard run [--port 7788] [--host 127.0.0.1] [--data-dir PATH] [--no-open]
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
