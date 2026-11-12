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
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from . import retrieve
from .assets import resolve
from .search_index import BoardIndex, FileNotes, PictureNotes, card_notes, file_of
from .ops import read_graph
from .paths import DataDir

log = logging.getLogger("mindboard.ask")

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
# Told its cited pictures are shown, the model once also wrote a markdown image
# - "[![diagram](file.png)](https://...)" with an address it made up. The
# citation shows the picture (DESIGN.md 48); an image it writes is taken out.
MD_IMAGE = re.compile(r"\[?!\[[^\]\n]*\]\([^)\n]*\)(?:\]\([^)\n]*\))?")
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
yours; "contradicts" means they think the two disagree. Anything you take from
a PICTURE is cited like a card's text: cite the card it belongs to. A picture card you cite
is shown to the person beside your answer: when they ask to see, show or find a
picture, diagram or screenshot, cite the cards whose pictures show it - never
say you cannot show pictures, and never write an image or a link to one: the
citation is what shows it. A FILE card's text is excerpted under it ("file
holds"): when a file covers what they ask, cite it - it is offered to them to
read more.

How to answer:
1. Answer from the cards first. Every time you use something from a card, cite
   it straight after, in double brackets: [[01ABC...]]. Use only ids you were
   given. Never invent one.
2. Keep their map and your own knowledge apart. If you add anything the cards
   do not say - missing ideas, gaps in their thinking, corrections, what to
   read next - put it after a line that reads exactly "{BEYOND}", and cite
   nothing there.
3. If the cards do not answer the question, say so plainly first.
4. Start with the answer itself. Never open with "Based on your cards", "Your
   board says" or the like, and do not keep mentioning the cards or the board -
   the citations already show where things come from.
5. Short and exact by default: a few sentences or a short list, nothing
   padded. Explain at length - steps, examples - only when they ask you to
   explain, or ask how or why. Plain sentences and short lists; no tables.
6. Finish with three lines, each starting exactly "{NEXT}", each holding one
   question the person could ask next: short, in their words, about what they
   just asked and the cards in front of you - one going deeper, one putting it
   to use, one on a neighbouring idea from the cards."""


# ------------------------------------------------------ planning the answer --
#
# DESIGN.md 46-47. Asked "which architecture for a RAG system for a school,
# where students ask about their lectures?", an expert answered for "a school
# project" and recommended the simplest thing, weighing nothing: it answered in
# one pass, its rules pushing it to summarise the cards that matched. Now it
# first plans. It reads the question - what kind it is, the person's situation
# in their own terms, the points that decide the answer - and, when one search
# cannot answer it, writes the steps to take first: what to find in the cards
# and what to work out from it, one step per look. The steps are run in turn
# (experts.run_plan), each seeing what the earlier ones found, and the answer is
# written from their findings. The plan is shown as it runs, so a misreading is
# seen at once.
#
# A question about one thing gets no steps and is answered as before: the
# planner of DESIGN.md 37 split every question into phrases and made search
# worse, so here the steps are added beside the question's own search, never
# instead of it, and a single step - no better than that search - is dropped.

PLAN = """You plan how an expert will answer a person's question from their
notes (the cards) before the answer is written. Reply with JSON only:
{"kind": "...", "reading": "...", "weigh": ["..."],
 "steps": [{"find": "...", "work_out": "...", "uses": 0}]}

- kind: "fact" (what is, define, recall), "explain" (how or why), "advice"
  (what should they do or choose, for their situation), "compare" (which of
  these, how they differ) or "design" (how to build or plan something).
- reading: their situation and what they want, in one plain sentence in the
  second person ("You want ..."), in their terms. Keep their goal as big as
  they said it: never call it a project, homework, prototype or toy unless
  they did, and do not decide they are a beginner.
- weigh: for advice, compare and design only - the 3 to 6 things about their
  situation that decide the answer: who will use it and how, the material,
  its scale, how accurate it must be, what must be kept apart. Use only what
  they said or what plainly follows from it; where something that matters is
  left open, name it as an open point ("how many courses - unknown") rather
  than assume it. Never add a budget, deadline or limit they did not give.
  Short phrases. [] otherwise.
