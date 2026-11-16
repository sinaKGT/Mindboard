"""Tools for asking a spreadsheet (DESIGN.md 65).

Searching a workbook row by row finds what a row says, but cannot add a
column up: a total over two thousand rows is in no passage. So an Excel or
CSV file asked on its own (filechat.py) is also loaded, while it is
prepared, into an in-memory SQLite database - one table a sheet - and the
model is given a few tools over it. Before each answer it decides, from the
question and the sheets' columns, whether to use any: most questions need
none; "what is the total", "which region sold most", "how many rows have..."
do. The tools run, and their results go to the answer beside the passages,
marked as calculated over the whole sheet.

The tools are structured - a sheet, a column, an operation, filters - never
SQL written by the model: every name is checked against the sheet, every
value passed as a parameter, so nothing the model writes can change or
escape the data.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

MAX_ROWS = 200_000             # rows of one sheet loaded for calculating
CALLS_MAX = 3                  # tools used for one question
DECIDE_SECONDS = 120.0         # choosing tools takes this long at most; then the answer goes on without
QUERY_SECONDS = 5.0            # one calculation takes this long at most
ROWS_MAX = 30                  # rows a "rows" tool returns
GROUPS_MAX = 25                # groups a grouped calculation returns
OPS = ("sum", "average", "min", "max", "count", "count_distinct", "median")
COMPARE = {"=": "=", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<=",
           "contains": "LIKE", "starts_with": "LIKE"}


@dataclass
class Column:
    name: str                  # as the sheet's header says it
    sql: str                   # its name in the table
    numeric: bool
    examples: list[str] = field(default_factory=list)


@dataclass
class Sheet:
    name: str
    table: str
    columns: list[Column]
    rows: int


class Workbook:
    """A file's sheets as tables, and the tools over them. Thread-safe: one
    connection, used under a lock."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.sheets: dict[str, Sheet] = {}
        self._lock = threading.Lock()
        self._deadline = 0.0
        # a calculation that runs too long is stopped, not waited on
        self.db.set_progress_handler(lambda: 1 if self._deadline and time.time() > self._deadline else 0,
                                     20_000)

    # ----------------------------------------------------------- loading --
    def add(self, name: str, rows: Iterable[Iterable[Any]]) -> Optional[Sheet]:
        """One sheet: its first non-empty row is the header."""
        it = iter(rows)
        header: Optional[list[str]] = None
        for row in it:
            values = ["" if v is None else str(v).strip() for v in row]
            if any(values):
                header = values
                break
        if header is None:
            return None
        names, seen = [], set()
        for i, h in enumerate(header):
            base = h or f"column {i + 1}"
            n, k = base, 2
            while n.lower() in seen:
                n, k = f"{base} {k}", k + 1
            seen.add(n.lower())
            names.append(n)
        data: list[list[Any]] = []
        for row in it:
            values = [_cell(v) for v in row][:len(names)]
            if not any(v not in (None, "") for v in values):
                continue
            data.append(values + [None] * (len(names) - len(values)))
            if len(data) >= MAX_ROWS:
                break
        numeric = []
        for c in range(len(names)):
            filled = [r[c] for r in data if r[c] not in (None, "")]
            nums = sum(1 for v in filled if _number(v) is not None)
            numeric.append(bool(filled) and nums / len(filled) >= 0.8)
        table = f"t{len(self.sheets) + 1}"
        cols = [Column(n, f"c{i + 1}", numeric[i]) for i, n in enumerate(names)]
        defs = ", ".join(c.sql + (" REAL" if c.numeric else " TEXT") for c in cols)
        with self._lock:
            self.db.execute(f"CREATE TABLE {table} ({defs})")
            self.db.executemany(
                f"INSERT INTO {table} VALUES ({', '.join('?' for _ in cols)})",
                [[(_number(v) if cols[i].numeric else (None if v in (None, "") else str(v)))
                  for i, v in enumerate(r)] for r in data])
            for c in cols:
                c.examples = [str(_show(v[0])) for v in self.db.execute(
                    f"SELECT DISTINCT {c.sql} FROM {table} WHERE {c.sql} IS NOT NULL LIMIT 3")]
        sheet = Sheet(name, table, cols, len(data))
        self.sheets[name.lower()] = sheet
        return sheet

    # ----------------------------------------------------- what it holds --
    def describe(self) -> str:
        """The sheets and their columns, for the model deciding on tools."""
        out = []
        for s in self.sheets.values():
            out.append(f'SHEET "{s.name}" - {s.rows:,} rows')
            for c in s.columns:
                out.append(f'  - "{c.name}" ({"number" if c.numeric else "text"}), e.g. '
                           + ", ".join(c.examples))
        return "\n".join(out)

    def _sheet(self, name: Any) -> Sheet:
        s = self.sheets.get(str(name or "").lower())
        if s is None and len(self.sheets) == 1:
            s = next(iter(self.sheets.values()))          # one sheet: no need to name it
        if s is None:
            raise ToolError(f"no sheet called {name!r}")
        return s

    @staticmethod
    def _col(s: Sheet, name: Any) -> Column:
        want = str(name or "").strip().lower()
        for c in s.columns:
            if c.name.lower() == want:
                return c
        raise ToolError(f'no column called {name!r} in "{s.name}"')

    def _where(self, s: Sheet, filters: Any) -> tuple[str, list]:
        parts, args = [], []
        for f in (filters or [])[:6]:
            if not isinstance(f, dict):
                continue
            c = self._col(s, f.get("column"))
            op = str(f.get("op") or "=").strip().lower()
            if op not in COMPARE:
                raise ToolError(f"cannot compare with {op!r}")
            value = f.get("value")
            if op in ("contains", "starts_with"):
                parts.append(f"{c.sql} LIKE ? ESCAPE '\\'")
                v = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                args.append(f"%{v}%" if op == "contains" else f"{v}%")
            elif c.numeric and _number(value) is not None:
                parts.append(f"{c.sql} {COMPARE[op]} ?")
                args.append(_number(value))
            else:
                parts.append(f"lower({c.sql}) {COMPARE[op]} lower(?)")
                args.append(str(value))
        return (" WHERE " + " AND ".join(parts) if parts else ""), args

    # -------------------------------------------------------------- tools --
    def run(self, call: dict) -> dict:
        """One tool call: {"tool", ...}. A result to show, or an error said."""
        tool = str(call.get("tool") or "")
        self._deadline = time.time() + QUERY_SECONDS
        try:
            if tool == "calculate":
                return self.calculate(call)
            if tool == "rows":
                return self.rows(call)
            if tool == "distinct":
                return self.distinct(call)
            raise ToolError(f"no tool called {tool!r}")
        except ToolError as exc:
            return {"tool": tool, "error": str(exc), "said": f"{tool}: {exc}"}
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                return {"tool": tool, "error": "took too long", "said": f"{tool}: took too long and was stopped"}
            log.warning("a sheet tool failed: %s %r", exc, call)
            return {"tool": tool, "error": "could not be calculated", "said": f"{tool}: could not be calculated"}
        except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            log.warning("a sheet tool failed: %s %r", exc, call)
            return {"tool": tool, "error": "could not be calculated", "said": f"{tool}: could not be calculated"}
        finally:
            self._deadline = 0.0

    def calculate(self, call: dict) -> dict:
        """sum, average, min, max, count, count_distinct or median of a column,
        over rows matching the filters, optionally per group."""
        s = self._sheet(call.get("sheet"))
        op = str(call.get("op") or "sum").lower().replace("avg", "average").replace("mean", "average")
        if op not in OPS:
            raise ToolError(f"no operation called {op!r}")
        col = self._col(s, call["column"]) if call.get("column") else None
        if col is None and op != "count":
            raise ToolError(f"{op} needs a column")
        if col is not None and op in ("sum", "average", "median") and not col.numeric:
            raise ToolError(f'"{col.name}" is not a number column')
        where, args = self._where(s, call.get("where"))
        group = self._col(s, call["group_by"]) if call.get("group_by") else None
        expr = {"sum": f"SUM({col.sql})" if col else "",
                "average": f"AVG({col.sql})" if col else "",
                "min": f"MIN({col.sql})" if col else "", "max": f"MAX({col.sql})" if col else "",
                "count": f"COUNT({col.sql})" if col else "COUNT(*)",
                "count_distinct": f"COUNT(DISTINCT {col.sql})" if col else "",
                "median": ""}[op]
        label = _label(op, col, group, call.get("where"), s)
        with self._lock:
            if op == "median":
                result = self._median(s, col, where, args, group)
            elif group is not None:
                order = "DESC" if str(call.get("order") or "desc").lower() != "asc" else "ASC"
                result = [(_show(g), _show(v)) for g, v in self.db.execute(
                    f"SELECT {group.sql}, {expr} AS v FROM {s.table}{where} GROUP BY {group.sql} "
                    f"ORDER BY v {order} LIMIT {GROUPS_MAX + 1}", args)]
            else:
                result = _show(self.db.execute(f"SELECT {expr} FROM {s.table}{where}", args).fetchone()[0])
            matched = self.db.execute(f"SELECT COUNT(*) FROM {s.table}{where}", args).fetchone()[0]
        if isinstance(result, list):
            more = len(result) > GROUPS_MAX
            said = label + ":\n" + "\n".join(f"  {g}: {v}" for g, v in result[:GROUPS_MAX]) \
                + ("\n  (more groups not shown)" if more else "")
        else:
            said = f"{label} = {result}"
        return {"tool": "calculate", "said": f"{said}  ({matched:,} rows matched)", "label": label,
                "result": result, "rows": matched}

    def _median(self, s, col, where, args, group):
        def med(vals):
            vals = sorted(v for v in vals if v is not None)
            if not vals:
                return None
            m = len(vals) // 2
            return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2
        if group is None:
            return _show(med([r[0] for r in self.db.execute(f"SELECT {col.sql} FROM {s.table}{where}", args)]))
        groups: dict = {}
        for g, v in self.db.execute(f"SELECT {group.sql}, {col.sql} FROM {s.table}{where}", args):
            groups.setdefault(g, []).append(v)
        return sorted(((_show(g), _show(med(v))) for g, v in groups.items()),
                      key=lambda p: -(p[1] if isinstance(p[1], (int, float)) else 0))[:GROUPS_MAX + 1]

    def rows(self, call: dict) -> dict:
        """The rows matching the filters, sorted, a few columns of them."""
        s = self._sheet(call.get("sheet"))
        cols = [self._col(s, c) for c in (call.get("columns") or [])[:8]] or s.columns[:8]
        where, args = self._where(s, call.get("where"))
        order = ""
        if call.get("order_by"):
            oc = self._col(s, call["order_by"])
            order = f" ORDER BY {oc.sql} {'ASC' if str(call.get('order') or 'desc').lower() == 'asc' else 'DESC'}"
        limit = max(1, min(int(call.get("limit") or 10), ROWS_MAX))
        with self._lock:
            got = list(self.db.execute(
                f"SELECT {', '.join(c.sql for c in cols)} FROM {s.table}{where}{order} LIMIT {limit}", args))
            matched = self.db.execute(f"SELECT COUNT(*) FROM {s.table}{where}", args).fetchone()[0]
        label = f'rows of "{s.name}"' + _filters(call.get("where"))
        lines = ["; ".join(f"{c.name}: {_show(v)}" for c, v in zip(cols, r) if v not in (None, "")) for r in got]
        return {"tool": "rows", "label": label, "rows": matched,
                "said": f"{label} - {matched:,} match, {len(got)} shown:\n" + "\n".join(f"  {ln}" for ln in lines)}

    def distinct(self, call: dict) -> dict:
        """The different values a column holds, most common first."""
        s = self._sheet(call.get("sheet"))
        c = self._col(s, call.get("column"))
        with self._lock:
            got = list(self.db.execute(
                f"SELECT {c.sql}, COUNT(*) n FROM {s.table} WHERE {c.sql} IS NOT NULL "
                f"GROUP BY {c.sql} ORDER BY n DESC LIMIT {GROUPS_MAX}", []))
            total = self.db.execute(f"SELECT COUNT(DISTINCT {c.sql}) FROM {s.table}").fetchone()[0]
        label = f'the values of "{c.name}" in "{s.name}"'
        return {"tool": "distinct", "label": label, "rows": total,
                "said": f"{label} - {total:,} different, most common first:\n"
                        + "\n".join(f"  {_show(v)} ({n:,} rows)" for v, n in got)}


