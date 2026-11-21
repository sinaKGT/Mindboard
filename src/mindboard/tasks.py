"""Understanding what is asked, then doing it in steps (DESIGN.md 66).

"Summarize this" with a picture used to get a description of the picture:
the question went to the model in one shot, and nothing worked out first
that a summary was asked for. Now every question in the chat - the board's,
Board off, a file's tab - goes through three stages:

1. **Understand** (one short call, JSON): the task - answer, summarize,
   explain, compare, extract, analyze, rewrite, translate, plan; what it is
   about; the output wanted (length, list or table, language); and, only when
   one look is not enough, the steps to get there.
2. **Chain the steps** (ReAct-style): each step is one action from a fixed
   set - read the pasted picture in full, search the board or the file, work
   something out from the earlier steps - run in turn, its result (the
   observation) handed to the next. When the steps are done the model checks
   whether the task is complete, and may add one more step, once.
3. **Write** in the task's own shape - a summary as its gist, key points and
   what it means; a comparison as a table - from what the steps found and
   what was in front of the model already, every fact tied to its source.

A plain question gets no steps and is answered as before, with only the
understanding call added. Anything that fails or takes too long is skipped,
and the answer goes on from what there is - said, never guessed.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

log = logging.getLogger(__name__)

TASKS = ("answer", "summarize", "explain", "compare", "extract", "analyze", "rewrite",
         "translate", "plan", "quiz", "funfact")
# a card's AI menu says what it wants (DESIGN.md 75): no need to read it first
KNOWN = ("summarize", "explain", "quiz", "funfact")
STEPS_MAX = 4
UNDERSTAND_SECONDS = 60.0      # past this, the question is answered as it stands
STEP_SECONDS = 150.0           # one step; past this it is skipped
CHECK_SECONDS = 45.0
OBSERVATION_CHARS = 3500       # what one step hands on
WORK_CHARS = 12000             # all the steps together, in the answer's prompt

SHAPES = {
    "summarize": ("Write a summary: first one line with the gist; then the key points as a short "
                  "list - 3 to 6, keeping the source's own sections as bold headings where it has "
                  "them; then one line on what it means or why it matters. Only what the source "
                  "says - nothing added, nothing left out that matters."),
    "explain": ("Explain it clearly in a few short steps, from what it is to how it works, with "
                "one concrete example."),
    "compare": ("Compare them in a small table: one row for each thing that matters, one column "
                "for each option; then one or two sentences on which fits when."),
    "extract": ("List every item asked for, one per line, exactly as the source gives it, each "
                "with where it comes from; say plainly if some could not be found."),
    "analyze": ("Analyze it: what it claims or shows, how well that holds up, what is missing or "
                "weak, and what follows - short headed parts."),
    "rewrite": "Give the rewritten text only, in the form asked for, then nothing else but the NEXT: lines.",
    "translate": "Give the translation only, faithfully, then nothing else but the NEXT: lines.",
    "plan": ("Lay out a plan: numbered steps in order, each one line with what to do and why; "
             "then the main risk or open point."),
    # DESIGN.md 72: a quiz is a game played over several turns, not a plan for one
    # DESIGN.md 75: a fact that surprises, not the card's first line read back
    "funfact": ("Give ONE fun fact from the cards: the most surprising, delightful or unexpected "
                "detail they hold - a striking number, a contrast, an odd name, a link between two "
                "things - told straight away in one or two playful sentences, with one fitting "
                "emoji, and the card cited. Do not open with \"Fun fact\" or any label - the page "
                "already heads it so (DESIGN.md 78) - nor with \"The most surprising detail is\": "
                "say the fact itself. Work nothing out - no sums of dates or ages, no comparing "
                "with today - only what the cards state. Never just restate a card's opening line or its "
                "definition. Only what the cards say: if they hold nothing that could surprise or "
                "delight, say so instead in one short, funny line ending with an emoji (like 🤷 or "
                "🙈). Nothing else - no lists, no headings."),
    "quiz": ("Play the quiz, in a playful, encouraging voice. If the person has just answered a "
             "question from earlier in this conversation, first say whether they got it right, "
             "explain in one or two sentences from the cards (cited), and give the score so far. "
             "Then ask only the NEXT question - one, never a list, and never its answer - or, "
             "after the last question, give the final score with a cheer. Questions come only "
             "from what the cards say, and each has exactly one right answer by the cards: in "
             "multiple choice, the wrong choices must be plainly wrong by the cards - never "
             "another true fact from them (a card listing three roles gives \"which is NOT "
             "one of his roles?\" or asks for one role with the others left out). Before asking, "
             "find the right answer written in the cards; if it is not there - what a title "
             "stands for, what kind of organisation it is - ask about something else. "
             "The three NEXT: lines are three answers the person can pick for the question "
             "just asked (its choices, when it is multiple choice) - one of them right - never "
             "follow-up questions."),
}

ACTIONS = {
    "read": "read the pasted picture(s) in full - every line of text and what each figure shows",
    "search": "search {where} for a short phrase - names and terms, not the whole question",
    "work_out": "work something out from the earlier steps' results",
}

UNDERSTAND = """You read a person's request before it is answered, and plan how to do it.
Reply with JSON only:
{{"task": "...", "about": "...", "output": "...", "reading": "...",
  "steps": [{{"do": "...", "what": "...", "uses": 0}}]}}

