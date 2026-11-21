"""The calculator for questions about a file (DESIGN.md 82).

Asked "how many visitors did Riverside have in 2025 in total?", the model
added four numbers it had read right and wrote 48,672 (the total is 18,382);
asked for an average pH it wrote 6.54 for 6.64. A small model does sums badly
in its head. So when a question needs arithmetic, the model only chooses:
which operation, and which numbers, each copied from a passage with where it
is. The code checks every number is written on that page of the passages,
works the result out exactly, and hands it to the answer as CALCULATED. A
number that cannot be found is not used: that calculation is NOT CALCULATED,
and the answer is told to say so. Structured, as the sheet tools are (rule 7):
an operation and numbers, never an expression or code from the model.
"""

from __future__ import annotations

import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

log = logging.getLogger("mindboard.calc")

DECIDE_SECONDS = 60.0
CALCS_MAX = 3
VALUES_MAX = 40

# a question that may need working out; anything else is answered as it is,
# without the extra call to the model
NEEDS = re.compile(
    r"\b(total|sum|add(?:ed)? up|altogether|combined|average|mean|median|differen\w*|how much (?:more|less|"
    r"higher|lower|bigger|smaller)|how many (?:more|fewer)|more than|less than|increase\w*|decrease\w*|"
    r"grow\w*|grew|growth|fall|fell|drop\w*|rise|rose|risen|change\w*|percent\w*|per ?cent|ratio|times|"
    r"proportion|share|margin|compared?|versus|vs|over budget|under budget|variance|gap|net)\b|%",
    re.IGNORECASE)

OPS = {
    "sum": "add all the values",
    "difference": "the first value minus the second",
    "percent_change": "the change from the first value to the second, as a percentage of the first",
    "percent_of": "the first value as a percentage of the second",
    "ratio": "the first value divided by the second (how many times)",
    "average": "the mean of all the values",
    "product": "multiply all the values",
    "min": "the smallest value",
    "max": "the largest value",
}
TWO = {"difference", "percent_change", "percent_of", "ratio"}

DECIDE = """You prepare the arithmetic a question about a document needs, for a calculator.
You do no arithmetic yourself. Reply with JSON only:
{"calculations": [{"label": "...", "op": "...", "values": [{"value": "...", "where": "...", "what": "..."}]}]}

- calculations: one for each figure the question needs worked out - a total,
  a difference, an average, a percentage, a ratio. At most 3. None ([]) when
  the answer is a number written in the passages as it is.
- label: what the result is, in a few words ("Riverside visitors, 2025 total").
- op: one of
{ops}
- values: the numbers, in the order the operation needs, each copied exactly as
  the passage writes it ("3,965", "48.3", "(1,458)"); "where" is the label in
  square brackets at the start of its passage ("p. 3"); "what" says which number it is (row and column: "Riverside,
  Q1"). Take each number from the right row and column - read the table's
  header for the columns. Never make a number up; if one the question needs is
  not in the passages, leave that calculation out."""

# a number standing on its own: not the 1 of "Q1", nor the 01 of "S-01"
NUM = re.compile(r"(?<![\w.\-])\(?[-−]?[£$€]?\d[\d,]*(?:\.\d+)?\)?")


def needs(question: str) -> bool:
    return bool(NEEDS.search(question or ""))


def number(text: str) -> Optional[Decimal]:
    """A number as a document writes it: "3,965", "£1.2", "(1,458)" (a bracket
    is negative), "12.5%". None when it is not one."""
    s = str(text).strip().replace("−", "-").replace(",", "").replace(" ", "")
    s = re.sub(r"^[£$€]|%$|[£$€]$", "", s)
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").lstrip("£$€")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return -d if neg else d


def _digits(text: str) -> str:
    """A written number's digits and point, for finding it in a page."""
    return re.sub(r"[^\d.]", "", text.replace(",", ""))


def written_on(value: str, where: str, passages: dict[str, str]) -> bool:
    """Is this number written on that page (or passage) - as a whole number,
    not part of a longer one?"""
    want = _digits(value)
    if not want:
        return False
    text = passages.get(where.strip().lower(), "")
    for m in NUM.finditer(text):
        if _digits(m.group(0)) == want:
            return True
    return False


def find_page(value: str, where: str, passages: dict[str, str]) -> Optional[str]:
    """Where this number is written in the passages: the page cited when it is
    there - or, as the model often names a table rather than a page ("Table
    1"), the one page that has it. None when no passage has it, or several do
    and none was cited: then it cannot be told which was meant."""
    cited = where.strip().lower()
    for key in passages:
        if key == cited or (key and re.search(rf"(?<![\w.]){re.escape(key)}(?!\d)", cited)):
            if written_on(value, key, passages):
                return key
    found = [key for key in passages if written_on(value, key, passages)]
    return found[0] if len(found) == 1 else None


def _places(values: list[str]) -> int:
    return max((len(v.split(".")[1]) if "." in v else 0) for v in values) if values else 0