class ToolError(Exception):
    pass


def _cell(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date)):
        # ISO text sorts and compares as dates do
        return v.isoformat(sep=" ")[:19] if isinstance(v, dt.datetime) and (v.hour or v.minute) \
            else (v.date() if isinstance(v, dt.datetime) else v).isoformat()
    return v


NUMBER = re.compile(r"^[-+]?[£$€]?\s?[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?\s?%?$")


def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v or "").strip()
    if not t or not NUMBER.match(t):
        return None
    try:
        return float(re.sub(r"[£$€,%\s+]", "", t))
    except ValueError:
        return None


def _show(v: Any) -> Any:
    if isinstance(v, float):
        return int(v) if v.is_integer() and abs(v) < 1e15 else round(v, 4)
    return v


def _filters(where: Any) -> str:
    parts = [f'{f.get("column")} {f.get("op") or "="} {f.get("value")!r}'
             for f in (where or []) if isinstance(f, dict)]
    return (" where " + " and ".join(parts)) if parts else ""


def _label(op: str, col: Optional[Column], group: Optional[Column], where: Any, s: Sheet) -> str:
    what = f'{op.replace("_", " ")} of "{col.name}"' if col else "count of rows"
    return f'{what} in "{s.name}"' + _filters(where) + (f' per "{group.name}"' if group else "")


