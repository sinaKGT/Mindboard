"""Ask your board: a question about exactly the cards the person put in
context, answered by the local model (DESIGN.md 24).

This module turns cards into what the model reads - text, relationships and
the pictures themselves - writes the rules it answers by, and checks what comes
back. It only reads the board; nothing here changes it.
"""

from __future__ import annotations

import base64
import io
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .assets import resolve
from .ops import read_graph
from .paths import DataDir

MAX_SIDE = 1024            # pictures are shrunk to this; larger costs time, not insight
CONTEXT_TOKENS = 32768     # what the model is started with (llm.LocalModel)
ANSWER_TOKENS = 3072
THINKING_TOKENS = 8192
BEYOND = "Beyond your board:"
CITE = re.compile(r"\[\[([0-9A-Za-z]{26})\]\]")

KIND_WORDS = {
    "relates": "relates to", "causes": "causes", "supports": "supports",
    "contradicts": "contradicts", "part-of": "is part of", "example-of": "is an example of",
}

SYSTEM = f"""You help one person think, using their own mind map.

CARDS are their notes. Each starts with its id in brackets, like [01ABC...]. A
PICTURE belongs to the card named just before it. RELATIONSHIPS are claims the
person made about how their cards connect - treat them as their judgement, not
yours; "contradicts" means they think the two disagree.

How to answer:
1. Answer from the cards first. Every time you use something from a card, cite
   it straight after, in double brackets: [[01ABC...]]. Use only ids you were
   given. Never invent one.
2. Keep their map and your own knowledge apart. If you add anything the cards
   do not say - missing ideas, gaps in their thinking, corrections, what to
   read next - put it after a line that reads exactly "{BEYOND}", and cite
   nothing there.
3. If the cards do not answer the question, say so plainly first.
4. Be direct and brief. Plain sentences and short lists; no tables."""


class AskError(ValueError):
    """The question cannot be asked as it stands - phrased for the person."""


@dataclass
class Picture:
    card: str
    data_url: str
    tokens: int


@dataclass
class Context:
    cards: list[str]                       # every card the model sees, groups expanded
    text: str
    pictures: list[Picture] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return estimate(self.text) + sum(p.tokens for p in self.pictures)


def estimate(text: str) -> int:
    """Tokens, roughly. English runs near four characters a token; three keeps
    the estimate on the safe side for ids, code and other languages."""
    return len(text) // 3 + 1


# ---------------------------------------------------------------- cards --

def expand(nodes: dict[str, dict], chosen: list[str]) -> list[str]:
    """The chosen cards, and for a group everything inside it at any depth -
    adding a group means asking about the group. Order kept, nothing twice."""
    children: dict[Optional[str], list[str]] = {}
    for n in nodes.values():
        children.setdefault(n.get("parent"), []).append(n["id"])
    out: list[str] = []
    seen: set[str] = set()

    def visit(nid: str) -> None:
        if nid in seen or nid not in nodes:
            return
        seen.add(nid)
        out.append(nid)
        if nodes[nid]["type"] == "meta":
            for kid in children.get(nid, []):
                visit(kid)

    for nid in chosen:
        visit(nid)
    return out


def _where(node: dict, nodes: dict[str, dict]) -> str:
    parts: list[str] = []
    cur = nodes.get(node.get("parent") or "")
    while cur:
        parts.insert(0, cur.get("title") or "a group")
        cur = nodes.get(cur.get("parent") or "")
    return " / ".join(parts)


def card_text(node: dict, nodes: dict[str, dict]) -> str:
    c = node.get("content") or {}
    kind = {"meta": "group", "file": "file"}.get(node["type"], node["type"])
    place = _where(node, nodes)
    head = f"[{node['id']}] ({kind}{f', in {place}' if place else ''}) {node.get('title') or 'Untitled'}"
    lines = []
    note = str(c.get("text") or c.get("summary") or "").strip()
    if node["type"] == "link":
        lines += [f"address: {c['url']}"] if c.get("url") else []
        lines += [f"page says: {c['excerpt']}"] if c.get("excerpt") else []
    elif node["type"] == "file":
        lines += [f"file: {c.get('filename') or c.get('alt') or 'unnamed'}"]
    elif node["type"] in ("image", "video") and c.get("alt") and c["alt"] != node.get("title"):
        lines += [f"file: {c['alt']}"]
    if note:
        lines.append(note)
    return head + "".join(f"\n    {line}" for line in "\n".join(lines).splitlines())


def _picture(dd: DataDir, conn: sqlite3.Connection, bid: str, sha: str) -> Optional[tuple[str, int]]:
    """A card's picture as the model takes it: shrunk, re-encoded, inlined."""
    path = resolve(dd, conn, bid, sha)
    if path is None:
        return None
    try:
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
            w, h = im.size
    except Exception:
        return None
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return url, (w * h) // (32 * 32) + 16   # Qwen sees a picture in 32-pixel squares