def _say(d: Decimal, places: int) -> str:
    q = Decimal(1).scaleb(-places)
    out = d.quantize(q, rounding=ROUND_HALF_UP)
    s = f"{out:,.{places}f}"
    return s


def work_out(op: str, nums: list[Decimal]) -> Decimal:
    if op == "sum":
        return sum(nums, Decimal(0))
    if op == "average":
        return sum(nums, Decimal(0)) / len(nums)
    if op == "product":
        out = Decimal(1)
        for n in nums:
            out *= n
        return out
    if op == "min":
        return min(nums)
    if op == "max":
        return max(nums)
    a, b = nums
    if op == "difference":
        return a - b
    if b == 0:
        raise ZeroDivisionError("division by zero")
    if op == "percent_change":
        return (b - a) / a * 100 if a else _zero()
    if op == "percent_of":
        return a / b * 100
    return a / b                                   # ratio


def _zero():
    raise ZeroDivisionError("a change from zero has no percentage")


def run(calc: dict, passages: dict[str, str]) -> dict:
    """One calculation, checked and worked out: {tool, label, said, error, values}."""
    label = " ".join(str(calc.get("label") or "a calculation").split())[:120]
    op = str(calc.get("op") or "").strip().lower()
    base = {"tool": "calculate", "label": label, "op": op, "values": []}
    if op not in OPS:
        return {**base, "said": f"{label}: no such calculation ({op or 'none'})", "error": "unknown operation"}
    vals = [v for v in (calc.get("values") or []) if isinstance(v, dict)][:VALUES_MAX]
    if (op in TWO and len(vals) != 2) or not vals:
        return {**base, "said": f"{label}: the numbers it needs were not all found", "error": "wrong number of values"}
    nums, shown, missing = [], [], []
    for v in vals:
        raw = " ".join(str(v.get("value") or "").split())[:40]
        where = " ".join(str(v.get("where") or "").split())[:40]
        what = " ".join(str(v.get("what") or "").split())[:80]
        n = number(raw)
        page = find_page(raw, where, passages) if n is not None else None
        if page is None:
            missing.append(f"{raw or '?'} ({what or 'unnamed'}, {where or 'no page'})")
            continue
        nums.append(n)
        shown.append({"value": raw, "where": page, "what": what})
    base["values"] = shown
    if missing:
        return {**base, "said": f"{label}: not found in the passages -{'; '.join(missing)}",
                "error": "a number was not found where it was said to be"}
    try:
        result = work_out(op, nums)
    except (ZeroDivisionError, InvalidOperation) as exc:
        return {**base, "said": f"{label}: {exc}", "error": str(exc)}
    places = _places([s["value"] for s in shown])
    if op in ("percent_change", "percent_of"):
        text = _say(result, 1) + "%"
    elif op in ("ratio", "average"):
        text = _say(result, max(places, 2))
    else:
        text = _say(result, places)
    sources = ", ".join(f"{s['value']} ({s['what']}, {s['where']})" for s in shown)
    return {**base, "result": text,
            "said": f"{label} = {text} - {OPS[op]}, from {sources}", "error": ""}


def page_texts(chunks) -> dict[str, str]:
    """The passages the model was given, by their label ("p. 3"): where a
    number is looked for."""
    out: dict[str, str] = {}
    for c in chunks:
        key = c.where.strip().lower()
        out[key] = (out.get(key, "") + "\n" + c.text).strip()
    return out


def decide(model, question: str, history, passages_text: str) -> tuple[list[dict], str]:
    """The calculations the model asks for - often none - and what went wrong
    choosing, if anything. A slow or unreadable reply is no choice: the answer
    goes on, told that nothing was calculated."""
    from .tasks import _convo, json_reply

    convo = _convo(history)
    system = DECIDE.replace("{ops}", "\n".join(f'    "{k}": {v}' for k, v in OPS.items()))
    prompt = (f"{passages_text}\n\n" + (f"CONVERSATION SO FAR\n{convo}\n\n" if convo else "")
              + f"QUESTION\n{question.strip()}")
    got, trouble = json_reply(model, system, prompt, 700, DECIDE_SECONDS)
    if trouble:
        return [], ("" if trouble == "stopped" else f"working out the figures: {trouble}")
    calcs = [c for c in (got.get("calculations") or []) if isinstance(c, dict)]
    return calcs[:CALCS_MAX], ""


def calculated_text(results: list[dict], trouble: str = "") -> str:
    """The calculator's results, as the answer reads them before the passages."""
    ok = [r for r in results if not r.get("error")]
    failed = [r for r in results if r.get("error")]
    out = ""
    if ok:
        out += ("CALCULATED by the calculator from numbers in the passages (exact - use these and "
                "say \"(calculated)\" after each):\n" + "\n".join(f"- {r['said']}" for r in ok) + "\n\n")
    if failed or trouble:
        out += ("NOT CALCULATED: " + "; ".join([r["said"] for r in failed] + ([trouble] if trouble else []))
                + ". Do not work these out yourself - say they could not be calculated.\n\n")
    return out
