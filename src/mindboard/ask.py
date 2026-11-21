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
   Write a formula in LaTeX between dollar signs, like $E = mc^2$.
6. Finish with three lines, each starting exactly "{NEXT}", each holding one
   question the person could ask next: short, in their words, about what they
   just asked and the cards in front of you - one going deeper, one putting it
   to use, one on a neighbouring idea from the cards."""


# Board off (DESIGN.md 63): the person asked for the model alone - no search,
# no cards, and instructions that do not talk of a board, so nothing pulls the
# answer towards their notes. The conversation and pasted pictures still count.
OWN_SYSTEM = f"""You are a clear, careful assistant. The person has switched their board
of notes off for this question: answer from your own knowledge, and do not
mention cards, notes or a board.

Start with the answer itself. Short and exact by default: a few sentences or a
short list. Explain at length - steps, examples - only when they ask you to
explain, or ask how or why. Say plainly when you are unsure or something may
be out of date. Write a formula in LaTeX between dollar signs, like $E = mc^2$.

Finish with three lines, each starting exactly "{NEXT}", each holding one
question the person could ask next: short, in their words - one going deeper,
one putting it to use, one on a neighbouring idea."""


# Explain more (DESIGN.md 86): the model's own knowledge and the conversation,
# nothing from the board. With the conversation's cards still sent (DESIGN.md
# 34, 80), the answer opened by restating them, cited, and put the explanation
# asked for under "Beyond your board"; the owner wants the board out of it.
EXPLAIN_SYSTEM = f"""You are a clear, careful teacher. The person has asked you to explain more:
answer from your own knowledge, building on the conversation so far. Do not
mention cards, notes or a board, and cite nothing.

Start with the explanation itself. Explain step by step, in plain words, and
give what they ask for - examples, an analogy, more detail; one or two concrete
examples if they name nothing. Say plainly when you are unsure or something may
be out of date, and do not make up names, places or figures. Write a formula in
LaTeX between dollar signs, like $E = mc^2$.

Finish with three lines, each starting exactly "{NEXT}", each holding one
question the person could ask next: short, in their words - one going deeper,
one putting it to use, one on a neighbouring idea."""

FILE_SYSTEM = f"""You answer questions about one document, from PASSAGES found in it for
each question - its best-matching parts, not the whole document. A page given
as a picture is that page itself: read its figures, tables and formulas from
the picture.

How to answer:
1. Answer from the passages first. After each thing you take from them, say
   where it is, in brackets as the passage is labelled: (p. 12), (Sales, rows
   2-21), (table 2). Never invent a page. Read a table's row and column
   headers before taking a number from it. Figures under CALCULATED were
   worked out with tools - over a whole sheet, or by the calculator from
   numbers in the passages: use them for totals, counts, averages,
   differences, percentages and rankings, and say "(calculated)" after
   each. Do no arithmetic yourself: a figure the question needs that is not
   under CALCULATED was not worked out - say so, never estimate it.
2. If the passages do not answer the question, say so plainly first: the
   answer may be in a part of the document not found this time - suggest
   other words to ask with. The passages are only part of the document:
   never present a total, count, average or "all of them" worked out from
   passages as if it covered the whole document, and never fill a gap with
   a guess. Say what is known and from where.
3. Anything you add from your own knowledge goes after a line that reads
   exactly "{BEYOND}" - meaning beyond this document.
4. Start with the answer itself. Short and exact by default: a few sentences
   or a short list. Explain at length only when asked to explain, or asked how
   or why. Write a formula in LaTeX between dollar signs, like $E = mc^2$.
