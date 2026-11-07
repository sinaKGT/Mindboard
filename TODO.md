# Mindboard — work still to do

What is left from the v2 conversations, in the order we planned to take it.
Each item says what was asked, what was decided, and what done looks like.
Last updated 2026-11-07, after commit `d6842b1`.

---

## 1. The conversation — waiting for your OK on the mockup

Asked for across several messages; designed as one simple whole, not
separate features. Simplicity and a clean interface come first: it must not
feel like many features.

- **A chat bar at the bottom of the screen.** Replaces the corner bubble and
  `Ctrl+J` as the way in (the shortcut can stay). A slim "Ask your board…"
  bar, centred at the bottom of the canvas, with a gentle live effect so it
  catches the eye. The conversation opens above it.
- **Expand.** One button in the conversation's header: the conversation
  fills most of the screen and the board dims behind it, for reading and
  thinking. The same button, or `Esc`, goes back.
- **Explain more** (a switch in the expanded view, beside *Think deeper*).
  While it is on, a question is **not** searched: it goes to the model with
  the conversation's current cards and history, asking for a proper
  explanation, related examples and a follow-up question. Off again, questions
  search the board as now.
- **The conversation remembers its cards.** Cards cited in earlier answers
  stay in context for follow-ups (first in the prompt, so the model's prompt
  cache is reused), and each new search adds to them.
- **Add to board.** On each answer, one small action. The model drafts a card
  from the answer — a short title and a few lines, not the raw chat — and
  Mindboard proposes where it goes: linked to the cards the answer cited, with
  a relationship (for a new example, *is an example of*), in their group. A
  small preview lets you edit the text and change the card it links to, then
  **Add**. Written through the op-log with provenance `"ai"`, one undoable
  step; the background indexer makes it searchable within seconds.
  (Originally asked as "save results of our chat as a new card, connected to a
  card the system asks me about".)

Done looks like: the mockup approved, built, checked in the app, and DESIGN.md
and README.md updated.

## 2. Retrieval step 3 — planning before retrieval

The third of the three measured steps (DESIGN.md §30). Steps 1 (search
engine) and 2 (reranker and graph ranking) are done.

- The model plans a question before searching: split a multi-part question
  into parts, follow a chain for multi-hop questions ("Docling is an example
  of what, and what does that relate to?"), and decide whether the board needs
  searching at all.
- Show the plan in the panel, briefly.
- **Decide the search budget per question here.** Today it is a fixed 5,000
  tokens. Measured: with the reranker, 2,000 still sends every right card on
  the test set and answers about 1.8 s sooner, but broad questions ("What am I
  missing here?") would see less. The planner can give broad questions more
  and specific ones less.
- Measure with `mindboard ai eval` before and after, as steps 1 and 2 were.

## 3. Pictures that can be searched

Describe each picture once (the 9B reads pictures) and keep the description
with the card, so a picture's content can be found by search — today only its
title and caption are searchable.

## 4. Things not measured or known to be rough

- **The canvas at 10,000 cards** has not been measured — only search was.
- **Relationships are not counted in the search budget.** A search sends
  ~5,000 tokens of cards but the context came to ~8,900 with the outline and
  the relationships between the sent cards.
- **Most of the wait before the first word is now the pictures** a search
  brings (up to 6,000 tokens). Worth measuring a smaller picture budget for
  searches.
- **The follow-up offer and scrolling**: once, the panel stopped about 70 px
  short of the offered question. The pane had been resized at the same time,
  so it may be nothing — worth watching.
- **After a model gives the card back**, the next question pays for
  restarting the search model it stopped (about 1.3 s). Fine for now.

## 5. Housekeeping

- **Delete the unused 27B model files** (about 15 GB in
  `.llm\models\qwen3.8-27b`) if you want the space back — your call.
- **Scratch test data**: the 10,000-card test board lives in the session's
  scratchpad, not in your data folder; nothing to clean in Mindboard itself.
- Not committed on purpose: `.claude/` (test-server settings), your `docs/`
  folder, `web/package-lock.json`.
