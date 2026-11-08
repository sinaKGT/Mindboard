"""Ask your board: a question about exactly the cards the person put in
context, answered by the local model (DESIGN.md 24).

This module turns cards into what the model reads - text, relationships and
the pictures themselves - writes the rules it answers by, and checks what comes
back. It only reads the board; nothing here changes it.
"""

from __future__ import annotations

import base64
import json
import io
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from . import retrieve
from .assets import resolve
from .search_index import BoardIndex, PictureNotes
from .ops import read_graph
from .paths import DataDir

MAX_SIDE = 1024            # pictures are shrunk to this; larger costs time, not insight
CONTEXT_TOKENS = 32768     # what the model is started with (llm.LocalModel)
ANSWER_TOKENS = 3072
THINKING_TOKENS = 8192
# A search brings the found cards' pictures, best match first, only up to this
# many tokens; the other cards' titles and captions still go in. Measured on
# RAG Deep Knowledge: 6,000 brought ten pictures and the first words at 5.0 s,
# 2,000 brings four - the best-matching cards' - and the first words at 3.0 s
# (DESIGN.md 35). Cards chosen by hand always bring all their pictures.
SEARCH_PICTURE_TOKENS = 2000
BEYOND = "Beyond your board:"
EXPLAIN = ("Explain more: I want a full explanation, not only what the cards say. Explain "
           "it step by step, in plain words. Where the cards and our conversation fall "
           "short, explain from your own knowledge - do not answer that the cards cannot "
           "explain it. Give one or two concrete examples that make it clear. Cite cards for "
           f"what comes from them, and put what comes from your own knowledge after the line "
           f"\"{BEYOND}\". Finish with the {'NEXT:'} line as always.")
# the model sometimes dresses the line up - **Beyond your board:**, a heading -
# and the split then left the stray ** on both sides of it
BEYOND_MARK = re.compile(r"(?:^[ \t]*#{1,6}[ \t]*)?(?:\*\*|__)?[ \t]*Beyond your board:[ \t]*"
                         r"(?:\*\*|__)?", re.MULTILINE)