5. Finish with three lines, each starting exactly "{NEXT}", each holding one
   question the person could ask next about this document: short, in their
   words - one going deeper, one putting it to use, one on a neighbouring
   part of the document."""


def own_context(attached: Optional[list[Picture]] = None) -> Context:
    """What a Board off question takes: nothing from the board."""
    return Context([], "", mode="own", attached=list(attached or []))


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
  these, how they differ), "design" (how to build something), "summarize",
  "extract" (list every X), "analyze" (assess, critique), "rewrite",
  "translate" or "plan" (the steps to do something).
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
KINDS = ("fact", "explain", "advice", "compare", "design", "summarize", "extract", "analyze",
         "rewrite", "translate", "plan")
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


def _task_shapes() -> None:
    # the chat's task shapes (tasks.py) for an expert's plan too, so both
    # answer "summarize" the same way
    from .tasks import SHAPES
    for kind in ("summarize", "extract", "analyze", "rewrite", "translate", "plan"):
        LENGTH[kind] = SHAPES[kind] + " Then the three NEXT: lines."


_task_shapes()


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
    # pictures pasted into the question itself - not on the board (DESIGN.md 62)
    attached: list[Picture] = field(default_factory=list)
    # asking one file (DESIGN.md 64): where each passage sent comes from
    passages: list = field(default_factory=list)
    # what the task's steps found and the shape the task asks for (tasks.py,
    # DESIGN.md 66) - said with the question, so the cached start stays put
    task_note: str = ""
    # leave out today's date (DESIGN.md 78): a fun fact compared the cards'
    # dates with it and called a past visit "from the future"
    no_dates: bool = False

    @property
    def tokens(self) -> int:
        return (estimate(self.text) + sum(p.tokens for p in self.pictures)
                + sum(p.tokens for p in self.attached))


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


# ---------------------------------------------------- pictures pasted in --
#
# DESIGN.md 62: a picture pasted into the chat box comes with the question. It
# is shrunk and re-encoded like a card's picture - whatever was pasted, the
# model gets a plain JPEG - described in a few lines so the board can be
# searched for what it shows, and then seen by the model with the question.

PASTED_MAX = 4                 # pictures with one question
PASTED_BYTES = 15_000_000      # a pasted picture's size, before shrinking
PASTED_TOKENS = 220            # a description to search by: a few lines
PASTED_PROMPT = ("Say in two or three plain sentences what this picture shows - what kind of "
                 "picture it is, its subject, and the main words, names, numbers or formulas in "
                 "it (formulas in LaTeX between dollar signs). No preamble.")


def take_pasted(urls: list[str]) -> list[Picture]:
    """Pasted pictures (data: URLs) as the model takes them. AskError for
    anything that is not a picture."""
    from PIL import Image

    got: list[Picture] = []
    for i, url in enumerate(urls[:PASTED_MAX]):
        head, _, data = str(url).partition(",")
        if not head.startswith("data:image/") or ";base64" not in head:
            raise AskError("a pasted picture did not come through - paste it again")
        try:
            raw = base64.b64decode(data, validate=False)
            if len(raw) > PASTED_BYTES:
                raise AskError("that picture is too large - paste a smaller one")
            with Image.open(io.BytesIO(raw)) as im:
                im = im.convert("RGB")
                im.thumbnail((MAX_SIDE, MAX_SIDE))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=88)
                w, h = im.size
        except AskError:
            raise
        except Exception:
            raise AskError("a pasted picture could not be read - try a PNG or JPEG")
        got.append(Picture(f"pasted {i + 1}",
                           "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
                           (w * h) // (32 * 32) + 16))
    return got


def describe_pasted(model, pic: Picture) -> str:
    """A few lines on what a pasted picture shows, to search the board by.
    The caller holds the model."""
    reply = model.chat([{"role": "user", "content": [
        {"type": "text", "text": PASTED_PROMPT},
        {"type": "image_url", "image_url": {"url": pic.data_url}}]}],
        max_tokens=PASTED_TOKENS, think=False, temperature=0.2)
    return " ".join(str(reply.get("text") or "").split())[:1200]


def with_pasted(question: str, said: list[str]) -> str:
    """The question as it is searched and answered: what was pasted, in words.
    Kept in the conversation too, so a follow-up still knows the picture."""
    said = [d for d in said if d]
    if not said:
        return question
    if len(said) == 1:
        return f"{question}\n\n(The person pasted a picture with this question. It shows: {said[0]})"
    return (f"{question}\n\n(The person pasted {len(said)} pictures with this question. "
            + " ".join(f"Picture {i + 1} shows: {d}" for i, d in enumerate(said)) + ")")


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
    more) nothing from the board goes in at all (DESIGN.md 86)."""
    graph = read_graph(conn, bid)
    nodes = {n["id"]: n for n in graph["nodes"]}
    if explain:
        # DESIGN.md 86: nothing from the board - the conversation and the
        # model's own knowledge, whatever cards are chosen
        return Context([], "", mode="explain", total=len(nodes))
    whole = not chosen
    kept = [i for i in dict.fromkeys(keep or []) if i in nodes]
    if whole:
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

    if getattr(ctx, "no_dates", False):
        return ""

    today = today or dt.date.today()
    note = f"\n\n(Today is {dt.date.strftime(today, '%A')} {say_date(today)}.)"
    if not TIME_WORDS.search(question) or ctx.mode in ("own", "file"):
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
    if ctx.mode in ("own", "explain"):
        msgs = [{"role": "system", "content": OWN_SYSTEM if ctx.mode == "own" else EXPLAIN_SYSTEM}]
        return _conversation(msgs, ctx, question, history)
    if ctx.mode == "file":
        # one file's passages (DESIGN.md 64), and its pages better seen than read
        parts: list[dict[str, Any]] = [{"type": "text", "text": ctx.text}]
        for p in ctx.pictures:
            parts.append({"type": "text", "text": f"{p.card} of the file, as a picture:"})
            parts.append({"type": "image_url", "image_url": {"url": p.data_url}})
        msgs = [{"role": "system", "content": FILE_SYSTEM}, {"role": "user", "content": parts},
                {"role": "assistant", "content": "I have read these passages. Ask away."}]
        return _conversation(msgs, ctx, question, history)
    parts: list[dict[str, Any]] = [{"type": "text", "text": ctx.text}]
    for p in ctx.pictures:
        parts.append({"type": "text", "text": f"PICTURE on card [{p.card}]:"})
        parts.append({"type": "image_url", "image_url": {"url": p.data_url}})
    system = SYSTEM + (f"\n\nWHO YOU ARE\n{ctx.persona}\nAnswer as this expert, in their voice, "
                       "from these cards first." if ctx.persona else "")
    msgs: list[dict] = [{"role": "system", "content": system},
                        {"role": "user", "content": parts},
                        {"role": "assistant", "content": READY}]
    return _conversation(msgs, ctx, question, history)


def _conversation(msgs: list[dict], ctx: Context, question: str, history: list[dict]) -> list[dict]:
    """The conversation after the cards (or after nothing, Board off), and the
    newest question last, with any pictures pasted into it."""
    for turn in history:
        if turn.get("role") in ("user", "assistant") and str(turn.get("text", "")).strip():
            said = str(turn["text"])
            if ctx.mode == "explain":
                # earlier answers' card citations would pull the board back in (DESIGN.md 86)
                said = _uncited(said)
            msgs.append({"role": turn["role"], "content": said})
    asked = question.strip() + weighing(ctx.reading) + ctx.task_note + dates_note(ctx, question)
    if ctx.attached:
        # pasted pictures go with the newest question, after the cards, so the
        # start of the prompt - and what llama-server has cached - stays put
        head = PASTED_HEAD if ctx.mode not in ("own", "explain") else PASTED_ALONE
        pasted: list[dict[str, Any]] = [{"type": "text", "text": head}]
        for p in ctx.attached:
            pasted.append({"type": "image_url", "image_url": {"url": p.data_url}})
        msgs.append({"role": "user", "content": pasted + [{"type": "text", "text": asked}]})
    else:
        msgs.append({"role": "user", "content": asked})
    return msgs


PASTED_HEAD = ("PASTED with this question (not on the board): the picture below. Answer about it, "
               "and bring in the cards wherever they bear on it - cite them as usual.")
# with no board in the answer (Board off, Explain more), nothing to bring in
PASTED_ALONE = "PASTED with this question: the picture below. Answer about it."


