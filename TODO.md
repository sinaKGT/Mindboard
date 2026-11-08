# Mindboard — work still to do

What is left from the v2 conversations, in the order we planned to take it.
Each item says what was asked, what was decided, and what done looks like.
Last updated 2026-11-08.

---

## 1. The conversation — done (DESIGN.md §34)

Chat bar, expand, Explain more, remembered cards and Add to board are built.
Worth watching in daily use: whether the drafted card's link and relationship
are usually right, and whether the bar's light feels calm rather than busy.

## 2. Retrieval step 3 - measured, not adopted (DESIGN.md §37)

Planning before searching made search worse on the test set (MRR 0.94 ->
0.87) and 1.5 s slower; multi-hop questions gained nothing. Revisit only with
a test set rich in multi-part questions, or a planner that leaves simple
questions alone.

## 3. Pictures that can be searched - done (DESIGN.md §38)

Each picture is described once by the 9B while it is loaded anyway; the
description is searchable. Not done: giving the descriptions to the answering
model for pictures left out of a question (it sees the pictures it is sent).

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