CITE = re.compile(r"\[\[([0-9A-Za-z]{26})\]\]")
LONE_ID = re.compile(r"(?<!\[)\[([0-9A-Z]{26})\](?!\])")
# the question the model offers to answer next, on the answer's last line
NEXT = "NEXT:"
NEXT_LINE = re.compile(r"^[ \t*_>#-]*NEXT:[ \t*_]*(.*?)[ \t*_]*$", re.MULTILINE)

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
4. Be direct and brief. Plain sentences and short lists; no tables.
5. Finish with one last line that starts exactly "{NEXT}" and holds one
   question the person could ask next: short, in their words, taking what they
   just asked one step further, answerable from their cards or your knowledge."""


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
    whole: bool = False                    # nothing chosen: asked about the whole board
    pictures_left_out: int = 0
    # how the cards were chosen: "chosen" by hand, or found by a "search" of
    # the whole board (retrieve.py) - never the whole board sent entire
    mode: str = "chosen"
    total: int = 0                         # cards on the board
    how: str = ""                          # what a search went by

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


# cards a question embeds itself before searching - what one person writes
# between two questions; more than that waits for the background indexer
EMBED_NOW = 32


def board_index(dd: DataDir, conn: sqlite3.Connection, bid: str, nodes: dict[str, dict],
                embedder: Optional[retrieve.Embedder], embed_model: str,
                background: bool = False) -> BoardIndex:
    """The board's search index, brought up to the board's current revision,
    with what each card costs to send - worked out once per revision, not for
    every card at every question.

    With `background`, the app's way, a question never waits for embedding:
    the text index is brought up to date (0.2 s at ten thousand cards), the
    few cards just written are embedded on the spot so the question can find
    them by meaning, and anything more is left to the background indexer -
    the search uses the vectors made so far. Without it (the terminal, the
    evaluator) every card is embedded first."""
    row = conn.execute("SELECT revision FROM board WHERE id=?", (bid,)).fetchone()
    revision = row["revision"] if row else 0
    index = BoardIndex.get(dd, bid)
    notes = PictureNotes.get(dd).notes
    try:
        if not background:
            index.sync(nodes, revision, embedder, embed_model, notes)
        else:
            index.sync_text(nodes, revision, embed_model if embedder is not None else None, notes)
            if embedder is not None and 0 < len(index.pending()) <= EMBED_NOW:
                index.embed_some(embedder, EMBED_NOW)
    except Exception:
        # the embedder failed: keep the text index, search without meaning
        index.sync_text(nodes, revision)
    if index.costs_revision != revision or len(index.costs) != len(nodes):
        index.costs = {i: estimate(card_text(n, nodes)) for i, n in nodes.items()}
        index.costs_revision = revision
    return index


def build(dd: DataDir, conn: sqlite3.Connection, bid: str, chosen: list[str],
          question: str = "", history: Optional[list[dict]] = None,
          embedder: Optional[retrieve.Embedder] = None, embed_model: str = "",
          reranker: Optional[retrieve.Reranker] = None, background: bool = False,
          keep: Optional[list[str]] = None, explain: bool = False) -> Context:
    """The context for a question. Nothing chosen means the whole board, and
    the whole board is always searched (retrieve.py), never sent: a board may
    hold ten thousand cards, and a small one asked the same way behaves the
    same way as it grows. Cards chosen by hand are never searched: they are
    exactly what was asked about.

    `keep` are the cards earlier answers in this conversation drew on (DESIGN
    34): they stay in context, first - so the model's prompt cache still holds
    them - and a search fills the rest of the budget. With `explain` (Explain
    more) there is no search at all: the answer comes from those cards and the
    conversation."""
    graph = read_graph(conn, bid)
    nodes = {n["id"]: n for n in graph["nodes"]}
    whole = not chosen
    kept = [i for i in dict.fromkeys(keep or []) if i in nodes]
    if whole and explain:
        ids, mode, how = expand(nodes, kept), "explain", ""
    elif whole:
        if not nodes:
            raise AskError("this board has no cards yet")
        if not question.strip():
            raise AskError("ask a question - the board is searched for the cards it is about")
        # a follow-up ("which of those...") is searched with what led up to it
        asked = [str(t.get("text", "")) for t in (history or []) if t.get("role") == "user"]
        query = " ".join(asked[-2:] + [question])
        index = board_index(dd, conn, bid, nodes, embedder, embed_model, background)
        kept = expand(nodes, kept)
        room = max(retrieve.BUDGET - sum(index.costs.get(i, 50) for i in kept), retrieve.BUDGET // 4)
        found = retrieve.search(index, query, nodes, graph["edges"], index.costs, embedder,
                                budget=room, reranker=reranker)
        ids = kept + [i for i in found.ids if i not in set(kept)]
        mode, how = "search", found.how
    else:
        ids, mode, how = expand(nodes, chosen), "chosen", ""
        if not ids:
            raise AskError("none of those cards are on this board any more")
    blocks = {i: card_text(nodes[i], nodes) for i in ids}

    inside = set(ids)
    pictures: list[Picture] = []
    left_out = 0
    budget = SEARCH_PICTURE_TOKENS
    for i in ids:
        n = nodes[i]
        c = n.get("content") or {}
        sha = c.get("sha256") if n["type"] == "image" else c.get("poster") if n["type"] == "video" else None
        if not sha:
            continue
        if mode != "chosen" and budget <= 0:
            left_out += 1             # its card's title and caption still go in
            continue
        got = _picture(dd, conn, bid, sha)
        if got:
            pictures.append(Picture(i, *got))
            budget -= got[1]

    edges = [e for e in graph["edges"]
             if e["source"] in inside and e["target"] in inside]
    links = [f"[{e['source']}] {KIND_WORDS.get(e['kind'], e['kind'])} [{e['target']}]"
             + (f" ({e['label']})" if e.get("label") else "") for e in edges]
    if mode == "search":
        text = (f"BOARD OUTLINE (its groups)\n{retrieve.outline(nodes)}\n\n"
                f"CARDS (the {len(ids)} of the board's {len(nodes)} that match the question)\n")
    elif mode == "explain":
        text = ("CARDS (from this conversation so far)\n" if ids
                else "CARDS\n(none yet - answer from the conversation)\n")
    else:
        text = "CARDS\n"
    text += "\n\n".join(blocks[i] for i in ids)
    if links:
        text += "\n\nRELATIONSHIPS\n" + "\n".join(links)
    return Context(ids, text, pictures, whole=whole, pictures_left_out=left_out,
                   mode=mode, total=len(nodes), how=how)


# ------------------------------------------------------------- messages --

READY = "I have read these cards. Ask away."


def messages(ctx: Context, question: str, history: list[dict]) -> list[dict]:
    """The cards come first and stay put; the conversation follows them; the
    newest question comes last.

    The order is for speed. llama-server reuses whatever start of the prompt it
    has already read, so with the cards first a follow-up reads only what is
    new. With the cards after the conversation - as this first did - every
    question changed the start of the prompt, and a 13,700-token context was
    read again from the top each time: four minutes on a shared graphics card.
    The short reply after the cards keeps the turns alternating, as the chat
    template expects. Change the cards and the start changes - and they are
    read again, as they should be."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": ctx.text}]
    for p in ctx.pictures:
        parts.append({"type": "text", "text": f"PICTURE on card [{p.card}]:"})
        parts.append({"type": "image_url", "image_url": {"url": p.data_url}})
    msgs: list[dict] = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": parts},
                        {"role": "assistant", "content": READY}]
    for turn in history:
        if turn.get("role") in ("user", "assistant") and str(turn.get("text", "")).strip():
            msgs.append({"role": turn["role"], "content": str(turn["text"])})
    # Explain more asks for a fuller answer; said with the question, not in
    # the system prompt, so the start of the prompt - and its cache - stay put
    msgs.append({"role": "user", "content": question.strip()
                 + (f"\n\n({EXPLAIN})" if ctx.mode == "explain" else "")})
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
            + (" - try without thinking deeper" if think
               else " - add only the cards you mean to the context" if ctx.whole
               else " - take some cards out"))
    return used