def _uncited(text: str) -> str:
    """Text with its card citations taken out, and its page citations - an
    Explain more in a file conversation (DESIGN.md 89) - with the space before
    each, and nothing else touched: a nested list keeps its indentation."""
    text = re.sub(r"[ \t]*\[\[[0-9A-Za-z]{26}\]\]", "", text)
    return re.sub(r"[ \t]*\((?:p|pp)\.\s?\d[\d\s,;.\-–p]*\)", "", text).strip()


def check_fits(ctx: Context, question: str, history: list[dict], think: bool) -> int:
    """Refuse up front rather than let the model silently drop the start."""
    used = (ctx.tokens + estimate(SYSTEM) + estimate(question) + estimate(weighing(ctx.reading))
            + estimate(ctx.task_note)
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


CHOICE = re.compile(r"^[ \t]*[*_]*\(?([A-Da-d])[).:][*_]*[ \t]+(.+?)[ \t]*$")
CHOICES_TAIL = 400           # the choices must sit near the end, after the question


def choices(answer: str) -> list[str]:
    """The lettered choices a quiz question ends with - "A) ...", "B) ..." -
    to offer as buttons when the model wrote no NEXT lines (DESIGN.md 72: told
    to stop and wait for an answer, it never did). Only the last run of two to
    four lines, close to the end; anything else gives none."""
    lines = answer.rstrip().splitlines()
    run: list[str] = []
    for i in range(len(lines) - 1, -1, -1):
        m = CHOICE.match(lines[i])
        if m:
            run.insert(0, f"{m.group(1).upper()}) {m.group(2).strip('*_ ').strip()}")
        elif run:
            break
        elif lines[i].strip() and len("\n".join(lines[i:])) > CHOICES_TAIL:
            return []
    letters = [c[0] for c in run]
    return run if 2 <= len(run) <= 4 and letters == list("ABCD"[:len(run)]) else []


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
    board, mark, beyond = answer.partition(BEYOND)
    # DESIGN.md 80: what comes after the line is the model's own knowledge -
    # a card cited there would pass it off as the person's; and a Beyond part
    # that is only "None" is no Beyond part
    beyond = CITE.sub("", beyond)
    if re.fullmatch(r"\s*(?:none|n/a|nothing)\.?\s*", beyond, re.IGNORECASE):
        mark = beyond = ""
    clean = CITE.sub(keep, board + mark + beyond)
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
           "reading": ctx.reading, "offer": ctx.offer, "passages": ctx.passages}
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
    picks = [] if questions else choices(answer)     # a quiz's question: its choices to tap
    next_question = questions[0] if questions else ""
    if ctx.mode == "explain":
        answer = _uncited(answer)                       # nothing from the board: DESIGN.md 86
    result = check_citations(answer, set(ctx.cards))
    # DESIGN.md 88: an answer from the board that cites no card is said to be
    # so under it - it reads as the person's notes but nothing shows it is
    uncited = bool(ctx.mode in ("search", "chosen") and ctx.cards and result["board"]
                   and not result["cited"])
    yield {"event": "done", **result, "uncited": uncited, "next": next_question, "nexts": questions or picks,
           "choices": bool(picks),
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


# ------------------------------------------------------------- beautify --
# DESIGN.md 78: a card's text laid out as Markdown - the same words, a better shape

BEAUTIFY_SYSTEM = """You organise the text of one card of a person's mind map.

The card's text is given as numbered PIECES, in order. You do not write any
text - you only say how to group the pieces, so not one word changes.

Reply with JSON only, no other text:
{"blocks": [{"kind": "...", "pieces": [1, 2]}]}

- kind: "heading" (one short piece that names the part after it), "bullets"
  (separate items, as a list), "numbered" (steps or ranks, in order) or
  "text" (sentences that belong together, as a paragraph).
- Use every piece exactly once, in the order given (1, 2, 3 ...).
- Lists for items, a paragraph for flowing sentences; a heading only when
  the text has clear parts. If it is fine as it is, one "text" block."""

BEAUTIFY_CHARS = 6000
BEAUTIFY_PIECES = 80
LABEL = re.compile(r"^([A-Za-z][\w /&()'-]{0,40}?):\s+(\S.*)$")
LIST_MARK = re.compile(r"^\s*(?:[-*+•]|\d+[.)]|\[[ xX]\])\s+")


def _words(text: str) -> list[str]:
    return re.findall(r"[0-9A-Za-zÀ-￿]+", text.lower())


def _semicolon_parts(body: str) -> list[str]:
    """`body` split at its semicolons - not those inside brackets."""
    parts, depth, cur = [], 0, []
    for ch in body:
        depth += (ch in "([{") - (ch in ")]}")
        if ch == ";" and depth <= 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [x.strip() for x in parts if x.strip()]


def _cut(text: str) -> tuple[list[str], list[str]]:
    """The card's pieces (`beautify_pieces`), and what joined each one to the
    next in the card - "; ", " ", or a new line - so pieces a plan keeps
    together are joined as they were, not run into each other."""
    pieces: list[str] = []
    joins: list[str] = []
    for line in text.splitlines():
        line = LIST_MARK.sub("", line.strip()).strip()
        line = re.sub(r"^#{1,6}\s+", "", line).strip()
        if not line:
            continue
        label = LABEL.match(line)
        body = label.group(2) if label else line
        parts = _semicolon_parts(body)
        if len(parts) >= 2 and all(len(x) < 160 for x in parts):
            # "Role: A; B; C" - the label stays a piece of its own, a heading or a lead
            if label:
                pieces.append(label.group(1) + ":"); joins.append(" ")
            for x in parts:
                pieces.append(x); joins.append("; ")
        elif len(line) > 220:
            for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", line):
                if x.strip():
                    pieces.append(x.strip()); joins.append(" ")
        else:
            pieces.append(line); joins.append(" ")
        if joins:
            joins[-1] = "\n"                    # the end of a line of the card
    return pieces, joins


def beautify_pieces(text: str) -> list[str]:
    """The card's text cut into the pieces Beautify may arrange (DESIGN.md 79):
    its lines, without any list marks they had; a line of items joined by
    semicolons (outside brackets), its items; a long paragraph, its sentences.
    Every word stays, in order - only the joins between pieces go."""
    return _cut(text)[0]


def _piece_md(piece: str) -> str:
    """A "Label: value" piece with its label in bold; anything else as it is."""
    m = LABEL.match(piece)
    if m and len(m.group(1).split()) <= 4:
        return f"**{m.group(1)}:** {m.group(2)}"
    if piece.endswith(":") and len(piece.split()) <= 4:      # a label alone, leading a list
        return f"**{piece}**"
    return piece


def beautify(model, title: str, text: str) -> dict:
    """The card's text organised - lists, numbered steps, short headings,
    bold labels - from its own pieces, so no word is changed, added or
    lost: the model only groups them (DESIGN.md 79). {"text", "changed"};
    as it was when the plan cannot be used. Writes nothing."""
    text = text or ""
    if not text.strip():
        return {"text": text, "changed": False}
    head, rest = text[:BEAUTIFY_CHARS], text[BEAUTIFY_CHARS:]
    pieces, joins = _cut(head)
    # the pieces hold exactly the card's words, in order - else leave it be
    if not pieces or len(pieces) > BEAUTIFY_PIECES or _words(" ".join(pieces)) != _words(head):
        return {"text": text, "changed": False}
    listing = "\n".join(f"{i}. {x}" for i, x in enumerate(pieces, 1))
    try:
        reply = model.chat([{"role": "system", "content": BEAUTIFY_SYSTEM},
                            {"role": "user", "content": f"TITLE\n{(title or '').strip()}\n\nPIECES\n{listing}"}],
                           max_tokens=600, think=False, temperature=0.1, timeout=BREAK_SECONDS)["text"]
        got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except Exception:
        return {"text": text, "changed": False}
    blocks = got.get("blocks") if isinstance(got, dict) else None
    if not isinstance(blocks, list):
        return {"text": text, "changed": False}
    used: list[int] = []
    out: list[str] = []
    for blk in blocks:
        if not isinstance(blk, dict):
            return {"text": text, "changed": False}
        kind = str(blk.get("kind") or "text").strip().lower()
        try:
            idx = [int(i) for i in (blk.get("pieces") or [])]
        except (TypeError, ValueError):
            return {"text": text, "changed": False}
        if not idx or kind not in ("heading", "bullets", "numbered", "text"):
            return {"text": text, "changed": False}
        used += idx
        parts = [pieces[i - 1] for i in idx if 1 <= i <= len(pieces)]
        if len(parts) != len(idx):
            return {"text": text, "changed": False}
        if kind == "heading":
            joined = " ".join(parts)
            # a heading names a part: short, and not a "Label: value" line - a
            # whole "Role: Control room, ..." line came back as a heading
            if joined.endswith(":") or LABEL.match(joined) or len(joined.split()) > 8:
                out.append(_piece_md(joined))
            else:
                out.append("### " + joined)
        elif kind == "bullets":
            out.append("\n".join(f"- {_piece_md(x)}" for x in parts))
        elif kind == "numbered":
            out.append("\n".join(f"{n}. {_piece_md(x)}" for n, x in enumerate(parts, 1)))
        else:
            # kept together: joined as they were in the card - "; " stays "; "
            said = ""
            for k, i in enumerate(idx):
                starts = k == 0 or joins[idx[k - 1] - 1] == "\n"   # at the start of a line
                said += _piece_md(pieces[i - 1]) if starts else pieces[i - 1]
                if k < len(idx) - 1:
                    said += joins[i - 1].replace("\n", " ") if joins[i - 1] != "\n" else "  \n"
            out.append(said)
    # every piece exactly once, in order - or nothing is changed
    if used != list(range(1, len(pieces) + 1)):
        log.info("a beautify plan skipped, repeated or moved pieces; left as it was")
        return {"text": text, "changed": False}
    laid = "\n\n".join(out) + rest
    return {"text": laid, "changed": laid.strip() != text.strip()}


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
    title = plain_title(got.get("title"))[:80] or fallback["title"]
    text = CITE.sub("", str(got.get("text") or "")).strip()[:800] or fallback["text"]
    link = str(got.get("link_to") or "").strip()
    kind = str(got.get("kind") or "").strip()
    label = " ".join(str(got.get("label") or "").split()[:LABEL_WORDS]).strip(" .")[:40]
    link = link if link in cards else (cards[0] if cards else "")
    return {"title": title, "text": text, "link_to": link,
            "kind": kind if kind in DRAFT_KINDS else "relates",
            "label": label if link else "",
            "why": str(got.get("why") or "").strip()[:200] if link else ""}


# ------------------------------------------- one card: break it, relate it --
# DESIGN.md 72: the card's AI menu. Breaking a card drafts smaller cards from
# its own words, for the person to accept; finding related cards searches the
# board with the card's words. Neither writes anything.

BREAK_SYSTEM = """You split one card of a person's mind map into smaller cards, one idea each.

Reply with JSON only, no other text:
{"cards": [{"title": "...", "text": "..."}]}

- Use only what the card says. Add no facts, examples or opinions of your own.
- 2 to 6 cards, in the card's own order; each a separate point it makes.
- title: a short name for the point, at most 8 words.
- text: the point in 1-3 plain sentences, in the card's own words where possible.
- If the card holds only one idea, reply {"cards": []}."""

BREAK_MIN, BREAK_MAX = 2, 6
BREAK_CHARS = 6000          # of a card's words the model reads
BREAK_SECONDS = 90.0        # one reply; past this it is hung up (CLAUDE.md rule 5)


def _points(text: str) -> list[str]:
    """A card's own points without the model: its list items, else its
    paragraphs, else its lines - the fallback when the model's reply cannot be
    used, or says one idea of a card that plainly holds several lines."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    items = [re.sub(r"^([-*+]|\d+[.)]|\[[ xX]\])\s+", "", ln) for ln in lines
             if re.match(r"^([-*+]|\d+[.)])\s+", ln)]
    if len(items) >= BREAK_MIN:
        return items
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) >= BREAK_MIN:
        return paras
    return lines if len(lines) >= BREAK_MIN else []


def break_card(model, title: str, text: str) -> dict:
    """Smaller cards drafted from one card's own words, and what went wrong if
    none could be: {"cards": [{"title", "text"}], "note": ""}."""
    title, text = (title or "").strip(), (text or "").strip()
    if not text:
        return {"cards": [], "note": "This card has no text to break up."}
    got: dict = {}
    try:
        reply = model.chat([{"role": "system", "content": BREAK_SYSTEM},
                            {"role": "user", "content": f"TITLE\n{title}\n\nTEXT\n{text[:BREAK_CHARS]}"}],
                           max_tokens=900, think=False, temperature=0.2, timeout=BREAK_SECONDS)["text"]
        got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except Exception:
        got = {}
    out = []
    for c in (got.get("cards") if isinstance(got, dict) else None) or []:
        if not isinstance(c, dict):
            continue
        t = " ".join(plain_title(c.get("title")).split()[:10]).strip()[:80]
        x = CITE.sub("", _plain(c.get("text"))).strip()[:600]
        if t or x:
            out.append({"title": t or x[:60], "text": x})
    out = out[:BREAK_MAX]
    if len(out) >= BREAK_MIN:
        return {"cards": out, "note": ""}
    # the model said one idea, strayed or failed: the card's own list or paragraphs
    points = _points(text)[:BREAK_MAX]
    if points:
        cards = []
        for p in points:
            labelled = _label_line(p)           # "**Label:** what it says": the label names it
            if labelled and labelled[1]:
                cards.append({"title": labelled[0][:60], "text": labelled[1][:600]})
            else:
                cards.append({"title": " ".join(plain_title(p).split()[:6])[:60], "text": p[:600]})
        return {"cards": cards, "note": ""}
    return {"cards": [], "note": "This card holds one idea - nothing to break up."}


MERGE_SYSTEM = """You merge several cards of a person's mind map into one card.

Reply with JSON only, no other text:
{"title": "...", "text": "..."}

- Use only what the cards say. Add no facts, examples or opinions of your own.
- Keep every distinct point the cards make; say a point they share once.
- title: a short name for the merged card, at most 8 words.
- text: the merged points in plain sentences, or a short "- " list when the
  cards list separate things. In the cards' own words where possible."""

MERGE_MAX = 8               # cards merged at once
MERGE_CHARS = 2500          # of each card's words the model reads


def merge_cards(model, cards: list[dict]) -> dict:
    """One card drafted from several cards' own words: {"title", "text"}.
    Writes nothing. A reply that cannot be used gives the cards side by side."""
    cards = [c for c in cards if (c.get("title") or "").strip() or (c.get("text") or "").strip()][:MERGE_MAX]
    joined = "\n\n".join(t for t in ((c.get("text") or "").strip() for c in cards) if t)
    fallback = {"title": " / ".join((c.get("title") or "").strip() for c in cards if (c.get("title") or "").strip())[:80]
                or "Merged card",
                "text": joined[:4000]}
    if len(cards) < 2:
        return fallback
    listing = "\n\n".join(f"CARD {i}\nTITLE: {(c.get('title') or '').strip()}\nTEXT: {(c.get('text') or '').strip()[:MERGE_CHARS]}"
                          for i, c in enumerate(cards, 1))
    try:
        reply = model.chat([{"role": "system", "content": MERGE_SYSTEM}, {"role": "user", "content": listing}],
                           max_tokens=900, think=False, temperature=0.2, timeout=BREAK_SECONDS)["text"]
        got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except Exception:
        return fallback
    if not isinstance(got, dict):
        return fallback
    title = " ".join(plain_title(got.get("title")).split()[:10]).strip()[:80]
    text = CITE.sub("", _plain(got.get("text"))).strip()[:4000]
    # a merge that lost most of the words is not a merge
    if not text or len(text) < min(len(joined), 400) * 0.3:
        return fallback
    return {"title": title or fallback["title"], "text": text}


QUIZ_SYSTEM = """You write ONE quiz question about a person's cards, as structured data.

Reply with JSON only, no other text:
{"question": "...", "choices": ["...", "...", "...", "..."], "answer": 0, "why": "..."}

- question: one short question whose answer is written in the cards. Never what
  a title stands for or anything the cards do not say.
- choices: 3 or 4 short options, without letters. Exactly one is right by the
  cards; the others are plainly wrong by the cards - never another true fact
  from them.
- answer: the position of the right choice, counting from 0.
- why: one short sentence, from the cards, saying why it is right.
- Never repeat a question already asked (listed below, if any)."""

QUIZ_CHOICES = (3, 4)
QUIZ_TRIES = 2


# DESIGN.md 84: a card is titled in plain words. Breaking a Beautified card
# (§78), whose lines open with bold labels, made cards titled
# "**Definition of Analogy:**" - the asterisks shown on the card and in every
# chip that names it. The page draws such titles the same way (text.ts).
_TITLE_LEAD = re.compile(r"^\s*(?:#{1,6}\s+|(?:[-*+•]|\d{1,3}[.)])\s+)")
_TITLE_MARKS = [(re.compile(r"\*\*(.+?)\*\*"), r"\1"), (re.compile(r"__(.+?)__"), r"\1"),
                (re.compile(r"`([^`]+)`"), r"\1"),
                (re.compile(r"(^|[\s(])\*([^*\s](?:[^*]*[^*\s])?)\*(?=$|[\s).,:;!?])"), r"\1\2"),
                (re.compile(r"(^|[\s(])_([^_\s](?:[^_]*[^_\s])?)_(?=$|[\s).,:;!?])"), r"\1\2")]


def plain_title(value) -> str:
    """A title as words: no heading or list marker, no bold, italics or code
    marks, and - when such marks were taken off - no trailing colon, which
    belonged to a "**Label:**" rather than to the title."""
    raw = " ".join(_plain(value).split())
    out = _TITLE_LEAD.sub("", raw)
    for pattern, keep in _TITLE_MARKS:
        out = pattern.sub(keep, out)
    out = out.strip("*_ ").strip()
    if out != raw:
        out = re.sub(r"\s*:$", "", out)
    return out or raw


def _label_line(line: str) -> Optional[tuple[str, str]]:
    """A line that opens with a bold label - "**Definition:** The theory..."
    as Beautify writes them: the label, and what follows it."""
    m = re.match(r"^\s*(?:[-*+]\s+)?\*\*(.+?)\*\*\s*:?\s*(.*)$", line)
    if not m or not m.group(1).strip(" :"):
        return None
    return re.sub(r"\s*:$", "", plain_title(m.group(1))), m.group(2).strip()


def _plain(value) -> str:
    """A field the model may send as a list of lines: joined, not shown as
    Python's list - "['first.', 'second.']" reached a card (DESIGN.md 77)."""
    if isinstance(value, (list, tuple)):
        return "\n".join(f"- {_plain(v)}" if len(value) > 1 else _plain(v) for v in value if _plain(v))
    return str(value or "").strip()


def quiz_question(model, cards: list[dict], asked: Optional[list[str]] = None, rng=None) -> dict:
    """One quiz question about these cards, as data - {question, choices,
    answer, why} - so the page shows the choices as buttons and marks the
    answer itself (DESIGN.md 77); or {note} when none could be made. The
    choices are shuffled: the model puts the right one first."""
    import random
    rng = rng or random.Random()
    listing = "\n\n".join(f"CARD {i}\nTITLE: {(c.get('title') or '').strip()}\nTEXT: {(c.get('text') or '').strip()[:MERGE_CHARS]}"
                          for i, c in enumerate(cards[:MERGE_MAX], 1))
    if not any((c.get("text") or c.get("title") or "").strip() for c in cards):
        return {"note": "These cards hold nothing to ask about yet."}
    before = "\n".join(f"- {q}" for q in (asked or [])[-10:])
    for _ in range(QUIZ_TRIES):
        try:
            reply = model.chat([{"role": "system", "content": QUIZ_SYSTEM},
                                {"role": "user", "content": listing
                                 + (f"\n\nALREADY ASKED\n{before}" if before else "")}],
                               max_tokens=400, think=False, temperature=0.5, timeout=BREAK_SECONDS)["text"]
            got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
        except Exception:
            continue
        if not isinstance(got, dict):
            continue
        question = CITE.sub("", _plain(got.get("question")))[:300].strip()
        raw = got.get("choices") if isinstance(got.get("choices"), list) else []
        choices = [re.sub(r"^\(?[A-Da-d][).:]\s+", "", CITE.sub("", _plain(c))).strip()[:160] for c in raw]
        try:
            answer = int(got.get("answer"))
        except (TypeError, ValueError):
            continue
        if (not question or not QUIZ_CHOICES[0] <= len(choices) <= QUIZ_CHOICES[1]
                or not all(choices) or len(set(c.lower() for c in choices)) != len(choices)
                or not 0 <= answer < len(choices)):
            continue
        right = choices[answer]
        rng.shuffle(choices)
        return {"question": question, "choices": choices, "answer": choices.index(right),
                "why": CITE.sub("", _plain(got.get("why")))[:300].strip()}
    return {"note": "Could not make a question from these cards - try again."}


RELATED_LIMIT = 6
RELATED_QUERY = 1500        # characters of the card's words searched with
HOLDS = 2                   # a card this many others are part of is a container, not a neighbour


def related_cards(dd: DataDir, conn: sqlite3.Connection, bid: str, nid: str,
                  embedder: Optional[retrieve.Embedder] = None, embed_model: str = "",
                  reranker: Optional[retrieve.Reranker] = None,
                  limit: int = RELATED_LIMIT) -> list[dict]:
    """Cards on the board that belong with this one, by the board's own search
    run on the card's words - leaving out the card, the cards it is already
    linked to, and the groups it sits in or holds."""
    graph = read_graph(conn, bid)
    nodes = {n["id"]: n for n in graph["nodes"]}
    me = nodes.get(nid)
    if me is None:
        raise AskError("that card is not on this board any more")
    c = me.get("content") or {}
    query = " ".join([me.get("title") or "", str(c.get("text") or c.get("summary") or "")]).strip()
    if not query:
        return []
    skip = {nid}
    for e in graph["edges"]:
        if e["source"] == nid:
            skip.add(e["target"])
        elif e["target"] == nid:
            skip.add(e["source"])
    up = me.get("parent")
    while up:                                  # the groups it sits in
        skip.add(up)
        up = (nodes.get(up) or {}).get("parent")
    skip.update(expand(nodes, [nid]))          # what it holds, if it is a group
    # groups, and cards that hold others - the board's root, "External
    # visitors", an organisation its people are part of - match every card's
    # words and are structure, not what "related" means. Counting links did
    # not find them: the root had 2, a meeting card with content 7 (DESIGN.md 72)
    held: dict[str, int] = {}
    for e in graph["edges"]:
        if e.get("kind") == "part-of":
            held[e["target"]] = held.get(e["target"], 0) + 1
    skip.update(i for i, n in nodes.items() if n.get("type") == "meta" or held.get(i, 0) >= HOLDS)
    index = board_index(dd, conn, bid, nodes, embedder, embed_model, background=True)
    scored, _ = retrieve.score(index, query[:RELATED_QUERY], nodes, graph["edges"], embedder, reranker)
    out = []
    for i in retrieve.ranked(scored):
        if i in skip or i not in nodes or scored[i] <= 0:
            continue
        n = nodes[i]
        words = " ".join(str((n.get("content") or {}).get(k) or "")
                         for k in ("text", "summary", "excerpt", "alt")).split()
        out.append({"id": i, "type": n.get("type"), "title": n.get("title") or "Untitled",
                    "snippet": " ".join(words)[:140]})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------ pictures --

# DESIGN.md 51: the description now writes out the picture's text, so a word
# that appears only in a corner of a slide or one cell of a table is found.
DESCRIBE = ("Describe this picture for a search index. First, in one or two plain sentences: "
            "what it shows and what kind of picture it is (photo, diagram, screenshot, chart, "
            "table, slide). Then write \"Text:\" and every word, number and label you can read "
            "in it, in reading order, exactly as written - headings, labels, table cells, axis "
            "names, captions, code. Write any mathematical formula in LaTeX between single "
            "dollar signs, like $P(Y \\mid X)$. Leave out the Text part if there is no text. "
            "No preamble.")
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


# ---------------------------------------------------- an answer as a table --
# DESIGN.md 85: "Make a table" under an answer. The model lays the answer's
# own words out as rows and columns, as data; the code checks every cell
# against the answer, keeps only the answer's own citations, and writes the
# markdown table itself. No search, no cards: nothing can come in that the
# answer did not say.

TABLE_SYSTEM = """You lay out an answer as a table, as structured data.

Reply with JSON only, no other text:
{"columns": ["...", "..."], "rows": [["...", "..."]]}

- Use only what the answer says, in its own words. Add nothing, work nothing
  out, and do not total, count or average anything.
- One row for each thing the answer describes; one column for each kind of
  detail it gives about them. The first column names the thing.
- 2 to 6 columns. Column names: a few plain words each.
- Copy citations exactly as written - [[...]] or (p. 12) - into the cell whose
  fact they support, most often the first cell of the row.
- A detail the answer does not give for a row: leave its cell "".
- Each row is a different thing the answer describes. Never make rows of
  what the answer says is missing or not covered.
- If the answer describes only one thing, or nothing that fits rows, reply
  {"columns": [], "rows": []}."""

TABLE_CHARS = 8000          # of the answer the model reads
TABLE_SECONDS = 90.0        # one reply; past this it is hung up (CLAUDE.md rule 5)
TABLE_COLUMNS = (2, 6)
TABLE_ROWS = 40
TABLE_CELL = 200            # characters a cell keeps
TABLE_WORDS_KEPT = 0.6      # share of a cell's words the answer must have
TABLE_DISTINCT = 0.6        # share of rows whose first cells must differ
PAGE_CITE = re.compile(r"\((?:p|pp)\.\s?\d{1,4}(?:\s?[-–]\s?\d{1,4})?\)")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[^\W\d_]{3,}")
NO_ROWS = "This answer doesn't have rows to compare."
NOT_MADE = "Couldn't make a table this time - try again."


def _stem(word: str) -> str:
    w = word.lower()
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def _cell_holds(cell: str, numbers: set[str], words: set[str]) -> bool:
    """Whether a cell says only what the answer says: every number written
    in it, and most of its words - a cell may shorten ("Nottingham City
    Council" to "Nottingham Council"), but not bring in a name of its own."""
    said = PAGE_CITE.sub(" ", CITE.sub(" ", cell))
    if not _numbers(said) <= numbers:
        return False
    mine = [_stem(w) for w in _WORD.findall(said)]
    return not mine or sum(w in words for w in mine) / len(mine) >= TABLE_WORDS_KEPT


def _cell(value) -> str:
    return " ".join(_plain(value).replace("|", "/").split())[:TABLE_CELL]


def answer_table(model, answer: str) -> dict:
    """An answer's own words laid out as a table: {"table": markdown,
    "rows", "blanked", "note"}. "table" is "" and "note" says why when the
    answer has no rows to compare or the reply could not be used. Writes
    nothing."""
    text = NEXT_LINE.sub("", BEYOND_MARK.sub(BEYOND, answer or "")).strip()
    if not text:
        return {"table": "", "rows": 0, "blanked": 0, "note": NO_ROWS}
    cites = set(CITE.findall(text))
    pages = {" ".join(p.split()) for p in PAGE_CITE.findall(text)}
    plain = PAGE_CITE.sub(" ", CITE.sub(" ", text))
    numbers, words = _numbers(plain), {_stem(w) for w in _WORD.findall(plain)}
    try:
        reply = model.chat([{"role": "system", "content": TABLE_SYSTEM},
                            {"role": "user", "content": f"ANSWER\n{text[:TABLE_CHARS]}"}],
                           max_tokens=2000, think=False, temperature=0.1, timeout=TABLE_SECONDS)["text"]
        got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except Exception as exc:
        log.warning("a table could not be made: %s", exc)
        return {"table": "", "rows": 0, "blanked": 0, "note": NOT_MADE}
    if not isinstance(got, dict):
        return {"table": "", "rows": 0, "blanked": 0, "note": NOT_MADE}
    columns = [_cell(c) for c in (got.get("columns") or []) if isinstance(c, (str, int, float))]
    columns = [c for c in columns if c][:TABLE_COLUMNS[1]]
    if len(columns) < TABLE_COLUMNS[0]:
        return {"table": "", "rows": 0, "blanked": 0, "note": NO_ROWS}

    def keep_cites(cell: str) -> str:
        # only the answer's own sources: a citation it did not make is dropped
        cell = CITE.sub(lambda m: m.group(0) if m.group(1) in cites else "", cell)
        cell = PAGE_CITE.sub(lambda m: m.group(0) if " ".join(m.group(0).split()) in pages else "", cell)
        return " ".join(cell.split())

    rows, blanked = [], 0
    for row in (got.get("rows") or [])[:TABLE_ROWS]:
        if not isinstance(row, list):
            continue
        cells = [keep_cites(_cell(c)) for c in row[:len(columns)]]
        cells += [""] * (len(columns) - len(cells))
        for i, c in enumerate(cells):
            if c and not _cell_holds(c, numbers, words):
                cells[i], blanked = "", blanked + 1
        if any(PAGE_CITE.sub("", CITE.sub("", c)).strip() for c in cells):
            rows.append(cells)
    # a column of citations alone (the real model made one, "Citation") goes
    # into the row's first cell, beside what it names
    for i in range(len(columns) - 1, 0, -1):
        if len(columns) > TABLE_COLUMNS[0] and all(not PAGE_CITE.sub("", CITE.sub("", r[i])).strip() for r in rows):
            for r in rows:
                r[0] = " ".join(f"{r[0]} {r[i]}".split())
                del r[i]
            del columns[i]
    # rows must name different things: asked what something is, the real
    # model tabled what the cards lack - "Cards | Do not contain ..." four times
    named = [PAGE_CITE.sub("", CITE.sub("", r[0])).strip().lower() for r in rows]
    if len(rows) < 2 or len({n for n in named if n}) < max(2, len(rows) * TABLE_DISTINCT):
        return {"table": "", "rows": 0, "blanked": blanked, "note": NO_ROWS}
    # a source of the answer that no cell carries is still shown, under the table
    shown = " ".join(" ".join(r) for r in rows)
    lost = [i for i in dict.fromkeys(CITE.findall(text)) if f"[[{i}]]" not in shown]
    lost += [p for p in dict.fromkeys(" ".join(p.split()) for p in PAGE_CITE.findall(text)) if p not in shown]
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(c or "—" for c in r) + " |" for r in rows]
    table = "\n".join(lines)
    if lost:
        table += "\n\nAlso from: " + " ".join(c if c.startswith("(") else "[[" + c + "]]" for c in lost)
    return {"table": table, "rows": len(rows), "blanked": blanked, "note": ""}


