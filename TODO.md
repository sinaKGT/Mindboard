# Mindboard — work still to do

Only what is left, in the order we plan to take it. Each item says what was
asked, what was decided, and what done looks like.
Last updated 2026-11-21 - A and B done (DESIGN.md §70-71), card AI added
(§72), Node.js in the installers and AGENTS.md (§74), card AI in a panel
with a quiz as data, soft glass cards and Beautify (§75-79); all merged into
main (PR #4); Explain more gives what it is asked for, with no "the cards
do not cover it" line (§80-81); numbers in file answers - rows kept, a
calculator and Double check (§82); the card AI panel moves (§83); card
titles in plain words (§84); Make a table under an answer (§85); Explain
more takes nothing from the board (§86); work moved from v2 to dev;
Summarise beside Add to board, scrolling while it writes, no hover text in
the chat (§87); a board answer citing no card says so (§88); Explain more
understands, plans and answers, in a file's conversation too, and page
lists are links (§89); tables with formulas keep their columns (§90);
Add to board keeps a table the answer wrote (§91); C (answers) closed by
the owner; next: D.

---

## D. The board

- **@ in card text to link another card.** Typing @ while writing a card
  opens a picker of cards; choosing one puts a link in the text (opens that
  card when clicked) and adds a link between the two cards - as ops.
  To decide: whether the board link is always added or only offered.
- **Export picture wrong when the board is zoomed** (`exportImage.ts`).
  To confirm when we start: what exactly goes wrong (cut off, blurred,
  shifted) and whether this is the export or something else.
- **Lists in the card editor.** Bullet and numbered lists left the card bar's
  Aa menu (DESIGN.md §71) to go into the editor; the editor does not have them
  yet. Done looks like: a list button (or `- ` / `1. ` typed at a line start)
  while editing a card, as an op like any edit.
- **A way to move a card out of its group.** "Move out of this group" lost its
  button with the card bar's "more" menu (§71); nothing else does it now.
  To decide: where it goes (dragging out, a key, or back on the bar).

## D2. Card AI - loose ends (from §72-79)

- **Break it into cards: a report cut off.** "When I click on break it down
  it shows..." - the rest of the sentence never came. The list-text fix
  (§77) may have been it. To do: ask what it showed, and try it on a few cards.
- **Fun fact still slips sometimes.** Without today's date and told to work
  nothing out (§78), one run in three still called a date "future" from the
  card's own words. Worth a few more runs on richer cards before deciding.

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

- **Try AGENTS.md on a clean computer** with an agent (Windows and a Mac),
  and fix what it trips on. The installers' Node.js offer has been run on
  Windows only; the Mac/Linux one in Git Bash, not on a real Mac or Linux.

---

## Later, only if needed

- **A far-out view drawn on a canvas** for huge flat levels (§35-36, §39).
  Boards in groups are already fast at 10,000 cards; only worth doing if
  huge flat levels, not groups, become how boards are used.