def build(dd: DataDir, conn: sqlite3.Connection, bid: str, chosen: list[str]) -> Context:
    graph = read_graph(conn, bid)
    nodes = {n["id"]: n for n in graph["nodes"]}
    ids = expand(nodes, chosen)
    if not ids:
        raise AskError("add a card to the context first")
    inside = set(ids)
    blocks = [card_text(nodes[i], nodes) for i in ids]
    pictures: list[Picture] = []
    for i in ids:
        n = nodes[i]
        c = n.get("content") or {}
        sha = c.get("sha256") if n["type"] == "image" else c.get("poster") if n["type"] == "video" else None
        got = _picture(dd, conn, bid, sha) if sha else None
        if got:
            pictures.append(Picture(i, *got))

    edges = [e for e in graph["edges"]
             if e["source"] in inside and e["target"] in inside]
    links = [f"[{e['source']}] {KIND_WORDS.get(e['kind'], e['kind'])} [{e['target']}]"
             + (f" ({e['label']})" if e.get("label") else "") for e in edges]
    text = "CARDS\n" + "\n\n".join(blocks)
    if links:
        text += "\n\nRELATIONSHIPS\n" + "\n".join(links)
    return Context(ids, text, pictures)


# ------------------------------------------------------------- messages --

def messages(ctx: Context, question: str, history: list[dict]) -> list[dict]:
    """The context travels with the newest question, so it is always the cards
    in context *now*; earlier turns keep only what was said."""
    msgs: list[dict] = [{"role": "system", "content": SYSTEM}]
    for turn in history:
        if turn.get("role") in ("user", "assistant") and str(turn.get("text", "")).strip():
            msgs.append({"role": turn["role"], "content": str(turn["text"])})
    parts: list[dict[str, Any]] = [{"type": "text", "text": ctx.text}]
    for p in ctx.pictures:
        parts.append({"type": "text", "text": f"PICTURE on card [{p.card}]:"})
        parts.append({"type": "image_url", "image_url": {"url": p.data_url}})
    parts.append({"type": "text", "text": f"QUESTION\n{question.strip()}"})
    msgs.append({"role": "user", "content": parts})
    return msgs


def check_fits(ctx: Context, question: str, history: list[dict], think: bool) -> int:
    """Refuse up front rather than let the model silently drop the start."""
    used = (ctx.tokens + estimate(SYSTEM) + estimate(question)
            + sum(estimate(str(t.get("text", ""))) for t in history))
    room = CONTEXT_TOKENS - ANSWER_TOKENS - (THINKING_TOKENS if think else 0)
    if used > room:
        raise AskError(
            f"that is too much to ask about at once: about {used:,} tokens "
            f"({len(ctx.cards)} cards, {len(ctx.pictures)} pictures) where {room:,} fit"
            + (" - try without thinking deeper" if think else " - take some cards out"))
    return used


# ------------------------------------------------------------ the answer --

def check_citations(answer: str, allowed: set[str]) -> dict:
    """Keep citations of cards that were in context; remove the rest and count
    them - that count is how often the model invents a source."""
    cited: list[str] = []
    dropped: list[str] = []

    def keep(m: re.Match) -> str:
        cid = m.group(1)
        if cid in allowed:
            if cid not in cited:
                cited.append(cid)
            return m.group(0)
        dropped.append(cid)
        return ""

    clean = CITE.sub(keep, answer)
    clean = re.sub(r"[ \t]+([.,;:])", r"\1", clean)
    board, _, beyond = clean.partition(BEYOND)
    return {"answer": clean.strip(), "board": board.strip(), "beyond": beyond.strip(),
            "cited": cited, "dropped": dropped}


def answer_events(model, ctx: Context, question: str, history: list[dict],
                  think: bool = False) -> Iterator[dict]:
    """Everything the person sees, in order: what went in, the thinking, the
    words as they come, and finally the checked answer."""
    used = check_fits(ctx, question, history, think)
    yield {"event": "context", "cards": ctx.cards, "pictures": len(ctx.pictures), "tokens": used}
    text: list[str] = []
    timings: dict = {}
    for ev in model.stream(messages(ctx, question, history),
                           max_tokens=ANSWER_TOKENS + (THINKING_TOKENS if think else 0),
                           think=think):
        if ev["type"] == "text":
            text.append(ev["text"])
            yield {"event": "text", "text": ev["text"]}
        elif ev["type"] == "thinking":
            yield {"event": "thinking", "text": ev["text"]}
        elif ev["type"] == "done":
            timings = ev
    result = check_citations("".join(text), set(ctx.cards))
    yield {"event": "done", **result,
           "tokens_per_second": timings.get("tokens_per_second")}
