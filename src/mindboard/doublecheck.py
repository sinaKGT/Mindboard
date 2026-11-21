"""Double check: the numbers in an answer about a file, looked up again
(DESIGN.md 82).

Asking a file, a number now and then came from the wrong row or column of a
table - S-02's phosphorus answered with S-01's, Car parks in October with
another row's figure. Double check is a separate step the person asks for,
under one answer. For each number in it:

1. Where it should be: the pages the sentence cites, else the pages the
   answer cites, else the pages a search for the question finds.
2. A fresh, narrow look at each of those pages: its text with the table rows
   kept, and - for a PDF - the page as a sharper picture. The model is asked
   only to copy, for each claim, the number the page gives for that row and
   column. It does not judge the answer.
3. The code judges. The copied number must be written on that page; then it
   either matches the answer's (checked), or replaces it (corrected). A copy
   that is not on the page proves nothing: the answer's number stays, marked
   not checked.
4. A number that is on no page - a total, a difference, a percentage - is
   worked out again by the calculator from the page's own numbers, never by
   the model.

Every number ends up checked, corrected or said to be unchecked: none is shown
as fact without one of the three.
"""

from __future__ import annotations

import base64
import logging
import re
from decimal import Decimal
from typing import Iterator, Optional

from . import calc

log = logging.getLogger("mindboard.doublecheck")

PAGES_MAX = 4
CLAIMS_MAX = 24
PAGE_SECONDS = 120.0
PICTURE_SIDE = 1600        # sharper than the 1,024 an answer gets: digits read better
PAGE_CHARS = 6000

# a number in the answer: digits with their own grouping and point, standing
# on their own (not the 3 of "Q3", the 07 of "S-07")
NUM = re.compile(r"(?<![\w.\-/])\d[\d,]*(?:\.\d+)?(?![\d/]|\.\d)")
CITE = re.compile(r"\(\s*(?:p|pp|page)\.?\s*(\d+)(?:\s*[-–]\s*\d+)?\s*\)", re.IGNORECASE)
WHERE = re.compile(r"\(([^()]{1,60}?)\)")
SKIP_SPANS = re.compile(r"\[\[[^\]]*\]\]|\((?:p|pp|page)\.?\s*\d+[^)]*\)|\$[^$\n]*\$", re.IGNORECASE)

LOOK = """You look numbers up on one page of a document. Each numbered line is a
sentence with one number left out, as ___. Find on this page the number that
belongs there - in a table, the row and the column header the sentence and the
question are about - and copy it exactly as the page writes it.
Reply with JSON only:
{"checks": [{"n": 1, "found": "...", "row": "...", "column": "..."}]}

- found: the number as the page writes it ("3.48", "4,421", "(1,458)"), or ""
  when this page does not give it (a total or a difference worked out from the
  page is not on it: "").
- row, column: where on the page it is, in a few words (row label, column
  header), "" if not in a table.
- Copy; do not work anything out, do not round."""


def claims_in(answer: str, question: str) -> list[dict]:
    """The numbers in an answer worth checking, each with its sentence and the
    pages that sentence cites. Left out: page references, card ids, list
    numbers, formulas, and numbers the question itself names (a year, a
    sample number) - those are what was asked about, not answers."""
    asked = {_norm(m.group(0)) for m in NUM.finditer(question or "")}
    skip = [m.span() for m in SKIP_SPANS.finditer(answer)]
    out: list[dict] = []
    for sent in _sentences(answer):
        start, end = sent
        text = answer[start:end]
        pages = [f"p. {p}" for p in CITE.findall(text)]
        for m in NUM.finditer(text):
            a, b = start + m.start(), start + m.end()
            if any(s <= a < e for s, e in skip):
                continue
            line_start = answer.rfind("\n", 0, a) + 1
            if re.fullmatch(r"\s*", answer[line_start:a]) and answer[b:b + 2] in (". ", ".\n", ") "):
                continue                                    # "1. " - a list's number
            raw = m.group(0).rstrip(",")
            b = a + len(raw)
            if _norm(raw) in asked or calc.number(raw) is None or _label(raw, answer[max(0, a - 14):a]):
                continue
            out.append({"n": len(out) + 1, "text": raw, "start": a, "end": b,
                        "sentence": " ".join(text.split())[:300], "pages": pages,
                        # blind (DESIGN.md 82): shown the answer's number, the look
                        # found that number - December's 763 for June - and
                        # agreed with it; shown a gap, it has to find the value
                        "blank": " ".join((text[:m.start()] + "___" + text[m.start() + len(raw):]).split())[:300]})
            if len(out) >= CLAIMS_MAX:
                return out
    return out


