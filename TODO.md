# Mindboard — work still to do

Only what is left, in the order we plan to take it. Each item says what was
asked, what was decided, and what done looks like.
Last updated 2026-11-21 - A and B done (DESIGN.md §70-71); next: C.

---

## C. Answers

- **Explain more should use the model's own knowledge when the board has
  nothing.** Asked for "two examples" with Explain more on, it says the
  context does not have them. It should give them from what it knows, marked
  plainly as not from your cards (rule 3 still holds: board facts cited,
  general knowledge labelled, never passed off as the board's).
- **Tables in answers when they help**, and a **"Make a table" action under
  an answer** that rewrites it as a table (and could add it to the board as
  a card - cards already draw markdown tables, §69).

## D. The board

- **@ in card text to link another card.** Typing @ while writing a card
  opens a picker of cards; choosing one puts a link in the text (opens that
  card when clicked) and adds a link between the two cards - as ops.
  To decide: whether the board link is always added or only offered.
- **Export picture wrong when the board is zoomed** (`exportImage.ts`).
  To confirm when we start: what exactly goes wrong (cut off, blurred,
  shifted) and whether this is the export or something else.

## E. Search

- **The effect of groups on retrieval.** Measure whether a card's group
  should count when searching: e.g. the group's title added to its cards'
  text, or a hit spreading to the other cards in its group (as links do).
  Measured on the test set as in §37 (MRR, time) before adopting anything.
- **Planning for Ask your board** (from §37, §47): measure the planned
  context on the test set as §37 did, and decide whether Ask your board
  should plan the way experts do.

## F. Tools and the person

- **More work on Mindboard's tools** (sheets §65, steps §66, file chat
  §64). To decide together: which tools are missing or weak - a list to make
  before any building.
- **Knowing the person.** A local profile: name, preferences, how they like
  to be taught; Mindboard learns some of it from the conversation (shown and
  editable, never hidden) and can talk in an educator's manner that follows
  it. Stays on this computer like everything else.

## G. Installing and releasing

- **Node.js in the installation** (`install.sh`, `install.ps1`, `mindboard
  update`), for building the interface.
- **A guide for coding agents** (`AGENTS.md`): how an AI agent installs and
  sets up Mindboard on a new computer by itself - steps, checks, what to ask
  the person.
- **Merge `v2` into `main`** - after the fixes above, with the tests and
  build passing.

---

## Later, only if needed

- **A far-out view drawn on a canvas** for huge flat levels (§35-36, §39).
  Boards in groups are already fast at 10,000 cards; only worth doing if
  huge flat levels, not groups, become how boards are used.