# ------------------------------------------------------------ the answer --

def split_next(answer: str) -> tuple[str, str]:
    """The answer, and the question it offers next - taken off its last line.
    Only a line that ends the answer counts, so a "NEXT:" the model happens to
    write mid-answer stays where it is. No line, no offer."""
    found = [m for m in NEXT_LINE.finditer(answer) if not answer[m.end():].strip()]
    if not found:
        return answer, ""
    m = found[-1]
    question = re.sub(r"[ \t]+([.,;:?!])", r"\1", CITE.sub("", m.group(1)))
    question = question.strip().strip('"“”').strip()
    return answer[:m.start()].rstrip(), question


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

    # the model sometimes copies an id as the RELATIONSHIPS list writes it,
    # in single brackets: read it as the citation it is meant to be
    answer = LONE_ID.sub(r"[[\1]]", BEYOND_MARK.sub(BEYOND, answer))
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
    yield {"event": "context", "cards": ctx.cards, "pictures": len(ctx.pictures), "tokens": used,
           "whole": ctx.whole, "pictures_left_out": ctx.pictures_left_out,
           "mode": ctx.mode, "total": ctx.total, "how": ctx.how}
    text: list[str] = []
    timings: dict = {}
    for ev in model.stream(messages(ctx, question, history),
                           max_tokens=ANSWER_TOKENS + (THINKING_TOKENS if think else 0),
                           think=think):
        if ev["type"] == "progress":
            yield {"event": "progress", "total": ev["total"], "cached": ev["cached"],
                   "done": ev["done"]}
        elif ev["type"] == "text":
            text.append(ev["text"])
            yield {"event": "text", "text": ev["text"]}
        elif ev["type"] == "thinking":
            yield {"event": "thinking", "text": ev["text"]}
        elif ev["type"] == "done":
            timings = ev
    answer, next_question = split_next("".join(text))
    result = check_citations(answer, set(ctx.cards))
    yield {"event": "done", **result, "next": next_question,
           "stopped": bool(timings.get("stopped")),
           "tokens_per_second": timings.get("tokens_per_second")}


