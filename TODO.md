# Mindboard — work still to do

What is left from the v2 conversations, in the order we planned to take it.
Each item says what was asked, what was decided, and what done looks like.
Last updated 2026-11-08 - everything planned is done; what is left is optional.

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

## 3. Pictures that can be searched - done (DESIGN.md §38-39)

Described once, searchable, and given to the answering model in words when a
picture itself is left out of a question.

## 4. A fast canvas for big levels - done as far as it needs to go (DESIGN.md §35-36, §39)

3,000 cards on one level zoom at 17-18 frames a second (2 before); boards in
groups are fast at 10,000 cards. A far-out view drawn on a canvas would go
further, but only matters if huge flat levels - not groups - become how
boards are used. Not planned until then.

## 6. Experts - done (DESIGN.md §40-42)

Experts, their voice (Kokoro) and speaking to them (whisper.cpp) are built,
all on this computer.

## 5. Housekeeping

- ~~Delete the unused 27B model files~~ — done, 15 GB freed.
- **Scratch test data**: the 10,000-card test board lives in the session's
  scratchpad, not in your data folder; nothing to clean in Mindboard itself.
- Not committed on purpose: `.claude/` (test-server settings), your `docs/`
  folder, `web/package-lock.json`.