- steps: what must be looked up and worked out, in order, before the answer
  can be written - one step for each separate look in the notes. Plan from
  WHAT THE NOTES HOLD: an option worth checking is one the notes name, called
  by the name they give it; never plan a step for something they do not hold.
  Only when a single search cannot answer the question:
    * several parts: one step per part;
    * a chain - find one thing, then what it leads to: the later step names
      the earlier one in "uses";
    * advice, compare or design: one step for each option the notes name
      that could fit (2 or 3 of them - never just one: an answer that checks
      one option has weighed nothing), and a last step that weighs them
      against the points above.
  A fact or an explanation of one thing needs no steps: []. At most 4 steps.
    find: a short search phrase for the notes that hold it - its specific
      names and terms, not the whole question. "" for a step that only works
      from the earlier steps, like the weighing.
    work_out: what to work out, in one short sentence.
    uses: the number of the earlier step whose result it needs (1 is the
      first), or 0.

For example, asked "which database should our clinic use for patient
records?" (design) with notes titled "PostgreSQL", "MongoDB", "Backups" and
"Indexes", the steps are one per option the notes hold - not one per topic -
and then the weighing:
  {"find": "PostgreSQL", "work_out": "Does PostgreSQL fit a clinic's patient records?", "uses": 0},
  {"find": "MongoDB", "work_out": "Does MongoDB fit them?", "uses": 0},
  {"find": "", "work_out": "Weigh the two against the deciding points.", "uses": 0}
and "what is a foreign key?" (fact) gets "steps": []."""

STEP_MARK = "STEP"
KINDS = ("fact", "explain", "advice", "compare", "design")
WEIGHED = ("advice", "compare", "design")
MAX_STEPS = 4


def collect(model, messages: list[dict], max_tokens: int, temperature: float) -> tuple[str, bool]:
    """One reply, whole, inside a run that already holds the model (hold=False):
    the reply's text, and whether it was stopped."""
    text, stopped = [], False
    for ev in model.stream(messages, max_tokens=max_tokens, think=False,
                           temperature=temperature, hold=False):
        if ev["type"] == "text":
            text.append(ev["text"])
        elif ev["type"] == "done":
            stopped = bool(ev.get("stopped"))
    return "".join(text), stopped


def make_plan(model, question: str, history: Optional[list[dict]] = None,
              titles: Optional[list[str]] = None) -> dict:
    """The question, read and planned before it is answered: {kind, reading,
    weigh, steps} - or {} if the model's reply cannot be used, and the
    question is then answered as it stands. `titles`: the cards that best
    match the question, so the plan is made from what the notes hold rather
    than blind - planned blind, it asked for embedding models and vector
    databases the notes never mention, and every step came back empty."""
    convo = "\n".join(f"{t.get('role')}: {str(t.get('text', ''))[:300]}"
                      for t in (history or [])[-4:] if str(t.get("text", "")).strip())
    held = "\n".join(f"- {t}" for t in dict.fromkeys(titles or []) if t.strip())
    prompt = ((f"CONVERSATION SO FAR\n{convo}\n\n" if convo else "")
              + (f"WHAT THE NOTES HOLD (titles of the cards that best match)\n{held}\n\n" if held else "")
              + f"QUESTION\n{question.strip()}")
    reply = ""
    try:
        reply, stopped = collect(model, [{"role": "system", "content": PLAN},
                                         {"role": "user", "content": prompt}],
                                 max_tokens=600, temperature=0.1)
        if stopped:
            return {}
        got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except Exception as exc:
        # answered as it stands - but said, so a planner that keeps failing is seen
        log.warning("the plan could not be read (%s): %r", exc.__class__.__name__, reply[:400])
        return {}
    return clean_plan(got) if isinstance(got, dict) else {}


