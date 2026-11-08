"""Experts: a name, a brief and the boards they draw on (DESIGN.md 40).

An expert is the same local model with a brief of its own and a library of
its own - the boards it is connected to. Its knowledge is those boards: every
answer is searched from them and cites their cards. Experts span boards, so
they belong to the library, not to a board: `<data>/experts.json`, written
whole and atomically, carried in backups. Asking an expert searches each of
its boards with the same search as a board (retrieve.py) and merges them by
reciprocal rank within one budget.

The guide answers "who can help with this?" from the overview: it searches
every expert's boards, lets the reranker judge how well each one's best cards
answer the question, and has the model say who can help and why.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import retrieve
from .ask import (SEARCH_PICTURE_TOKENS, Context, Picture, _picture, board_index,
                  card_text, estimate)
from .boards import conn_for, list_boards
from .ids import ulid
from .ops import read_graph
from .paths import DataDir
from .search_index import card_fields, card_passage

# The monument each expert stands as on the Experts page, all in sand - earned,
# not chosen freely: each needs a library of a certain size, from the simplest
# to the grandest, so growing your boards is what opens the next one.
# (shape, boards needed, cards each of those boards needs)
UNLOCKS = [("ankh", 0, 0), ("obelisk", 2, 20), ("pyramid", 3, 50), ("steps", 5, 100), ("pylon", 8, 150)]
SHAPES = [u[0] for u in UNLOCKS]
SHAPE_WORDS = {"ankh": "Ankh", "obelisk": "Obelisk", "pyramid": "Pyramid", "steps": "Step pyramid",
               "pylon": "Temple gate"}


def shapes(dd: DataDir) -> list[dict]:
    """Every monument, whether it is unlocked, and what it still needs."""
    sizes = sorted((b["node_count"] for b in list_boards(dd)), reverse=True)
    out = []
    for shape, boards, cards in UNLOCKS:
        have = sum(1 for n in sizes if n >= cards) if boards else 0
        out.append({"shape": shape, "unlocked": have >= boards,
                    "need": f"{boards} boards with {cards}+ cards each" if boards else "",
                    "have": min(have, boards), "of": boards})
    return out
COLOURS = ["#f0c070", "#7fd3c4", "#e9967a", "#d8c8a8", "#b4a0e8", "#8fc8f0", "#e6d36a", "#e88fb4"]
_lock = threading.Lock()


class ExpertError(ValueError):
    """Said to the person as it is."""


def _path(dd: DataDir) -> Path:
    return dd.root / "experts.json"


def load(dd: DataDir) -> list[dict]:
    try:
        return json.loads(_path(dd).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(dd: DataDir, experts: list[dict]) -> None:
    path = _path(dd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(experts, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _clean(dd: DataDir, data: dict, old: Optional[dict] = None) -> dict:
    name = str(data.get("name", (old or {}).get("name", ""))).strip()[:60]
    if not name:
        raise ExpertError("an expert needs a name")
    present = {b["id"] for b in list_boards(dd)}
    boards = [b for b in dict.fromkeys(data.get("boards", (old or {}).get("boards", []))) if b in present]
    if not boards:
        raise ExpertError("connect the expert to at least one board")
    return {"name": name, "brief": str(data.get("brief", (old or {}).get("brief", ""))).strip()[:600],
            "boards": boards, "colour": str(data.get("colour") or (old or {}).get("colour") or ""),
            "shape": _shape(dd, data.get("shape"), (old or {}).get("shape")),
            "voice": str(data.get("voice") or (old or {}).get("voice") or "")[:20]}


def _shape(dd: DataDir, wanted: Optional[str], had: Optional[str]) -> str:
    """The shape asked for, if it is unlocked - or the one the expert already
    had, which it keeps even if the rule changes."""
    if wanted in (None, "", had):
        return had or ""
    if wanted not in SHAPES:
        raise ExpertError("no such shape")
    state = next(x for x in shapes(dd) if x["shape"] == wanted)
    if not state["unlocked"]:
        raise ExpertError(f"the {SHAPE_WORDS[wanted]} is still locked - it needs {state['need']} "
                          f"(you have {state['have']} of {state['of']})")
    return wanted


def listing(dd: DataDir) -> list[dict]:
    """Every expert, with how much it knows: the cards on its boards (gone
    boards dropped from view, not from the file)."""
    counts = {b["id"]: (b["title"], b["node_count"]) for b in list_boards(dd)}
    out = []
    for e in load(dd):
        boards = [{"id": b, "title": counts[b][0], "cards": counts[b][1]}
                  for b in e.get("boards", []) if b in counts]
        out.append({**e, "boards": boards, "cards": sum(b["cards"] for b in boards)})
    return out


def create(dd: DataDir, data: dict) -> dict:
    with _lock:
        experts = load(dd)
        e = {"id": ulid(), **_clean(dd, data)}
        e["colour"] = e["colour"] or COLOURS[len(experts) % len(COLOURS)]
        if not e["voice"]:                           # a voice of its own, by turn (voice.CAST)
            from .voice import CAST
            e["voice"] = CAST[len(experts) % len(CAST)]
        if not e["shape"]:                           # take turns among the unlocked ones
            open_ = [x["shape"] for x in shapes(dd) if x["unlocked"]]
            e["shape"] = open_[len(experts) % len(open_)]
        _save(dd, experts + [e])
        return e


def update(dd: DataDir, expert_id: str, data: dict) -> dict:
    with _lock:
        experts = load(dd)
        for i, e in enumerate(experts):
            if e["id"] == expert_id:
                experts[i] = {"id": expert_id, **_clean(dd, data, e)}
                _save(dd, experts)
                return experts[i]
    raise ExpertError("no such expert")


def delete(dd: DataDir, expert_id: str) -> bool:
    with _lock:
        experts = load(dd)
        kept = [e for e in experts if e["id"] != expert_id]
        _save(dd, kept)
        return len(kept) < len(experts)


def restore(dd: DataDir, incoming: list[dict], replace: bool = False) -> int:
    """Experts from a backup: new ones added; ones already here kept, or
    replaced with `replace`. Links to boards that are not here are dropped."""
    present = {b["id"] for b in list_boards(dd)}
    with _lock:
        experts = {e["id"]: e for e in load(dd)}
        added = 0
        for e in incoming:
            if not isinstance(e, dict) or not e.get("id") or (e["id"] in experts and not replace):
                continue
            boards = [b for b in e.get("boards", []) if b in present]
            if boards:
                experts[e["id"]] = {**e, "boards": boards}
                added += 1
        _save(dd, list(experts.values()))
        return added


def get(dd: DataDir, expert_id: str) -> dict:
    e = next((e for e in load(dd) if e["id"] == expert_id), None)
    if e is None:
        raise ExpertError("no such expert")
    return e


# --------------------------------------------------------------- searching --

@dataclass
class Library:
    """An expert's boards, read and indexed for one question."""
    nodes: dict[str, dict]          # every card on every board - ids are unique across boards
    edges: list[dict]
    board_of: dict[str, str]        # card -> its board
    titles: dict[str, str]          # board -> its title
    indexes: dict[str, Any]         # board -> its search index
    conns: dict[str, sqlite3.Connection]