# ------------------------------------------------------------ to a card --

DRAFT_KINDS = ("relates", "supports", "example-of", "part-of", "causes", "contradicts")
DRAFT_SYSTEM = """You turn one answer from a conversation into a card for a person's mind map.

Reply with JSON only, no other text:
{"title": "...", "text": "...", "link_to": "...", "kind": "..."}

- title: a short name for the idea, at most 8 words.
- text: the knowledge itself in 1-4 plain sentences - not the conversation, no
  citations, no "Beyond your board".
- link_to: the id of the one card below it belongs with most, or "" if none.
- kind: how the new card relates to that card: relates, supports, example-of,
  part-of, causes or contradicts. A new example of an idea is example-of."""


def draft_card(model, nodes: dict[str, dict], question: str, answer: str,
               cited: list[str]) -> dict:
    """A card made from an answer, for the person to check before it is added
    (DESIGN 34): title, text, the card to link it to and how. Nothing is
    written here - the board only changes when the person presses Add, through
    the op-log like any edit."""
    cards = [i for i in dict.fromkeys(cited) if i in nodes]
    listing = "\n".join(f"{i}: {nodes[i].get('title') or 'Untitled'}" for i in cards) or "(none)"
    clean = CITE.sub("", BEYOND_MARK.sub(BEYOND, answer)).replace(BEYOND, "\nFrom general knowledge:")
    fallback = {"title": question.strip()[:70], "text": clean.strip()[:500],
                "link_to": cards[0] if cards else "", "kind": "relates"}
    try:
        reply = model.chat([{"role": "system", "content": DRAFT_SYSTEM},
                            {"role": "user", "content":
                             f"QUESTION\n{question.strip()}\n\nANSWER\n{clean.strip()[:6000]}"
                             f"\n\nCARDS IT DREW ON\n{listing}"}],
                           max_tokens=400, think=False, temperature=0.3)["text"]
        got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except Exception:
        return fallback                      # a model that strays still gives a usable card
    title = str(got.get("title") or "").strip()[:80] or fallback["title"]
    text = CITE.sub("", str(got.get("text") or "")).strip()[:800] or fallback["text"]
    link = str(got.get("link_to") or "").strip()
    kind = str(got.get("kind") or "").strip()
    return {"title": title, "text": text,
            "link_to": link if link in cards else (cards[0] if cards else ""),
            "kind": kind if kind in DRAFT_KINDS else "relates"}


# ------------------------------------------------------------ pictures --

DESCRIBE = ("Describe this picture for a search index, in 1-3 plain sentences: what it "
            "shows, any words or numbers written in it, and what kind of picture it is "
            "(photo, diagram, screenshot, chart). No preamble.")


def describe_picture(model, dd: DataDir, conn: sqlite3.Connection, bid: str, sha: str) -> str:
    """What a picture shows, in words, for search (DESIGN.md 38). "" when the
    picture cannot be read."""
    got = _picture(dd, conn, bid, sha)
    if got is None:
        return ""
    reply = model.chat([{"role": "user", "content": [
        {"type": "text", "text": DESCRIBE},
        {"type": "image_url", "image_url": {"url": got[0]}}]}],
        max_tokens=160, think=False, temperature=0.2)
    return " ".join(str(reply.get("text") or "").split())[:600]