def clean_plan(got: dict) -> dict:
    """What the planner said, kept to what can be run."""
    def phrases(v, n):
        return [str(x).strip()[:120] for x in (v if isinstance(v, list) else []) if str(x).strip()][:n]

    kind = str(got.get("kind", "")).strip().lower()
    kind = kind if kind in KINDS else "explain"
    steps: list[dict] = []
    for s in got.get("steps") if isinstance(got.get("steps"), list) else []:
        if not isinstance(s, dict):
            continue
        find = " ".join(str(s.get("find") or "").split())[:120]
        work = " ".join(str(s.get("work_out") or "").split())[:200]
        if not (find or work):
            continue
        try:
            uses = int(s.get("uses") or 0)
        except (TypeError, ValueError):
            uses = 0
        steps.append({"find": find, "work_out": work or f"What the cards say about {find}.",
                      "uses": uses if 0 < uses <= len(steps) else 0})
    steps = steps[:MAX_STEPS]
    if len(steps) < 2 or not any(s["find"] for s in steps):
        steps = []          # one look is the question's own search (DESIGN.md 37)
    return {"kind": kind, "reading": str(got.get("reading", "")).strip()[:300],
            "weigh": phrases(got.get("weigh"), 6) if kind in WEIGHED else [], "steps": steps}


LENGTH = {"fact": "Answer in one to three sentences, then the three NEXT: lines.",
          "explain": "Explain it clearly, with a short example if it helps, then the three NEXT: lines."}


def findings_note(plan: dict) -> str:
    """What the steps found, said with the question for the answer to build on."""
    found = plan.get("findings") or []
    if not found:
        return ""
    steps = plan.get("steps") or []
    parts = [f"{f['i'] + 1}. {steps[f['i']]['work_out'] if f['i'] < len(steps) else ''}\n{f['text']}"
             for f in found]
    return ("\n\n(WHAT YOUR STEPS FOUND - you looked these up before answering:\n\n"
            + "\n\n".join(parts)
            + "\n\nBuild the answer on these findings and cite the cards they cite. Where a step "
              "found nothing in the cards, say so rather than fill the gap silently. Do not list "
              "the steps themselves.)")


def weighing(reading: dict) -> str:
    """What the answer is asked to do with the plan - said with the question,
    so the cached start of the prompt stays put."""
    note = findings_note(reading)
    if not reading.get("weigh"):
        return note + (f"\n\n({LENGTH[reading['kind']]})" if reading.get("kind") in LENGTH else "")
    points = "\n".join(f"- {w}" for w in reading["weigh"])
    return note + (
        f"\n\n(You read this as: {reading.get('reading', '')}\n"
        f"What decides the answer:\n{points}\n"
        "Answer briefly, in this order: your recommendation, in one or two sentences; why - "
        "one short line for each point above, saying how the recommended approach meets it; "
        "for an approach that does not fit, one line on why not; then one line on what would "
        "change this recommendation. Do not open by restating the question or mentioning the "
        "cards. End with the three NEXT: lines as always.)")


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
    persona: str = ""                      # an expert's name and brief (experts.py)
    # for an expert: each card's board and title, so a citation can open it
    where: dict = field(default_factory=dict)
    # an expert's plan for the question - its reading, steps and what they
    # found - made before answering (DESIGN.md 46-47)
    reading: dict = field(default_factory=dict)
    # PDFs that match the question itself, offered to read more (DESIGN.md 48)
    offer: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return estimate(self.text) + sum(p.tokens for p in self.pictures)


FILE_EXCERPT = 700       # characters of a PDF's text the model reads with its card


def file_excerpt(dd: DataDir, node: dict) -> str:
    """The start of a PDF's text, under its card, so the model knows what the
    file covers and can offer it (DESIGN.md 48). "" for other cards."""
    sha = file_of(node)
    text = FileNotes.get(dd).notes.get(sha) if sha else ""
    return f"\n    file holds: {text[:FILE_EXCERPT]}" if text else ""


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
    notes = card_notes(dd)
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
    blocks = {i: card_text(nodes[i], nodes) + file_excerpt(dd, nodes[i]) for i in ids}

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
            left_out += 1             # its card's title and caption still go in -
            seen = PictureNotes.get(dd).notes.get(sha)
            if seen:                  # and what the picture shows, in words (DESIGN 39)
                blocks[i] += seen_line(seen)
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