# ------------------------------------------------------------ deciding --

DECIDE = """You decide whether answering a question about a spreadsheet needs
calculating over its whole sheets. Passages of matching rows are found by
search anyway; they are enough for questions about what a particular row or
item says. Use tools for totals, averages, counts, minimums, maximums, rankings
("which ... most"), comparisons across many rows, or listing every row that
matches a condition.

TOOLS (at most 3 calls):
- {"tool": "calculate", "sheet": "...", "op": "sum|average|min|max|count|count_distinct|median",
   "column": "...", "where": [{"column": "...", "op": "=|!=|>|<|>=|<=|contains|starts_with", "value": ...}],
   "group_by": "...", "order": "desc|asc"}   (column may be left out for count; where and group_by are optional)
- {"tool": "rows", "sheet": "...", "where": [...], "columns": ["..."], "order_by": "...", "order": "desc|asc", "limit": 10}
- {"tool": "distinct", "sheet": "...", "column": "..."}

Pick the operation by what is asked:
- "total", "how many ... in all", "which X has the most/least Y" (Y a number)
  -> sum of Y, with group_by X for "which X" (ordered desc for most, asc for least)
- "which X appears most often", "how many rows/visits/orders" -> count, with group_by X if per X
- "the largest/smallest single ..." -> max/min
- "average", "typical" -> average or median

Use sheet and column names exactly as listed. Dates are written YYYY-MM-DD,
so compare them as text, e.g. {"column": "Date", "op": ">=", "value": "2026-01-01"}.

Answer with JSON only: {"why": "a few words", "calls": [...]} - and
{"why": "...", "calls": []} when no tool is needed."""


