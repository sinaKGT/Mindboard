# Mindboard — work still to do

What is left from the v2 conversations, in the order we planned to take it.
Each item says what was asked, what was decided, and what done looks like.
Last updated 2026-11-08.

---

## 1. The conversation — done (DESIGN.md §34)

Chat bar, expand, Explain more, remembered cards and Add to board are built.
Worth watching in daily use: whether the drafted card's link and relationship
are usually right, and whether the bar's light feels calm rather than busy.

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

## 4. A fast canvas for big levels - mostly done (DESIGN.md §35-36)

Relationship lines count in the budget; pictures in a search are capped at
2,000 tokens (first words 5.0 s -> 3.0 s); zooming 3,000 cards on one level
went from 2 to 18 frames a second.

Left, if big flat levels matter: the remaining cost is painting ~2,300
relationship curves per frame - thin or simplify them when zoomed far out, or
draw them on a canvas. Measure the same way (frames a second while zooming).

## 5. Housekeeping

- ~~Delete the unused 27B model files~~ — done, 15 GB freed.
- **Scratch test data**: the 10,000-card test board lives in the session's
  scratchpad, not in your data folder; nothing to clean in Mindboard itself.
- Not committed on purpose: `.claude/` (test-server settings), your `docs/`
  folder, `web/package-lock.json`.