# ------------------------------------------------------------- dates --
#
# DESIGN.md 56. Asked when the latest visit was, the model kept naming 3 Nov
# on a board whose newest date was 11 Nov: it was never told today's date, and
# a 9B model compares dates written in words unreliably. A question about time
# now comes with today's date and the dates found in its cards, newest first -
# worked out here, not left to the model.

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MONTH_NAMES = ("January February March April May June July August September October "
               "November December").split()
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
# "3 Nov 2026", "22 Sep and 29 Oct 2026", "3-5 Nov 2026" (a day before its month)
DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\b\.?(?:\s*,?\s*(\d{{4}}))?", re.I)
# "Nov 3, 2026", "November 3rd 2026"
MONTH_DAY = re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?!\d)(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{4}}))?", re.I)
ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
YEAR = re.compile(r"\b(19|20)\d{2}\b")
TIME_WORDS = re.compile(
    r"\b(when|date|dates|dated|day|days|latest|last|recent(?:ly)?|newest|oldest|earliest|first|next|"
    r"upcoming|previous|before|after|since|until|week|weeks|month|months|year|years|today|yesterday|"
    r"tomorrow|ago|timeline|order|chronolog\w*|schedule|soon)\b|" + _MON, re.I)
CARD_HEAD = re.compile(r"^\[([0-9A-Z]{26})\]\s*(.*)$")
MAX_DATES = 20            # dates listed, newest first
DATE_QUOTE = 70           # characters of the words around a date, quoted with it