def library(dd: DataDir, expert: dict, embedder=None, embed_model: str = "",
            background: bool = True) -> Library:
    titles = {b["id"]: b["title"] for b in list_boards(dd)}
    lib = Library({}, [], {}, {}, {}, {})
    for bid in expert.get("boards", []):
        if bid not in titles:
            continue
        conn = conn_for(dd, bid)
        g = read_graph(conn, bid)
        nodes = {n["id"]: n for n in g["nodes"]}
        lib.indexes[bid] = board_index(dd, conn, bid, nodes, embedder, embed_model, background)
        lib.conns[bid], lib.titles[bid] = conn, titles[bid]
        lib.nodes.update(nodes)
        lib.edges += g["edges"]
        lib.board_of.update({nid: bid for nid in nodes})
    return lib


def search(lib: Library, question: str, embedder=None, reranker=None,
           budget: Optional[int] = None, also: Optional[list[str]] = None) -> retrieve.Found:
    """Each board searched as a board is (retrieve.score), merged by
    reciprocal rank so every board's best cards come near the top, then the
    strongest that fit one budget. `also`: what the reading of the question
    says to look up as well (DESIGN.md 46) - added beside the question, which
    counts twice, never instead of it (37)."""
    rankings, used = [], ["keywords"]
    for bid, index in lib.indexes.items():
        mine = {k: v for k, v in lib.nodes.items() if lib.board_of[k] == bid}
        if not mine:
            continue
        scored, how = retrieve.score(index, question, mine, lib.edges, embedder, reranker)
        rankings += [retrieve.ranked(scored)] * (2 if also else 1)
        for phrase in also or []:
            extra, _ = retrieve.score(index, phrase, mine, lib.edges, embedder, reranker)
            rankings.append(retrieve.ranked(extra))
        used = how
    cost = {}
    for index in lib.indexes.values():
        cost.update(index.costs)
    return retrieve.fit(retrieve.fuse(*rankings), lib.nodes, lib.edges, cost,
                        retrieve.BUDGET if budget is None else budget, used)