SUMMARY_SYSTEM = """You summarise an answer in a few short sentences.

- Use only what the answer says, in its own words. Add nothing, work nothing
  out, and do not total, count or average anything.
- 2 to 5 short sentences; or, when the answer is a list, up to 5 points, each
  on its own line starting "- ". Keep the main points; leave out examples,
  asides and repetition.
- Keep each citation exactly as written - [[...]] or (p. 12) - after the
  point it supports.
- Plain text: no headings, no tables, no bold. Reply with the summary only."""

SUMMARY_CHARS = 8000        # of each part the model reads
SUMMARY_SECONDS = 60.0      # one reply; past this it is hung up (CLAUDE.md rule 5)
SUMMARY_FROM = 400          # a part shorter than this is kept as it is
SUMMARY_WORDS_KEPT = 0.6    # share of a sentence's words the answer must have
NOT_SUMMARISED = "Couldn't summarise this time - try again."
ALREADY_SHORT = "This answer is already short."
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])|\n+")


def _summary_part(model, text: str) -> tuple[str, int]:
    """One part of an answer summarised: (summary, sentences dropped). A
    sentence that brings in a number, or mostly words, the part never said is
    dropped; a citation the part did not make is taken out (DESIGN.md 87)."""
    cites = set(CITE.findall(text))
    pages = {" ".join(p.split()) for p in PAGE_CITE.findall(text)}
    plain = PAGE_CITE.sub(" ", CITE.sub(" ", text))
    numbers, words = _numbers(plain), {_stem(w) for w in _WORD.findall(plain)}
    reply = model.chat([{"role": "system", "content": SUMMARY_SYSTEM},
                        {"role": "user", "content": f"ANSWER\n{text[:SUMMARY_CHARS]}"}],
                       max_tokens=800, think=False, temperature=0.2, timeout=SUMMARY_SECONDS)["text"]
    reply = NEXT_LINE.sub("", BEYOND_MARK.sub("", reply or "")).replace("**", "").strip()
    lines, dropped = [], 0
    for line in reply.splitlines():
        point = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line)
        body = line[point.end():] if point else line.strip()
        # a heading of its own ("Summary:") is not a point of the answer
        if not body.strip() or body.lstrip().startswith("#") or re.fullmatch(r"\s*summary:?\s*", body, re.I):
            continue
        kept = []
        for s in _SENTENCE.split(body):
            s = CITE.sub(lambda m: m.group(0) if m.group(1) in cites else "", s)
            s = PAGE_CITE.sub(lambda m: m.group(0) if " ".join(m.group(0).split()) in pages else "", s)
            s = re.sub(r"\s+([.,;:!?])", r"\1", " ".join(s.split()))
            if not PAGE_CITE.sub("", CITE.sub("", s)).strip():
                if s and kept:          # "... best. [[id]]": the citation stays with it
                    kept[-1] += " " + s
                continue
            if _cell_holds(s, numbers, words):
                kept.append(s)
            else:
                dropped += 1
        if kept:
            lines.append(("- " if point else "") + " ".join(kept))
    return "\n".join(lines), dropped