def found_dates(text: str, today, year: Optional[int] = None) -> list:
    """The dates written in a piece of text, as dates. A date without a year
    takes the year written after it in the same text, else `year`, else this
    year."""
    import datetime as dt

    years = [int(m.group(0)) for m in YEAR.finditer(text)] or ([year] if year else [])
    out = []

    def add(y, m, d):
        try:
            out.append(dt.date(y, m, d))
        except ValueError:
            pass

    for m in ISO.finditer(text):
        add(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for rx, day_first in ((DAY_MONTH, True), (MONTH_DAY, False)):
        for m in rx.finditer(text):
            day, mon = (m.group(1), m.group(2)) if day_first else (m.group(2), m.group(1))
            year = m.group(3)
            if not year:                     # "22 Sep and 29 Oct 2026": the year that follows
                year = years[-1] if years else today.year
            add(int(year), MONTHS[mon[:3].lower()], int(day))
    return sorted(set(out))


def around(text: str, d, today) -> str:
    """The words around where a date is written: a short window on it, so a
    long line still shows what the date is for - "...Updated 12 Nov 2026."."""
    at = 0
    for rx in (ISO, DAY_MONTH, MONTH_DAY):
        for m in rx.finditer(text):
            if d in found_dates(text[m.start():m.end()] + " " + str(d.year), today):
                at = m.start()
                break
        else:
            continue
        break
    start = max(0, min(at - 30, len(text) - DATE_QUOTE))
    piece = text[start:start + DATE_QUOTE].strip()
    return ("…" if start else "") + piece + ("…" if start + DATE_QUOTE < len(text) else "")


def say_date(d) -> str:
    return f"{d.day} {MONTH_NAMES[d.month - 1]} {d.year}"


def dates_note(ctx: "Context", question: str, today=None) -> str:
    """Today's date, and - for a question about time - the dates in the cards,
    newest first, each with the card it is on."""
    import datetime as dt

    today = today or dt.date.today()
    note = f"\n\n(Today is {dt.date.strftime(today, '%A')} {say_date(today)}.)"
    if not TIME_WORDS.search(question):
        return note
    on: dict = {}                            # date -> the cards it is written on
    for block in ctx.text.split("\n\n"):
        # a block is one card - the first one sits under the CARDS heading line
        lines = block.strip().split("\n")
        at = next((i for i, ln in enumerate(lines[:2]) if CARD_HEAD.match(ln)), None)
        if at is None:
            continue
        block = "\n".join(lines[at:])
        head = CARD_HEAD.match(lines[at])
        title = re.sub(r"^\(\w[\w-]*\)\s*", "", head.group(2)).strip()[:70] or "Untitled"
        block_years = [int(m.group(0)) for m in YEAR.finditer(block)]
        seen_here: set = set()
        # each date with the words around it, so "Updated 12 Nov 2026" is not
        # read as a visit beside "Meeting at the lab on 11 Nov 2026" (DESIGN.md 56)
        for i, line in enumerate(lines[at:]):
            for d in found_dates(line, today, block_years[-1] if block_years else None):
                if d in seen_here:
                    continue
                seen_here.add(d)
                said = line if i else head.group(2)
                said = " ".join(re.sub(r"^\(\w[\w-]*\)\s*", "", said).split())
                quote = f' - "{around(said, d, today)}"' if said and said != title else ""
                on.setdefault(d, []).append(f"{title}{quote} [{head.group(1)}]")
    if not on:
        return note
    lines = [f"- {say_date(d)}{' (after today)' if d > today else ''}: " + "; ".join(cards[:6])
             + (f"; and {len(cards) - 6} more" if len(cards) > 6 else "")
             for d, cards in sorted(on.items(), reverse=True)[:MAX_DATES]]
    return note[:-1] + (
        " The dates written in these cards, newest first, each with the words around it - "
        "use this order for latest, most recent, next or earliest, and cite the cards. Read "
        "what each date is for before using it: a date a list was updated or a report written "
        "is not a visit or a meeting.\n" + "\n".join(lines) + ")")


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
    system = SYSTEM + (f"\n\nWHO YOU ARE\n{ctx.persona}\nAnswer as this expert, in their voice, "
                       "from these cards first." if ctx.persona else "")
    msgs: list[dict] = [{"role": "system", "content": system},
                        {"role": "user", "content": parts},
                        {"role": "assistant", "content": READY}]
    for turn in history:
        if turn.get("role") in ("user", "assistant") and str(turn.get("text", "")).strip():
            msgs.append({"role": turn["role"], "content": str(turn["text"])})
    # Explain more asks for a fuller answer; said with the question, not in
    # the system prompt, so the start of the prompt - and its cache - stay put
    msgs.append({"role": "user", "content": question.strip()
                 + (f"\n\n({EXPLAIN})" if ctx.mode == "explain" else "")
                 + weighing(ctx.reading) + dates_note(ctx, question)})
    return msgs


def check_fits(ctx: Context, question: str, history: list[dict], think: bool) -> int:
    """Refuse up front rather than let the model silently drop the start."""
    used = (ctx.tokens + estimate(SYSTEM) + estimate(question) + estimate(weighing(ctx.reading))
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

NEXT_ANYCASE = re.compile(r"^[ \t*_>#-]*next:[ \t*_]*(.*?)[ \t*_]*$", re.MULTILINE | re.IGNORECASE)


def split_nexts(answer: str) -> tuple[str, list[str]]:
    """The answer, and the questions it offers next - the "NEXT:" lines that
    end it (DESIGN.md 46: three). Only lines that end the answer count, so a
    "NEXT:" the model happens to write mid-answer stays where it is."""
    questions: list[str] = []
    end = len(answer)
    # a bare "NEXT:" with the questions on the lines under it
    bare = [m for m in NEXT_LINE.finditer(answer) if not m.group(1).strip()]
    if bare:
        m = bare[-1]
        under = [re.sub(r"^[ \t]*(?:[-*•]|\d+[.)])?[ \t]*", "", ln).strip().strip('"“”').strip()
                 for ln in answer[m.end():].splitlines() if ln.strip()]
        if 0 < len(under) <= 4 and all(not NEXT_LINE.match(ln) for ln in under):
            return answer[:m.start()].rstrip(), [CITE.sub("", q).strip() for q in under][:3]
    for m in reversed(list(NEXT_LINE.finditer(answer))):
        if answer[m.end():end].strip():
            break
        q = re.sub(r"[ \t]+([.,;:?!])", r"\1", CITE.sub("", m.group(1))).strip().strip('"“”').strip()
        if q:
            questions.insert(0, q)
        end = m.start()
    if not questions:
        # "Next: ..." in any case counts only as a block of two or more at the end -
        # a single "Next:" line is just a word in the answer
        block = []
        for m in reversed(list(NEXT_ANYCASE.finditer(answer))):
            if answer[m.end():end].strip():
                break
            block.insert(0, m)
            end = m.start()
        if len(block) >= 2:
            questions = [CITE.sub("", m.group(1)).strip().strip('"“”').strip() for m in block]
        else:
            end = len(answer)
    rest = answer[:end].rstrip()
    # a "Beyond your board:" with nothing after it is left out
    rest = re.sub(r"\n*[ \t*_#]*Beyond your board:[ \t*_]*$", "", rest).rstrip()
    return rest, [q for q in questions if q][:3]


def split_next(answer: str) -> tuple[str, str]:
    """The answer, and the first question it offers next."""
    answer, questions = split_nexts(answer)
    return answer, questions[0] if questions else ""


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
    answer = LONE_ID.sub(r"[[\1]]", BEYOND_MARK.sub(BEYOND, MD_IMAGE.sub("", answer)))
    answer = re.sub(r"\n{3,}", "\n\n", answer)
    clean = CITE.sub(keep, answer)
    clean = re.sub(r"[ \t]+([.,;:])", r"\1", clean)
    board, _, beyond = clean.partition(BEYOND)
    return {"answer": clean.strip(), "board": board.strip(), "beyond": beyond.strip(),
            "cited": cited, "dropped": dropped}


class LoopGuard:
    """Notices thinking that repeats itself (DESIGN.md 56): the same line - a
    real line, not "Yes." - a third time."""

    REPEATS = 3
    LINE = 14          # characters a line needs to count

    def __init__(self):
        self.seen: dict[str, int] = {}
        self.buf = ""

    def feed(self, piece: str) -> bool:
        self.buf += piece
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            key = re.sub(r"[^a-z0-9 ]", "", line.lower())
            key = " ".join(key.split())
            if len(key) >= self.LINE:
                self.seen[key] = self.seen.get(key, 0) + 1
                if self.seen[key] >= self.REPEATS:
                    return True
        return False


def answer_events(model, ctx: Context, question: str, history: list[dict],
                  think: bool = False, hold: bool = True) -> Iterator[dict]:
    """Everything the person sees, in order: what went in, the thinking, the
    words as they come, and finally the checked answer. hold=False inside a
    run that already holds the model - an expert's plan and steps (47)."""
    used = check_fits(ctx, question, history, think)
    yield {"event": "context", "cards": ctx.cards, "pictures": len(ctx.pictures), "tokens": used,
           "whole": ctx.whole, "pictures_left_out": ctx.pictures_left_out,
           "mode": ctx.mode, "total": ctx.total, "how": ctx.how, "where": ctx.where,
           "reading": ctx.reading, "offer": ctx.offer}
    text: list[str] = []
    timings: dict = {}
    looped = False
    loop = LoopGuard()
    asked = messages(ctx, question, history)
    stream = model.stream(asked, max_tokens=ANSWER_TOKENS + (THINKING_TOKENS if think else 0),
                          think=think, hold=hold)
    for ev in stream:
        if ev["type"] == "progress":
            yield {"event": "progress", "total": ev["total"], "cached": ev["cached"],
                   "done": ev["done"]}
        elif ev["type"] == "text":
            text.append(ev["text"])
            yield {"event": "text", "text": ev["text"]}
        elif ev["type"] == "thinking":
            yield {"event": "thinking", "text": ev["text"]}
            if loop.feed(ev["text"]):
                looped = True
                break
        elif ev["type"] == "done":
            timings = ev
    if looped:
        # DESIGN.md 56: thinking that goes round in circles never ends on its
        # own - hang up on it, and answer without thinking
        stream.close()
        yield {"event": "thinking", "text": "\n\n(The thinking went round in circles - answering without it.)"}
        for ev in model.stream(asked, max_tokens=ANSWER_TOKENS, think=False, hold=hold):
            if ev["type"] == "text":
                text.append(ev["text"])
                yield {"event": "text", "text": ev["text"]}
            elif ev["type"] == "done":
                timings = ev
    answer, questions = split_nexts("".join(text))
    next_question = questions[0] if questions else ""
    result = check_citations(answer, set(ctx.cards))
    yield {"event": "done", **result, "next": next_question, "nexts": questions,
           "stopped": bool(timings.get("stopped")),
           "tokens_per_second": timings.get("tokens_per_second")}


# ------------------------------------------------------- fix the writing --
#
# DESIGN.md 56: a small AI button on a card fixes its spelling, grammar and
# punctuation - the person's words, corrected, not rewritten - and the card is
# changed in one undoable step.

PROOFREAD = """You correct spelling, grammar and punctuation in a note from a person's mind map.

Reply with JSON only, no other text: {"title": "...", "text": "..."}

- Fix only real mistakes: misspellings, wrong words, grammar, punctuation,
  capital letters. Keep the person's wording, tone, language and meaning.
- Do not add, remove, shorten or explain anything. Do not translate.
- Keep line breaks, list markers ("- ", "1. "), links, emails, numbers, names
  and technical terms exactly as they are.
- If something is already right, return it unchanged."""

PROOF_CHARS = 6000


def proofread(model, title: str, text: str) -> dict:
    """The card's title and text with their mistakes fixed - or as they were,
    if the model's reply cannot be used. `changed` says whether anything did."""
    title, text = title or "", text or ""
    if not (title.strip() or text.strip()):
        return {"title": title, "text": text, "changed": False}
    try:
        reply = model.chat([{"role": "system", "content": PROOFREAD},
                            {"role": "user", "content": json.dumps({"title": title, "text": text[:PROOF_CHARS]},
                                                                   ensure_ascii=False)}],
                           max_tokens=min(4000, 200 + len(text) // 2), think=False, temperature=0.1)["text"]
        got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except Exception:
        return {"title": title, "text": text, "changed": False}
    fixed_title = str(got.get("title", title))
    fixed_text = str(got.get("text", text)) + text[PROOF_CHARS:]
    # an answer that is no longer the same note is not a correction
    if (title.strip() and not fixed_title.strip()) or (text.strip() and len(fixed_text) < len(text) * 0.6):
        return {"title": title, "text": text, "changed": False}
    return {"title": fixed_title, "text": fixed_text,
            "changed": fixed_title != title or fixed_text != text}


# ------------------------------------------------------------ to a card --

DRAFT_KINDS = ("relates", "supports", "example-of", "part-of", "causes", "contradicts")
DRAFT_SYSTEM = """You add one new card to a person's mind map, from a conversation about it.

Reply with JSON only, no other text:
{"title": "...", "text": "...", "link_to": "...", "kind": "...", "label": "...", "why": "..."}

The card holds what the LAST ANSWER adds that the CARDS do not already say:
new knowledge - what the answer explained, added from general knowledge, or
put together. Never repeat what a card already holds, and never write about
the conversation itself.

- title: a short name for that new idea, at most 8 words.
- text: the new knowledge in 1-4 plain sentences, with no citations and no
  "Beyond your board".
- link_to: the id of the ONE card below this new knowledge belongs with most -
  the card it explains, extends, gives an example of or argues with. The
  cards are the ones this conversation found; read their words, not only
  their titles. "" only if none of them is related.
- kind: how the new card relates to that card: relates, supports, example-of,
  part-of, causes or contradicts.
- label: "" when the kind says it well enough; otherwise a few words naming
  the relation more exactly, read as "new card <label> that card" - such as
  "is a faster variant of" or "explains why". At most 5 words.
- why: one short sentence on why it belongs with that card."""

DRAFT_CARDS = 40          # cards from the conversation the drafter may link to
DRAFT_EXCERPT = 160       # characters of each card's words it reads
LABEL_WORDS = 5


def draft_card(model, nodes: dict[str, dict], question: str, answer: str,
               cited: list[str], candidates: Optional[list[str]] = None,
               history: Optional[list[dict]] = None) -> dict:
    """A card made from an answer (DESIGN 34, 53): the NEW knowledge it adds,
    the card from this conversation it belongs with, and how they relate - a
    kind, and a label of its own where the kind is too coarse - with the
    reason. Nothing is written here: the interface places it, then asks the
    person whether it is in the right place.

    `cited`: the cards the answer drew on; `candidates`: every card the
    conversation brought up - an Explain more answer cites none, so without
    them it had nothing to link to; `history`: the conversation before it."""
    cards = [i for i in dict.fromkeys([*cited, *(candidates or [])]) if i in nodes][:DRAFT_CARDS]

    def words(n: dict) -> str:
        c = n.get("content") or {}
        said = " ".join(str(c.get(k) or "") for k in ("text", "summary", "excerpt")).split()
        return " ".join(said)[:DRAFT_EXCERPT]

    marked = set(cited)
    listing = "\n".join(f"{i}{' *' if i in marked else ''}: {nodes[i].get('title') or 'Untitled'}"
                         + (f" - {words(nodes[i])}" if words(nodes[i]) else "") for i in cards) or "(none)"
    clean = CITE.sub("", BEYOND_MARK.sub(BEYOND, answer)).replace(BEYOND, "\nFrom general knowledge:")
    convo = "\n".join(f"{t.get('role')}: {CITE.sub('', str(t.get('text', '')))[:400].strip()}"
                       for t in (history or [])[-6:] if str(t.get("text", "")).strip())
    fallback = {"title": question.strip()[:70], "text": clean.strip()[:500],
                "link_to": cards[0] if cards else "", "kind": "relates", "label": "", "why": ""}
    try:
        reply = model.chat([{"role": "system", "content": DRAFT_SYSTEM},
                            {"role": "user", "content":
                             (f"CONVERSATION BEFORE\n{convo}\n\n" if convo else "")
                             + f"LAST QUESTION\n{question.strip()}\n\nLAST ANSWER\n{clean.strip()[:6000]}"
                             f"\n\nCARDS (* = the last answer drew on it)\n{listing}"}],
                           max_tokens=500, think=False, temperature=0.3)["text"]
        got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except Exception:
        return fallback                      # a model that strays still gives a usable card
    title = str(got.get("title") or "").strip()[:80] or fallback["title"]
    text = CITE.sub("", str(got.get("text") or "")).strip()[:800] or fallback["text"]
    link = str(got.get("link_to") or "").strip()
    kind = str(got.get("kind") or "").strip()
    label = " ".join(str(got.get("label") or "").split()[:LABEL_WORDS]).strip(" .")[:40]
    link = link if link in cards else (cards[0] if cards else "")
    return {"title": title, "text": text, "link_to": link,
            "kind": kind if kind in DRAFT_KINDS else "relates",
            "label": label if link else "",
            "why": str(got.get("why") or "").strip()[:200] if link else ""}


# ------------------------------------------------------------ pictures --

# DESIGN.md 51: the description now writes out the picture's text, so a word
# that appears only in a corner of a slide or one cell of a table is found.
DESCRIBE = ("Describe this picture for a search index. First, in one or two plain sentences: "
            "what it shows and what kind of picture it is (photo, diagram, screenshot, chart, "
            "table, slide). Then write \"Text:\" and every word, number and label you can read "
            "in it, in reading order, exactly as written - headings, labels, table cells, axis "
            "names, captions, code. Leave out the Text part if there is no text. No preamble.")
DESCRIBE_TOKENS = 600
DESCRIBE_CHARS = 3000        # kept for search; the prompt reads less (SEEN_CHARS)
SEEN_CHARS = 700             # of a description, read in place of a picture left out


def seen_line(text: str) -> str:
    """A picture's description, under its card, where the picture itself did
    not fit - shortened, as a long one would crowd out other cards."""
    return f"\n    picture shows: {text[:SEEN_CHARS]}" if text else ""


def describe_picture(model, dd: DataDir, conn: sqlite3.Connection, bid: str, sha: str) -> str:
    """What a picture shows, in words, for search (DESIGN.md 38). "" when the
    picture cannot be read."""
    got = _picture(dd, conn, bid, sha)
    if got is None:
        return ""
    reply = model.chat([{"role": "user", "content": [
        {"type": "text", "text": DESCRIBE},
        {"type": "image_url", "image_url": {"url": got[0]}}]}],
        max_tokens=DESCRIBE_TOKENS, think=False, temperature=0.2)
    return " ".join(str(reply.get("text") or "").split())[:DESCRIBE_CHARS]