- task: one of answer (a question to answer), summarize, explain, compare, extract
  (list every X), analyze (assess, critique), rewrite, translate, plan, quiz (they
  want to be quizzed - or they are answering a quiz question asked just before).
- about: what it is about, in a few words ("the pasted slide", "this file", "the
  cards about funding", "general knowledge").
- output: the shape and length wanted, from their words - "short", "5 bullet
  points", "a table", "in Persian" - or "" if they did not say.
- reading: what they want, in one plain sentence in the second person ("You want
  ...").
- steps: only when one look is not enough - a long source to summarize, several
  separate things to compare, every X across many places to extract, a plan to
  build. What is already in front of the model (below) was found for this very
  request: when it likely holds the answer - a fact, a date, a name, who, what,
  when, the latest or the first of something - there are no steps: []. Most
  requests need none. At most {steps_max}. Each step is one action:
{actions}
  "what" says what to read, search for or work out, in one short phrase; "uses"
  is the number of the earlier step whose result it needs (1 is the first), or 0.
  The answer itself is written after the steps - never plan a step that writes
  the final answer.

WHAT THERE IS TO WORK WITH
{have}"""

CHECK = """You check whether the work below is enough to do the task, before the answer
is written. Reply with JSON only: {"done": true} - or, when something the task
needs is plainly missing, {"done": false, "step": {"do": "...", "what": "..."}}
with one more step, using only these actions: """


@dataclass
class Tools:
    """What the steps can do here: read pictures pasted with the question,
    search the board or the file - each a function giving an observation,
    and for a board, the cards it found, so the answer may cite them."""
    have: str                                              # what there is to work with, in words
    where: str = ""                                        # "your board", "this file"
    read: Optional[Callable[[str], str]] = None
    search: Optional[Callable[[str], tuple[str, list[str]]]] = None

    def actions(self) -> dict[str, str]:
        out = {}
        if self.read:
            out["read"] = ACTIONS["read"]
        if self.search:
            out["search"] = ACTIONS["search"].format(where=self.where or "the source")
        out["work_out"] = ACTIONS["work_out"]
        return out


def json_reply(model, system: str, prompt: str, max_tokens: int, seconds: float) -> tuple[dict, str]:
    """A JSON object from the model, inside a run that already holds it - and
    what went wrong, if anything. Cut off past `seconds`: the reply is hung up,
    so llama-server stops writing it."""
    reply, deadline = [], time.time() + seconds
    stream = model.stream([{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                          max_tokens=max_tokens, think=False, temperature=0.1, hold=False)
    try:
        for ev in stream:
            if ev["type"] == "text":
                reply.append(ev["text"])
            elif ev["type"] == "done" and ev.get("stopped"):
                return {}, "stopped"
            if time.time() > deadline:
                return {}, "took too long"
    except Exception as exc:
        log.warning("a planning call failed: %s", exc)
        return {}, "the model failed"
    finally:
        stream.close()
    text = "".join(reply)
    try:
        got = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        log.warning("a planning reply could not be read: %r", text[:300])
        return {}, "could not be read"
    return (got, "") if isinstance(got, dict) else ({}, "could not be read")


def _convo(history: Optional[list[dict]]) -> str:
    return "\n".join(f"{t.get('role')}: {str(t.get('text', ''))[:300]}"
                     for t in (history or [])[-4:] if str(t.get("text", "")).strip())


def understand(model, question: str, history: Optional[list[dict]], tools: Tools) -> tuple[dict, str]:
    """The task, read: {task, about, output, reading, steps} - {} and why, if
    the reply cannot be used, and the question is then answered as it stands."""
    actions = tools.actions()
    system = UNDERSTAND.format(steps_max=STEPS_MAX, have=tools.have,
                               actions="\n".join(f'    "{k}": {v}' for k, v in actions.items()))
    convo = _convo(history)
    got, trouble = json_reply(model, system,
                              (f"CONVERSATION SO FAR\n{convo}\n\n" if convo else "") + f"REQUEST\n{question.strip()}",
                              400, UNDERSTAND_SECONDS)
    if not got:
        return {}, trouble
    plan = clean(got, actions)
    if len(plan["steps"]) == 1 and plan["steps"][0]["do"] == "search":
        # one search is the search already made for the question: no steps -
        # it only cost time (and, tried, the answer's citations)
        plan["steps"] = []
    return plan, ""


def clean(got: dict, actions: dict) -> dict:
    """Only what can be done here, in the shapes the rest expects."""
    task = str(got.get("task") or "answer").strip().lower()
    steps = []
    for st in (got.get("steps") or [])[:STEPS_MAX]:
        if not isinstance(st, dict):
            continue
        do = str(st.get("do") or "").strip().lower()
        what = " ".join(str(st.get("what") or "").split())[:160]
        if do not in actions or not what:
            continue
        try:
            uses = int(st.get("uses") or 0)
        except (TypeError, ValueError):
            uses = 0
        steps.append({"do": do, "what": what, "uses": uses if 0 < uses <= len(steps) else 0})
    return {"task": task if task in TASKS else "answer",
            "about": " ".join(str(got.get("about") or "").split())[:120],
            "output": " ".join(str(got.get("output") or "").split())[:120],
            "reading": " ".join(str(got.get("reading") or "").split())[:240],
            "steps": steps}


def _work_out(model, question: str, plan: dict, found: list[dict], step: dict) -> str:
    """One reasoning step over what the earlier steps found."""

    earlier = "\n\n".join(f"STEP {f['i'] + 1} ({f['do']}: {f['what']})\n{f['text']}" for f in found)
    prompt = (f"REQUEST\n{question.strip()}\n\nTASK\n{plan.get('reading') or plan['task']}\n\n"
              + (f"WHAT THE STEPS FOUND SO FAR\n{earlier}\n\n" if earlier else "")
              + f"THIS STEP\n{step['what']}\n\nDo only this step, from what is above - short, exact, "
                "keeping names, numbers and where each thing comes from. If what is above does not "
                "hold it, say so; never guess.")
    out, deadline = [], time.time() + STEP_SECONDS
    stream = model.stream([{"role": "user", "content": prompt}], max_tokens=700, think=False,
                          temperature=0.2, hold=False)
    try:
        for ev in stream:
            if ev["type"] == "text":
                out.append(ev["text"])
            if time.time() > deadline:
                raise TimeoutError("this step took too long")
    finally:
        stream.close()                       # hung up: llama-server stops writing it
    return "".join(out).strip()


def run(model, ctx, question: str, history: list[dict], tools: Tools,
        known: str = "") -> Iterator[dict]:
    """Understand the request and do its steps, as events: "task" with the
    plan, then "step" for each as it runs and ends. What the steps found and
    the shape the task asks for are left on `ctx.task_note`, for the answer;
    cards a board search found join `ctx.cards`, so they can be cited.

    `known`: a card action's task (DESIGN.md 75) - taken as it is, without the
    model reading the request first: no wait, no misreading ("LIST" for a fun
    fact), and the answer shaped for it at once."""
    if known in KNOWN:
        plan = {"task": known, "about": "", "output": "", "reading": "", "steps": []}
        yield {"event": "task", **{k: plan[k] for k in ("task", "about", "output", "reading")},
               "steps": [], "trouble": ""}
        ctx.task_note = task_note(plan, [])
        if known == "funfact":
            ctx.no_dates = True
        return
    plan, trouble = understand(model, question, history, tools)
    if model.cancelled():
        return
    if not plan:
        # not understood in time, or not readable: answered as it stands, as
        # before this existed - nothing is lost but the shaping
        if trouble and trouble != "stopped":
            log.info("the request was not understood (%s); answered as it stands", trouble)
        return
    yield {"event": "task", **{k: plan[k] for k in ("task", "about", "output", "reading")},
           "steps": [{"do": s["do"], "what": s["what"]} for s in plan["steps"]], "trouble": ""}
    found: list[dict] = []
    steps = list(plan["steps"])
    extra_done = False
    i = 0
    while i < len(steps):
        if model.cancelled():
            return
        step = steps[i]
        yield {"event": "step", "i": i, "status": "running"}
        t0 = time.time()
        text, status = "", "done"
        try:
            if step["do"] == "read" and tools.read:
                text = tools.read(step["what"])
            elif step["do"] == "search" and tools.search:
                query = step["what"]
                if step["uses"] and step["uses"] <= len(found):
                    query += " " + found[step["uses"] - 1]["text"][:120]
                text, cards = tools.search(query)
                for c in cards:
                    if c not in ctx.cards:
                        ctx.cards.append(c)
                text = text or "(nothing found for this)"
            else:
                text = _work_out(model, question, plan, found, step)
        except Exception as exc:                            # skipped, and said
            log.warning("step %d (%s) failed: %s", i + 1, step["do"], exc)
            text, status = "", "failed"
        if time.time() - t0 > STEP_SECONDS and status == "done":
            status = "slow"
        if model.cancelled():
            yield {"event": "step", "i": i, "status": "stopped"}
            return
        found.append({"i": i, "do": step["do"], "what": step["what"], "text": text[:OBSERVATION_CHARS]})
        yield {"event": "step", "i": i, "status": "failed" if status == "failed" else "done",
               "note": _note(text) if status != "failed" else "could not be done"}
        i += 1
        # the steps done: is the task complete? one more step may be added, once
        if i == len(steps) and len(steps) >= 2 and not extra_done:
            extra_done = True
            more = _check(model, question, plan, found, tools)
            if more:
                steps.append(more)
                yield {"event": "task_step", "do": more["do"], "what": more["what"]}
    ctx.task_note = task_note(plan, found)


def _check(model, question: str, plan: dict, found: list[dict], tools: Tools) -> Optional[dict]:
    actions = tools.actions()
    work = "\n\n".join(f"STEP {f['i'] + 1} ({f['do']}: {f['what']})\n{f['text'][:1500]}" for f in found)
    got, _ = json_reply(model, CHECK + ", ".join(actions),
                        f"REQUEST\n{question.strip()}\n\nTASK\n{plan.get('reading') or plan['task']}\n\nWORK\n{work}",
                        150, CHECK_SECONDS)
    if not got or got.get("done") is not False or not isinstance(got.get("step"), dict):
        return None
    st = clean({"steps": [got["step"]]}, actions)["steps"]
    return st[0] if st else None


def _note(text: str) -> str:
    """A step's result in one short line, for the person watching."""
    line = " ".join(text.split())
    return line[:140] + ("…" if len(line) > 140 else "")


def task_note(plan: dict, found: list[dict]) -> str:
    """Said with the question: what the steps found, and how to shape the answer."""
    parts = []
    work = "\n\n".join(f"STEP {f['i'] + 1} - {f['what']}\n{f['text']}" for f in found if f["text"])
    if work:
        parts.append("WORK DONE TOWARD THIS (by the steps; use it - and cite cards, pages or "
                     "\"(calculated)\" as the sources inside it say):\n" + work[:WORK_CHARS])
    shape = SHAPES.get(plan.get("task", ""))
    if plan.get("reading"):
        parts.append(f"You read this as: {plan['reading']}")
    if shape:
        parts.append(shape)
    if plan.get("output"):
        parts.append(f"The person asked for: {plan['output']} - follow that over the shape above.")
    if not parts:
        return ""
    return ("\n\n(" + "\n\n".join(parts) + "\nIf you have nothing to add from your own knowledge, "
            "leave out the Beyond part entirely - never write it with \"None\". Answer every part "
            "of the request, and cite your sources as always. End with the three NEXT: lines.)")
