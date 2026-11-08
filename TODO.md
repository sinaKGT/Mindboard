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

## 3. Pictures that can be searched - done (DESIGN.md §38-39)

Described once, searchable, and given to the answering model in words when a
picture itself is left out of a question.

## 4. A fast canvas for big levels - done as far as elements go (DESIGN.md §35-36, §39)

3,000 cards on one level zoom at 17-18 frames a second (2 before). Beyond
that needs a far-out view drawn on a canvas - only worth it if huge flat
levels, rather than groups, turn out to be how boards are used.

## 6. Experts - done (DESIGN.md §40-41)

Experts and their voice (Kokoro, local) are built. Left, if wanted: speaking
*to* an expert - local speech recognition with whisper.cpp (one more small
model and program; the browser's own recognition sends audio to a server).

## 5. Housekeeping

- ~~Delete the unused 27B model files~~ — done, 15 GB freed.
- **Scratch test data**: the 10,000-card test board lives in the session's
  scratchpad, not in your data folder; nothing to clean in Mindboard itself.
- Not committed on purpose: `.claude/` (test-server settings), your `docs/`
  folder, `web/package-lock.json`.
