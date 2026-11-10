"""A board's summary, shown when the pointer rests on its tile (DESIGN.md 54).

One or two sentences on what a board holds, written by the local model from
its title, description, groups and most connected cards - once, then kept,
and written again only when the board has changed a lot. Derived like the
search indexes: kept in `<data>/search/summaries.json`, never in a board or a
backup, rebuilt if deleted.

Written only while the model is loaded and free - never loaded for this: the
tile asks, and so does the background indexer after a board's round. Until
then the tile says it has not been summarised yet.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from .paths import DataDir

SUMMARY = """You write the one-line summary shown on a mind map's tile, for someone
deciding whether to open it. In one or two plain sentences, at most 40 words,
say what it holds: its main subjects, in its own terms. Start with the
subjects themselves - never with "This board", "This mind map" or the like.
No preamble, no list, no quotation marks."""

CARD_TITLES = 30      # its most connected cards the model reads
CHANGED = 0.15        # a board this much bigger or smaller is summarised again
CHANGED_AT_LEAST = 5  # ...and by at least this many cards

_lock = threading.Lock()


def _path(dd: DataDir) -> Path:
    return dd.root / "search" / "summaries.json"


def load(dd: DataDir) -> dict[str, dict]:
    try:
        got = json.loads(_path(dd).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(dd: DataDir, data: dict) -> None:
    path = _path(dd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def fresh(kept: Optional[dict], title: str, count: int) -> bool:
    """Still true of the board: the same title, and not much bigger or smaller."""
    if not kept or not kept.get("text"):
        return False
    was = int(kept.get("count") or 0)
    moved = abs(count - was)
    return kept.get("title") == title and (moved < CHANGED_AT_LEAST or moved <= CHANGED * max(was, 1))


def brief(meta: dict, nodes: list[dict], edges: list[dict]) -> str:
    """What the model reads: the title, the description, the top-level groups
    and the cards with the most links."""
    degree: dict[str, int] = {}
    for e in edges:
        for k in (e["source"], e["target"]):
            degree[k] = degree.get(k, 0) + 1
    children: dict[str, int] = {}
    for n in nodes:
        if n.get("parent"):
            children[n["parent"]] = children.get(n["parent"], 0) + 1
    groups = [f"- {n.get('title') or 'Untitled'} ({children.get(n['id'], 0)} cards)"
              for n in nodes if n["type"] == "meta" and not n.get("parent")]
    named = [n for n in nodes if n["type"] != "meta" and (n.get("title") or "").strip()]
    named.sort(key=lambda n: -degree.get(n["id"], 0))
    cards = [f"- {n['title'].strip()[:80]}" for n in named[:CARD_TITLES]]
    return (f"TITLE\n{meta.get('title', '')}\n\nDESCRIPTION\n{meta.get('description') or '(none)'}\n\n"
            + ("GROUPS\n" +"\n".join(groups[:15]) + "\n\n" if groups else "")
            + f"MOST CONNECTED CARDS (of {len(nodes)})\n" + ("\n".join(cards) or "(none)"))


def write(model, dd: DataDir, bid: str, meta: dict, nodes: list[dict], edges: list[dict]) -> str:
    """Ask the model for the summary and keep it. The caller holds the model."""
    reply = model.chat([{"role": "system", "content": SUMMARY},
                        {"role": "user", "content": brief(meta, nodes, edges)}],
                       max_tokens=120, think=False, temperature=0.3)
    text = " ".join(str(reply.get("text") or "").split()).strip('"')[:320]
    if text:
        with _lock:
            data = load(dd)
            data[bid] = {"text": text, "title": meta.get("title", ""), "count": len(nodes), "at": time.time()}
            _save(dd, data)
    return text
