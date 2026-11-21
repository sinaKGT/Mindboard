# CLAUDE.md — working on Mindboard

Mindboard is a local-first mind-map canvas with a chat over your own notes:
boards of cards and links, answered by local models (llama.cpp: Qwen3.5 chat +
vision, Qwen3 embedding and reranker). Nothing leaves the computer.

- **Why things are the way they are:** `DESIGN.md`, numbered sections §1–§79.
  Read the sections for the area you touch before changing it, and add a new
  section for every change of substance (see "Writing a DESIGN.md section").
- **How it fits together, workflow by workflow:** `docs/architecture.md`
  (git-ignored, the owner's local copy — may be missing on other machines).

## Layout

```
src/mindboard/         Python server (FastAPI) and CLI
  app.py               every HTTP route; stream_answer() for SSE answers
  ops.py               the op-log - the ONLY writer of nodes/edges
  ask.py               board context, prompts (SYSTEM, OWN_SYSTEM, FILE_SYSTEM),
                       answer_events(), citations, dates, LoopGuard, proofread, drafts
  retrieve.py          search: phrases + BM25 + meaning, RRF (k=60), rerank, link spread
  search_index.py      per-board Tantivy index + vectors; picture/PDF notes
  indexer.py           background: embeddings, PDF text, picture descriptions
  tasks.py             understand the request -> chained steps -> answer shape (§66)
  filechat.py          asking one file: prepare, search inside it, page pictures (§64)
  sheets.py            spreadsheet tools over in-memory SQLite (§65)
  experts.py           experts: library over boards, plan, steps, voice (§40-47)
  llm.py               install/start local models; LocalModel, the question slot
  bundle.py            backups and restore; home.py, themes.py, updater.py, menu.py, cli.py
web/src/               React + TypeScript (Vite)
  api.ts               every call to the server, SSE reader
  askStore.ts          chat state: turns, Board off, file branches
  AskPanel.tsx         the chat; FileBranch.tsx, TaskSteps.tsx, Pasted.tsx beside it
  Canvas.tsx nodes.tsx actions.ts store.ts   the board (edits as ops)
  Prose.tsx Math.tsx   answer layout, citations, page links, KaTeX
  experts/             Experts page
tests/                 pytest; fake_llama_server.py plays llama-server
```

## Commands

```bash
# tests (Windows venv path; on macOS/Linux use .venv/bin/python)
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_tasks.py

# interface: type-check and build into src/mindboard/web/
cd web && npx tsc --noEmit -p . && npm run build
git checkout -- src/mindboard/web/.gitkeep    # the build wipes it; restore it every time

# install a dependency into the venv (there is no pip in it; use uv)
uv pip install --python .venv/Scripts/python.exe "<package>"

# run the app
mindboard run            # port 7788 by default; `mindboard menu` for the menu
```

Add Python dependencies to `pyproject.toml` (with a comment saying what for and
the licence) — `install.sh` / `install.ps1` / `mindboard update` install from it.

## The process for every change

Every change goes through these steps, in this order. Say which step you are
on; do not skip one because the change looks small.

1. **Plan.** Understand what is asked; read the DESIGN.md sections for the
   area and the code it touches. Say the plan in a few lines. Ask only when
   something is genuinely unclear - not for what the code or a sensible
   default can settle.
2. **Design, if the change is something the owner sees.** Show a mock-up
   first (two or three directions when the look is open), say which you
   recommend, and wait for the owner's choice before building it. Skip this
   for fixes and changes with no new look.
3. **Implement.** Follow the rules of the house below; one change of
   substance at a time.
4. **Test.** Unit and route tests for what changed (`pytest`), the interface
   type-checked and built, and the change checked in the browser
   (`.claude/launch.json`; restart the server after Python changes). For
   anything the model does, check it with the real model too. Then add the
   DESIGN.md section, with what was measured and what is not done yet.
5. **Update TODO.md.** Take out what is done, add what was found or left
   over, and update its "Last updated" line.
6. **Commit** on `dev`, as set out under Git below.
7. **Push to GitHub:** `git push origin dev`. Merging into `main` (a pull
   request, then merge) happens only when the owner asks for it.

## Rules of the house

1. **Every board change is an op** through `POST /api/boards/{bid}/ops`
   (`ops.py`). Never write node/edge tables any other way. The interface
   builds ops with `newNode` / `newEdge` and `store.run([...])`.
2. **The model has one question slot.** Take it with `take_turn()` (app.py
   helper / `LocalModel.take_turn`) — it waits; never refuse at once. A run
   holds it for its whole length (`stream_answer(..., hold=True)`), and calls
   inside use `model.stream(..., hold=False)` or `model.chat(...)`. Background
   work must check `model.waiting` and step aside.
3. **Facts only.** Answers cite their sources (`[[card-id]]`, `(p. 12)`,
   `(calculated)`). Anything that fails is *said* to the person and to the
   model ("NOT CALCULATED — do not work these out"), never filled with a
   guess. Don't let a prompt invite totals or "all of them" from partial
   passages.
4. **Never put a whole file in the context.** Files are prepared and searched
   (filechat.py). Context budget: 32,768 tokens; answer 3,072; thinking 8,192.
5. **Time-limit every model step** and hang up on overrun (close the stream
   generator — llama-server stops). Existing limits are listed in
   `docs/architecture.md` §5; follow the same pattern (`tasks.json_reply`).
6. **Keep llama-server's cache warm:** stable things first in the prompt
   (system, cards), per-question things last (plan notes, task notes, dates,
   pasted pictures) — see `ask.messages()`.
7. **Structured tools, not model-written code.** The sheet tools take a sheet,
   column, operation and filters; names are checked, values are parameters.
   Follow that for any new tool.
8. **The model's JSON is untrusted:** parse between the first `{` and last
   `}`, validate every field (`tasks.clean`, `experts.clean_plan`), drop what
   isn't allowed, and have a sensible fallback (answer as it stands).
9. **Errors reach the page as events**, not exceptions: `stream_answer` turns
   exceptions into `event: error`. New stream stages emit their own events
   (`task`, `step`, `tools`, `pasted`) and the page must cope when they are
   absent.
10. **Temporary means temporary:** file sessions live in memory and
    `<data>/tmp/filechat`, removed on close, after 6 h, and on start.

## Testing

- `tests/fake_llama_server.py` stands in for llama-server: it streams a fixed
  answer citing the first card, describes any picture as "MINDBOARD beside a
  red circle", embeds by text length, reranks by shared words, and answers the
  task planner (system prompt starting "You read a person's request") with a
  no-steps JSON. Teach it new system prompts the same way rather than making
  tests slow.
- `events(res)` in `tests/test_ask.py` drops task events (`task`, `step`,
  `task_step`) unless `tasks=True`.
- Unit-test pure pieces with small fakes (see `tests/test_tasks.py` `Model`,
  `tests/test_filechat.py` `Words`, `Choosing`); route tests use the `client`
  and `model` fixtures.
- After a real behaviour change, check with the real model too, and write
  what it actually said into the DESIGN.md section (numbers, timings).
  Every DESIGN.md section ends with the test count.

## Checking in the browser

`.claude/launch.json` has a `mindboard-10k` server (port 7798, a scratch data
folder with test boards, e.g. "SWIFT Lab Visitors"). The server does not
reload Python: stop and start it after server changes. Add a fresh `v=` query
parameter to the URL to avoid a stale cached page. If the browser pane is
hidden, drive the page with JavaScript (set a textarea's value through the
native setter, then click `.ask-bar .send.ready`).

## Editing gotchas

- Shell heredocs mangle backslashes (`\n`, `\d`, `\\`) in Python/TS sources.
  For code with escapes, use the Edit/Write tools, or write a patch script
  with the Write tool and run it.
- Python 3.10 is supported: no nested same-quote f-strings.
- The interface build output is git-ignored; never commit
  `src/mindboard/web/*` (except `.gitkeep`).

## Writing a DESIGN.md section

`## NN. Title in plain words (YYYY-MM-DD)` — what was wrong or wanted, what was
done, the numbers that decided it, what was tried and dropped, what is not done
yet, and finally `NNN tests.` Plain, exact English; British spelling; no
marketing words. Code comments follow the same voice and cite `DESIGN.md NN`.

## Git

- Work on branch `dev`; `main` is the release branch. Only these two exist.
- Never commit `docs/`, `.claude/`, `web/package-lock.json`, `.llm/`.
- Commit messages: a short title line, a paragraph on what and why, and end
  with the attribution line the session gives (currently
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).
- Commit and push `dev` at the end of every change (steps 6-7 of the process
  above). Merge into `main` only when the owner asks.
- The GitHub CLI is at `C:\Program Files\GitHub CLI\gh.exe` (it may not be on
  the PATH of an older shell); pull requests go from `dev` into `main`.