# a number that names something rather than measures it: "Table 4.1", "Band
# D", "Q2", "Sample 3", "2024" - first measured, Double check "corrected" the
# years 2022-2025 of a list into the values beside them, and "Table 2" into 6,840
LABEL_BEFORE = re.compile(r"(?:\b(?:table|figure|fig\.?|section|appendix|chapter|page|pp?\.|step|band|"
                          r"sample|row|rows|column|col\.?|no\.?|number|quarter|item|part|version|year|"
                          r"note|footnote)|\bq|#)\s*$", re.IGNORECASE)


def _label(raw: str, before: str) -> bool:
    if re.fullmatch(r"(?:19|20)\d\d", raw):           # a year
        return True
    if re.fullmatch(r"0+", raw.replace(",", "")):      # the 000 of "GBP 000"
        return True
    return bool(LABEL_BEFORE.search(before))


def on_row(found: str, row: str, text: str) -> bool:
    """Is this number on a line of the page that names the row given? A table
    row is a line now (DESIGN.md 82), so the model's "row" can be checked:
    a number copied from another row is not on that row's line."""
    words = [w for w in re.findall(r"[a-z0-9]+", row.lower().replace("’", "'")) if len(w) >= 2]
    if not words:
        return True                                    # no row named: nothing to hold it to
    for line in text.splitlines():
        have = set(re.findall(r"[a-z0-9]+", line.lower().replace("’", "'")))
        if all(w in have for w in words) and on_page(found, line):
            return True
    return False


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower().replace("’", "'"))


def cell(row: str, column: str, text: str) -> Optional[str]:
    """The number in this row and column of a table on the page, read by the
    code: the column's place in the header line, the row's numbers on its own
    line. Measured: told "Waste, Nov", the model copied 465 - Waste's, but
    another month's - where the cell says 148. None when the table cannot be
    read this way (no such header, or its cells do not line up with the row's
    numbers): then the model's copy is all there is."""
    rw, col = [w for w in _words(row) if len(w) >= 2], _words(column)
    if not rw or not col:
        return None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        have = set(_words(line))
        if not all(w in have for w in rw):
            continue
        label_end = 0
        for w in rw:                                   # the numbers after the row's label
            m = re.search(re.escape(w), line.lower().replace("’", "'"))
            if m:
                label_end = max(label_end, m.end())
        nums = [m.group(0) for m in calc.NUM.finditer(line[label_end:])]
        if not nums:
            continue
        for head in reversed(lines[:i]):
            heads = head.split()
            if len(heads) == len(nums) + 1:
                heads = heads[1:]                      # its first cell names the rows
            if len(heads) != len(nums):
                continue
            names = [" ".join(_words(h)) for h in heads]
            want = " ".join(col)
            if want in names:
                return nums[names.index(want)]
        return None
    return None


def _norm(s: str) -> str:
    return s.replace(",", "").lstrip("0") or "0"