def answer_summary(model, answer: str) -> dict:
    """An answer's own words summarised, each part apart - what the board
    says, and what is beyond it - so the two stay told apart: {"board",
    "beyond", "dropped", "note"}. A part too short to summarise comes back as
    it is; "note" says why when nothing could be made. Writes nothing."""
    text = NEXT_LINE.sub("", BEYOND_MARK.sub(BEYOND, answer or "")).strip()
    at = text.find(BEYOND)
    parts = {"board": (text[:at] if at >= 0 else text).strip(),
             "beyond": text[at + len(BEYOND):].strip() if at >= 0 else ""}
    if all(len(p) < SUMMARY_FROM for p in parts.values()):
        return {"board": "", "beyond": "", "dropped": 0, "note": ALREADY_SHORT}
    got, dropped = {}, 0
    for name, part in parts.items():
        if len(part) < SUMMARY_FROM:
            got[name] = part
            continue
        try:
            got[name], lost = _summary_part(model, part)
        except Exception as exc:
            log.warning("an answer could not be summarised: %s", exc)
            return {"board": "", "beyond": "", "dropped": 0, "note": NOT_SUMMARISED}
        dropped += lost
        if not got[name]:
            return {"board": "", "beyond": "", "dropped": dropped, "note": NOT_SUMMARISED}
    return {"board": got["board"], "beyond": got["beyond"], "dropped": dropped, "note": ""}