def context(dd: DataDir, expert: dict, question: str, history: Optional[list[dict]] = None,
            embedder=None, embed_model: str = "", reranker=None,
            reading: Optional[dict] = None) -> tuple[Context, Library]:
    """What the model reads to answer as this expert - with its reading of
    the question, if one was made (ask.analyse)."""
    lib = library(dd, expert, embedder, embed_model)
    if not lib.nodes:
        raise ExpertError("this expert's boards have no cards yet")
    asked = [str(t.get("text", "")) for t in (history or []) if t.get("role") == "user"]
    found = search(lib, " ".join(asked[-2:] + [question]), embedder, reranker,
                   also=(reading or {}).get("look_up") or None)
    ids = found.ids
    blocks = {i: card_text(lib.nodes[i], lib.nodes) + f"\n    (on the board {lib.titles[lib.board_of[i]]!r})"
              for i in ids}
    pictures, budget, left = [], SEARCH_PICTURE_TOKENS, 0
    for i in ids:
        n = lib.nodes[i]
        c = n.get("content") or {}
        sha = c.get("sha256") if n["type"] == "image" else c.get("poster") if n["type"] == "video" else None
        if not sha:
            continue
        if budget <= 0:
            left += 1
            continue
        bid = lib.board_of[i]
        got = _picture(dd, lib.conns[bid], bid, sha)
        if got:
            pictures.append(Picture(i, *got))
            budget -= got[1]
    inside = set(ids)
    from .ask import KIND_WORDS
    links = [f"[{e['source']}] {KIND_WORDS.get(e['kind'], e['kind'])} [{e['target']}]"
             + (f" ({e['label']})" if e.get("label") else "")
             for e in lib.edges if e["source"] in inside and e["target"] in inside]
    text = (f"CARDS (the {len(ids)} of your {len(lib.nodes)} cards, across "
            f"{len(lib.titles)} boards, that match the question)\n" + "\n\n".join(blocks[i] for i in ids))
    if links:
        text += "\n\nRELATIONSHIPS\n" + "\n".join(links)
    persona = f"You are {expert['name']}." + (f" {expert['brief']}" if expert.get("brief") else "")
    ctx = Context(ids, text, pictures, whole=True, pictures_left_out=left, mode="search",
                  total=len(lib.nodes), how=found.how, persona=persona,
                  where={i: {"board": lib.board_of[i], "title": lib.nodes[i].get("title") or "Untitled"}
                         for i in ids}, reading=reading or {})
    return ctx, lib


# ----------------------------------------------------------------- guide --

GUIDE = """You help a person choose which of their experts to ask. Each expert
knows only the boards it is connected to. Given the question and, for the
experts whose boards answer it best, the cards that matched, say in one or two
plain sentences who can help and why. Name only the experts listed. No
preamble."""


def guide(dd: DataDir, question: str, model=None, embedder=None, embed_model: str = "",
          reranker=None) -> dict:
    """Who can help with this question, and why (DESIGN.md 40)."""
    scores = []
    for e in load(dd):
        lib = library(dd, e, embedder, embed_model)
        if not lib.nodes:
            continue
        found = search(lib, question, embedder, reranker, budget=1500)
        best = found.ids[:4]
        if reranker is not None and best:
            docs = [card_passage(card_fields(lib.nodes[i], lib.nodes)) for i in best]
            try:
                top = max(reranker.rerank(question[-500:], [d[:1300] for d in docs]))
            except Exception:
                top = 0.0
        else:
            top = max(found.scores.values(), default=0.0)
        scores.append({"id": e["id"], "name": e["name"], "score": float(top),
                       "cards": [lib.nodes[i].get("title") or "Untitled" for i in best[:3]]})
    if not scores:
        return {"picked": [], "why": "You have no experts yet - make one first."}
    scores.sort(key=lambda s: -s["score"])
    lead = scores[0]["score"]
    picked = [s for s in scores if s["score"] >= max(lead * 0.6, 0.05)][:2] or scores[:1]
    why = ""
    if model is not None:
        listing_ = "\n".join(f"- {s['name']}: {', '.join(s['cards'])}" for s in picked)
        try:
            why = " ".join(model.chat([{"role": "system", "content": GUIDE},
                                       {"role": "user", "content": f"QUESTION\n{question}\n\nEXPERTS\n{listing_}"}],
                                      max_tokens=120, think=False, temperature=0.3)["text"].split())[:400]
        except Exception:
            why = ""
    if not why:
        why = (" and ".join(s["name"] for s in picked) + " can help: "
               + "; ".join(f"{s['name']} knows {', '.join(s['cards'][:2])}" for s in picked) + ".")
    return {"picked": picked, "why": why}