def _sentences(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z*(\[])|\n+", text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def same(a: str, b: str) -> bool:
    """The same number, however written: "3,965" and "3965"; "£148,000" in a
    table of thousands is 148."""
    x, y = calc.number(a), calc.number(b)
    if x is None or y is None:
        return False
    return abs(x) == abs(y) or abs(x) == abs(y) * 1000 or abs(x) * 1000 == abs(y)


def on_page(value: str, text: str) -> bool:
    for m in calc.NUM.finditer(text):
        if same(m.group(0), value):
            return True
    return False


def page_texts(session) -> dict[str, str]:
    """Every page's whole text, by its label - not only the passages an answer
    was given."""
    out: dict[str, str] = {}
    for c in session.index.chunks:
        if c.figure:
            continue
        key = c.where.strip().lower()
        if key in out and c.text[:80] in out[key]:
            continue
        out[key] = (out.get(key, "") + "\n" + c.text).strip()
    return out


def check(model, session, question: str, answer: str, history, find=None) -> Iterator[dict]:
    """The Double check, as events: "check" as it goes, then "checked" with
    the answer as it should read and each number's mark. The caller holds the
    model."""
    from .filechat import render_page
    from .tasks import json_reply

    claims = claims_in(answer, question)
    if not claims:
        yield {"event": "checked", "answer": answer, "marks": [], "summary": "No numbers to check."}
        return
    texts = page_texts(session)
    for c in claims:                     # a page cited that the file does not have is no page
        c["pages"] = [p for p in c["pages"] if p in texts]
    fallback = list(dict.fromkeys(p for c in claims for p in c["pages"]))
    if not fallback and find is not None:
        fallback = [p for p in dict.fromkeys(ch.where.strip().lower() for ch in find(question))
                    if p in texts][:PAGES_MAX]
    for c in claims:
        c["pages"] = (c["pages"] or fallback)[:PAGES_MAX]
    pages = list(dict.fromkeys(p for c in claims for p in c["pages"]))[:PAGES_MAX]
    yield {"event": "check", "stage": "start", "numbers": len(claims), "pages": pages}

    # 2. each page looked at again, for the claims that cite it
    seen: dict[int, list[dict]] = {}
    for i, page in enumerate(pages):
        if model.cancelled():
            return
        mine = [c for c in claims if page in c["pages"]]
        yield {"event": "check", "stage": "page", "page": page, "done": i, "total": len(pages)}
        listing = "\n".join(f'{c["n"]}. {c["blank"]}' for c in mine)
        parts: list[dict] = [{"type": "text", "text": f"PAGE {page}, as text (table rows kept):\n"
                                                      f"{texts[page][:PAGE_CHARS]}"}]
        number = re.match(r"p\.\s*(\d+)$", page)
        if number and session.kind == "pdf":
            try:
                image = render_page(session.path, int(number.group(1)), PICTURE_SIDE)
                parts.append({"type": "text", "text": "The same page, as a picture:"})
                parts.append({"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")}})
            except Exception:
                log.exception("drawing %s of %s to check it", page, session.name)
        parts.append({"type": "text", "text": f"QUESTION ASKED\n{question.strip()}\n\nSENTENCES\n{listing}"})
        got, trouble = json_reply(model, LOOK, parts, 600, PAGE_SECONDS)
        if trouble:
            log.info("checking %s: %s", page, trouble)
            continue
        for row in got.get("checks") or []:
            if not isinstance(row, dict):
                continue
            try:
                n = int(row.get("n"))
            except (TypeError, ValueError):
                continue
            found = " ".join(str(row.get("found") or "").split())[:40]
            where = ", ".join(x for x in (" ".join(str(row.get("row") or "").split())[:60],
                                          " ".join(str(row.get("column") or "").split())[:40]) if x)
            seen.setdefault(n, []).append({"page": page, "found": found, "where": where,
                                           "row": " ".join(str(row.get("row") or "").split())[:60],
                                           "column": " ".join(str(row.get("column") or "").split())[:40]})

    # 3. the code judges what was copied
    marks: list[dict] = []
    derived: list[dict] = []
    for c in claims:
        mark = {"n": c["n"], "text": c["text"], "status": "unchecked", "note": ""}
        written = [p for p in c["pages"] if on_page(c["text"], texts.get(p, ""))]
        for look in seen.get(c["n"], []):
            found, page = look["found"], look["page"]
            if not found or calc.number(found) is None:
                continue
            if _scanned(session, page):
                pass                                        # a scan has no text to hold it to
            elif not on_page(found, texts.get(page, "")):
                continue                                    # a copy not on the page proves nothing
            else:
                # the cell the model names, read by the code where the table allows
                read = cell(look.get("row", ""), look.get("column", ""), texts.get(page, ""))
                if read is not None:
                    found = read
                elif not on_row(found, look.get("row", ""), texts.get(page, "")):
                    continue                                # nor one from another row than it names
            where = f"{page}" + (f", {look['where']}" if look["where"] else "")
            if same(found, c["text"]):
                mark.update(status="ok", note=where)
            else:
                mark.update(status="fixed", was=c["text"], text=_like(found, c["text"]), note=where)
            break
        if mark["status"] == "unchecked":
            if written:
                mark["note"] = f"written on {written[0]}, but which row and column it belongs to was not confirmed"
            else:
                derived.append(c)
                mark["note"] = "not written on " + (", ".join(c["pages"]) or "the pages found")
        marks.append(mark)

    # 4. what is on no page is worked out again, by the calculator
    if derived and pages and not model.cancelled():
        yield {"event": "check", "stage": "calculate", "numbers": len(derived)}
        shown = "\n\n".join(f"[{p}] {texts[p][:PAGE_CHARS]}" for p in pages)
        calcs, _ = calc.decide(model, question, history, shown)
        results = [r for r in (calc.run(x, {p: texts[p] for p in pages}) for x in calcs) if not r.get("error")]
        for c in derived:
            mark = next(m for m in marks if m["n"] == c["n"])
            hit = next((r for r in results if _close(r["result"], c["text"])), None)
            if hit:
                mark.update(status="ok", note="calculator: " + _how(hit))
            elif len(derived) == 1 and len(results) == 1:
                r = results[0]
                mark.update(status="fixed", was=c["text"], text=_like(r["result"], c["text"]),
                            note="calculator: " + _how(r))

    fixed = _apply(answer, claims, marks)
    ok = sum(m["status"] == "ok" for m in marks)
    corrected = sum(m["status"] == "fixed" for m in marks)
    left = len(marks) - ok - corrected
    summary = (f"{len(marks)} number{'s' if len(marks) != 1 else ''}: {ok} checked"
               + (f", {corrected} corrected" if corrected else "") + (f", {left} not checked" if left else ""))
    yield {"event": "checked", "answer": fixed, "marked": _marked(answer, claims, marks), "summary": summary,
           "marks": [{k: m[k] for k in ("text", "status", "note", "was") if k in m} for m in marks]}