def decide(model, book: Workbook, question: str, history: Optional[list[dict]] = None
           ) -> tuple[list[dict], str, str]:
    """The tool calls the model chooses for this question - often none - why,
    and what went wrong choosing, if anything ("" when nothing did). A choice
    that takes too long or cannot be read is no choice: the answer goes on
    from the passages, and the person is told no calculation was made."""
    convo = "\n".join(f"{t.get('role')}: {str(t.get('text', ''))[:300]}"
                      for t in (history or [])[-4:] if str(t.get("text", "")).strip())
    prompt = (f"{book.describe()}\n\n" + (f"CONVERSATION SO FAR\n{convo}\n\n" if convo else "")
              + f"QUESTION\n{question.strip()}")
    reply, deadline = [], time.time() + DECIDE_SECONDS
    stream = model.stream([{"role": "system", "content": DECIDE}, {"role": "user", "content": prompt}],
                          max_tokens=500, think=False, temperature=0.1, hold=False)
    try:
        for ev in stream:
            if ev["type"] == "text":
                reply.append(ev["text"])
            elif ev["type"] == "done" and ev.get("stopped"):
                return [], "", "stopped"
            if time.time() > deadline:
                return [], "", "choosing a calculation took too long"
    except Exception as exc:                 # the model failed: said, and the answer goes on
        log.warning("choosing tools failed: %s", exc)
        return [], "", "the model could not choose a calculation"
    finally:
        stream.close()                       # a reply cut short hangs up, and llama-server stops
    text = "".join(reply)
    try:
        got = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        log.warning("the tool choice could not be read: %r", text[:300])
        return [], "", "the model's choice of calculation could not be read"
    if not isinstance(got, dict):
        return [], "", "the model's choice of calculation could not be read"
    calls = [c for c in (got.get("calls") or []) if isinstance(c, dict) and c.get("tool")]
    return calls[:CALLS_MAX], str(got.get("why") or "")[:200], ""


def calculated_text(results: list[dict], trouble: str = "") -> str:
    """The tools' results, as the model reads them before the passages - and
    what could not be calculated, so it is said rather than guessed."""
    ok = [r for r in results if not r.get("error")]
    failed = [r for r in results if r.get("error")]
    out = ""
    if ok:
        out += ("CALCULATED over the whole sheets, with tools (exact - prefer these to adding up "
                "passages yourself; say \"(calculated)\" after a figure you take from here):\n"
                + "\n".join(f"- {r['said']}" for r in ok) + "\n\n")
    if failed or trouble:
        out += ("NOT CALCULATED: " + "; ".join([r["said"] for r in failed] + ([trouble] if trouble else []))
                + ". Do not work these figures out from the passages - they are only part of the "
                "sheet; say they could not be calculated.\n\n")
    return out