def _scanned(session, page: str) -> bool:
    """A page with no text of its own (a scan): what is copied from its
    picture cannot be found in its text, and is taken as read."""
    text = "\n".join(c.text for c in session.index.chunks if c.where.strip().lower() == page and not c.figure)
    return len(text.strip()) < 40


def _close(result: str, text: str) -> bool:
    x, y = calc.number(result), calc.number(text)
    if x is None or y is None:
        return False
    places = len(text.split(".")[1]) if "." in text else 0
    return abs(x - y) <= Decimal(5) * Decimal(10) ** (-(places + 1)) or same(result, text)


def _how(r: dict) -> str:
    vals = ", ".join(v["value"] for v in r.get("values", []))
    return f"{calc.OPS.get(r.get('op', ''), r.get('op', ''))} of {vals} = {r['result']}"


def _like(found: str, original: str) -> str:
    """The corrected number, written the way the answer wrote the old one
    where it can be (no brackets for a negative it called an overspend)."""
    out = re.sub(r"^[£$€]|[£$€]$", "", found.strip())   # the answer keeps its own £
    if out.startswith("(") and out.endswith(")") and not original.startswith("("):
        out = out[1:-1]
    return out.rstrip("%") if not original.endswith("%") else out


def _field(s: str) -> str:
    return " ".join(str(s).replace("|", "/").replace("⟦", "(").replace("⟧", ")").split())


def _marked(answer: str, claims: list[dict], marks: list[dict]) -> str:
    """The answer with each number wrapped for the page to draw - as text,
    "⟦status|number|was|note⟧", which Prose reads into a mark (never HTML)."""
    out = answer
    for c in sorted(claims, key=lambda c: -c["start"]):
        m = next((m for m in marks if m["n"] == c["n"]), None)
        if m:
            token = f"⟦{m['status']}|{_field(m['text'])}|{_field(m.get('was', ''))}|{_field(m['note'])}⟧"
            out = out[:c["start"]] + token + out[c["end"]:]
    return out


def _apply(answer: str, claims: list[dict], marks: list[dict]) -> str:
    out = answer
    for c in sorted(claims, key=lambda c: -c["start"]):
        m = next((m for m in marks if m["n"] == c["n"]), None)
        if m and m["status"] == "fixed":
            out = out[:c["start"]] + m["text"] + out[c["end"]:]
    return out
