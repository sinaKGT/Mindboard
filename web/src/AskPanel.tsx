/** Ask your board: the cards in context, and a conversation about exactly
 *  those cards with the local model (DESIGN.md 25).
 *
 *  It appears when used and folds away otherwise - not a menu, the place a
 *  question about your own cards goes. Everything it holds is screen state;
 *  nothing here writes to the board.
 */
import { useEffect, useRef, useState } from 'react'

import { api } from './api'
import { AnswerFiles, filesToOffer } from './AnswerFiles'
import { AnswerPictures, picturesToShow } from './AnswerPictures'
import { askable, cardsInside, hasNumbers, revealCard, summarisable, tablePart, tableable, useAsk, type Turn } from './askStore'
import { Calculated, FileStrip, FileTabs, PagesUsed, pageLink } from './FileBranch'
import { stepLine, TaskSteps } from './TaskSteps'
import { Icon } from './icons'
import { AskedWith, PastedStrip, PickPicture, usePasted } from './Pasted'
import { Prose } from './Prose'
import { plainTitle } from './text'
import { useStore } from './store'
import { useUI } from './ui'
import { newEdge, newNode, type MEdge, type Op } from './types'

const BEYOND = 'Beyond your board:'
const STARTER = 'What am I missing here?'

/** The model ends an answer with "NEXT: <a question>"; the server takes that
 *  line off when the answer is done and sends the question on its own. While
 *  the answer is still being written, the line - or the start of it - is kept
 *  out of sight here. */
const NEXT_LINE = /(^|\n)[ \t*_>#-]*(NEXT:[\s\S]*|N(E(X(T)?)?)?)$/i
const hideNext = (text: string) => text.replace(NEXT_LINE, '')
/** **Beyond your board:**, or a heading of it, read as the plain line - as the
 *  server does (ask.BEYOND_MARK) - so no stray ** is left either side */
const BEYOND_MARK = /(^[ \t]*#{1,6}[ \t]*)?(\*\*|__)?[ \t]*Beyond your board:[ \t]*(\*\*|__)?/m

/** A card named in an answer: its title, and a click that goes to it. */
function Cite({ id }: { id: string }) {
  const node = useStore((s) => s.nodes[id])
  return (
    <button className="cite" aria-label="Go to this card" onClick={() => revealCard(id)}>
      {node ? (plainTitle(node.title) || 'Untitled') : 'a card no longer here'}
    </button>
  )
}

/** An answer laid out (Prose): headings, lists with their depth, bold, and
 *  [[card]] citations as chips. Nothing is ever interpreted as HTML. */
function Rich({ text, page }: { text: string; page?: (page: number, label: string) => React.ReactNode }) {
  return <Prose text={text} cite={(id) => <Cite id={id} />} page={page} />
}

/** a pin: the chat stays open when the board is clicked (DESIGN.md 68) */
function PinIcon({ on }: { on: boolean }) {
  return (
    <svg width={14} height={14} viewBox="0 0 16 16" fill={on ? 'currentColor' : 'none'} stroke="currentColor"
      strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" aria-hidden>
      <path d="M6 2.5h4l-.6 4 2.1 2.2H4.5l2.1-2.2z" />
      <path d="M8 8.7v4.8" fill="none" />
    </svg>
  )
}

/** the file conversation being shown, if one is (DESIGN.md 64) */
const useBranch = () => useAsk((s) => (s.active ? s.branches.find((b) => b.id === s.active) : undefined))

/** The pictures an answer cites, shown under it (DESIGN.md 48) - from the
 *  board's own cards, which this page already has. */
function CitedPictures({ turn }: { turn: Turn }) {
  const nodes = useStore((s) => s.nodes)
  const boardId = useStore((s) => s.boardId)
  if (!boardId) return null
  const pictures = picturesToShow(turn.cited ?? [], turn.cards ?? [], (id) => {
    const n = nodes[id]
    const sha = !n ? undefined : (n.type === 'image' ? n.content.sha256 : n.type === 'video' ? n.content.poster : undefined)
    return typeof sha === 'string' ? { card: id, board: boardId, sha, title: n.title || 'Untitled' } : null
  })
  return <AnswerPictures pictures={pictures} onShow={(p) => revealCard(p.card)} />
}

/** The PDFs this answer touches, offered to read more (DESIGN.md 48). */
function OfferedFiles({ turn }: { turn: Turn }) {
  const nodes = useStore((s) => s.nodes)
  const boardId = useStore((s) => s.boardId)
  if (!boardId) return null
  const files = filesToOffer(turn.cited ?? [], turn.cards ?? [], (id) => {
    const n = nodes[id]
    const sha = n?.content.sha256
    const name = String(n?.content.filename || n?.content.alt || n?.title || '')
    const pdf = n?.type === 'file' && typeof sha === 'string' && (n.content.mime === 'application/pdf' || /\.pdf$/i.test(name))
    return pdf ? { card: id, board: boardId, sha: sha as string, name } : null
  })
  return <AnswerFiles files={files} onShow={(f) => revealCard(f.card)}
    onAsk={(f) => void useAsk.getState().openFile({ board: f.board, sha: f.sha, card: f.card, name: f.name })} />
}

const fmt = (n: number) => n.toLocaleString()

/** What the model is doing before its first word - said precisely, because on
 *  a shared graphics card reading a big context can take minutes, and "loading"
 *  for all of it looked like a hang. */
function statusLine(t: Turn): string | null {
  if (t.status === 'loading') return 'Starting the model…'
  if (t.status === 'thinking') return 'Thinking…'
  const step = stepLine(t)
  if (step) return step
  if (t.status !== 'reading') return null
  if (t.looking) return 'Looking at your picture…'
  // a card action says what it is doing, all the way to its first word (DESIGN.md 75)
  if (t.doing) return t.doing
  // before the answer's context: the request being understood (DESIGN.md 66)
  if (t.used === undefined) return t.task ? 'Getting ready to write…' : 'Understanding your request…'
  const what = t.mode === 'search' ? `the ${t.used} best-matching cards`
    : t.mode === 'file' ? `${t.used} passages from the file` : 'your cards'
  const p = t.progress
  if (p && p.total > 0) {
    const fresh = Math.max(p.total - p.cached, 1)
    const pct = Math.min(100, Math.round(((p.done - p.cached) / fresh) * 100))
    const remembered = p.cached > 0 ? ` (${fmt(p.cached)} already read before)` : ''
    return `Reading ${what} — ${pct}% of ${fmt(fresh)} tokens${remembered}…`
  }
  return `Reading ${what}${t.tokens ? ` — about ${fmt(t.tokens)} tokens` : ''}…`
}

const KIND_WORDS: Record<string, string> = {
  relates: 'relates to', supports: 'supports', 'example-of': 'is an example of',
  'part-of': 'is part of', causes: 'causes', contradicts: 'contradicts',
}

/** Add to board (DESIGN.md 34, 53): the model writes what this answer adds
 *  that the conversation's cards do not already say - most often what Explain
 *  more brought in from general knowledge - picks the card it belongs with
 *  among every card this conversation found, and how they relate: a kind, and
 *  a label of its own where a kind is too coarse. The card goes on the board
 *  at once, one undoable step through the op-log, marked as made by AI, and
 *  the canvas asks "Right place?" beside it (PlacedCheck): keep it, or link
 *  it to another card yourself. */
function AddToBoard({ question, turn, onDone }: { question: string; turn: Turn; onDone: () => void }) {
  const boardId = useStore((s) => s.boardId)
  const turns = useAsk((s) => s.turns)
  const branch = useBranch()
  const [error, setError] = useState('')

  useEffect(() => {
    if (!boardId) return
    const nodes = useStore.getState().nodes
    const at = turns.indexOf(turn)
    const before = turns.slice(0, Math.max(at - 1, 0))
    // every card this conversation brought up - what answers cited and what
    // searches found - the latest first: Explain more cites none of its own
    const seen = [turn, ...before.slice().reverse()]
      .flatMap((t) => [...(t.cited ?? []), ...(t.cards ?? [])])
    const candidates = [...new Set([...(branch?.card ? [branch.card] : []), ...seen])]
      .filter((id) => nodes[id]).slice(0, 60)
    const history = before
      .filter((t) => !t.error && t.text.trim() && (t.role === 'user' || t.status === 'done'))
      .slice(-6).map((t) => ({ role: t.role, text: t.text }))
    const cited = (turn.cited ?? []).filter((id) => nodes[id])
    // a table shown in the answer's place (DESIGN.md 85) is what goes on the card
    const table = turn.table?.status === 'done' && turn.table.shown ? tableForCard(turn.table.table ?? '') : ''
    // so is a summary shown in its place (DESIGN.md 87)
    const sum = turn.summary?.status === 'done' && turn.summary.shown ? turn.summary : null
    const summary = sum ? [sum.board, sum.beyond ? `Beyond your board:\n${sum.beyond}` : ''].filter(Boolean).join('\n') : ''
    api.askDraft(boardId, { question, answer: table || summary || turn.text, cited, candidates, history })
      // the drafter titles what is new, which a table is not: the question names it
      .then((draft) => place(table ? { ...draft, title: question.trim().replace(/[?.!]+$/, ''), text: table } : draft))
      .catch((e) => setError(e.message || 'could not draft the card'))
    // drafted once, when asked for
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const place = (draft: { title: string; text: string; link_to: string; kind: string; label: string; why: string }) => {
    const store = useStore.getState()
    const target = draft.link_to ? store.nodes[draft.link_to] : undefined
    // beside the card it belongs with, in its group; else on the level in view
    const pos: [number, number] = target
      ? [target.pos[0] + (target.size[0] ?? 240) + 90, target.pos[1] + 30]
      : [0, 0]
    const parent = target ? target.parent : (store.path[store.path.length - 1] ?? null)
    const card = newNode({ pos, parent, title: draft.title.trim() || question.slice(0, 70),
                           content: { text: draft.text.trim() }, provenance: 'ai' })
    const ops: Op[] = [{ kind: 'node.create', node: card }]
    let edgeId: string | null = null
    if (target) {
      const edge = newEdge(card.id, target.id, { kind: draft.kind as MEdge['kind'], label: draft.label,
                                                provenance: 'ai' })
      edgeId = edge.id
      ops.push({ kind: 'edge.create', edge })
    }
    store.run(ops)
    useUI.getState().set('placed', {
      cardId: card.id, edgeId, targetId: target?.id ?? null, why: draft.why,
      relation: draft.label || KIND_WORDS[draft.kind] || 'relates to' })
    // the board has to be seen to answer "Right place?": the chat steps aside
    // unless it sits along the left edge, out of the way
    const ask = useAsk.getState()
    if (ask.expanded || ask.side !== 'left') ask.setOpen(false)
    onDone()
    revealCard(card.id)
  }

  if (error) return <div className="ask-error">{error}</div>
  return <div className="ask-note">Finding what is new here, and where it belongs on your board…</div>
}

/** a table as a card's text: no citation chips, which a card does not draw,
 *  and no "Also from" line, which only names them */
const tableForCard = (table: string) =>
  table.replace(/\n+Also from:.*$/s, '').replace(/\s*\[\[[0-9A-Za-z]{26}\]\]/g, '').trim()

/** Make a table (DESIGN.md 85): under an answer, its own words laid out as
 *  rows and columns, shown in its place; "Show as written" brings it back. */
function TableButton({ turn, index }: { turn: Turn; index: number }) {
  const busy = useAsk((s) => s.busy)
  const t = turn.table
  if (t?.status === 'done') {
    return (
      <button className="ask-check" onClick={() => useAsk.getState().showTable(index, !t.shown)}>
        <Icon name={t.shown ? 'type' : 'table'} size={11} /> {t.shown ? 'Show as written' : 'Show as a table'}
      </button>
    )
  }
  if (t?.status === 'making' || t?.status === 'none') return null
  if (!tableable(tablePart(turn.text).text)) return null
  return (
    <button className="ask-check" disabled={busy}
      onClick={() => void useAsk.getState().makeTable(index)}>
      <Icon name="table" size={11} /> {t?.status === 'failed' || t?.status === 'stopped' ? 'Try the table again' : 'Make a table'}
    </button>
  )
}

/** Summarise (DESIGN.md 87): beside Add to board, the answer's own words
 *  shorter, shown in its place; "Show as written" brings it back. */
function SummaryButton({ turn, index }: { turn: Turn; index: number }) {
  const busy = useAsk((s) => s.busy)
  const m = turn.summary
  if (m?.status === 'done') {
    return (
      <button className="ask-check" onClick={() => useAsk.getState().showSummary(index, !m.shown)}>
        <Icon name="type" size={11} /> {m.shown ? 'Show as written' : 'Show the summary'}
      </button>
    )
  }
  if (m?.status === 'making' || m?.status === 'none') return null
  if (!summarisable(turn.text)) return null
  return (
    <button className="ask-check" disabled={busy} onClick={() => void useAsk.getState().summarise(index)}>
      <Icon name="type" size={11} /> {m?.status === 'failed' || m?.status === 'stopped' ? 'Summarise again' : 'Summarise'}
    </button>
  )
}

/** what Summarise said, under the answer */
function SummaryNote({ turn }: { turn: Turn }) {
  const m = turn.summary
  if (!m) return null
  if (m.status === 'making') return <div className="ask-status checking">Summarising the answer…</div>
  if (m.status === 'stopped') return <div className="ask-note">Summary stopped.</div>
  if (m.status === 'failed') return <div className="ask-error">{m.note || 'Could not summarise.'}</div>
  if (m.status === 'none') return <div className="ask-note">{m.note}</div>
  if (!m.shown) return null
  return (
    <div className="ask-note">
      Summarised from the answer as written — nothing added.
      {m.dropped ? ` ${m.dropped} sentence${m.dropped === 1 ? '' : 's'} the answer does not say ${m.dropped === 1 ? 'was' : 'were'} left out.` : ''}
    </div>
  )
}

/** what Make a table said, under the answer */
function TableNote({ turn }: { turn: Turn }) {
  const t = turn.table
  if (!t) return null
  if (t.status === 'making') return <div className="ask-status checking">Laying it out as a table…</div>
  if (t.status === 'stopped') return <div className="ask-note">Table stopped.</div>
  if (t.status === 'failed') return <div className="ask-error">{t.note || 'Could not make a table.'}</div>
  if (t.status === 'none') return <div className="ask-note">{t.note}</div>
  if (!t.shown) return null
  return (
    <div className="ask-note">
      Laid out from the answer as written — nothing added.
      {t.blanked ? ` ${t.blanked} cell${t.blanked === 1 ? '' : 's'} the answer does not say ${t.blanked === 1 ? 'was' : 'were'} left out.` : ''}
    </div>
  )
}

/** Double check (DESIGN.md 82): under an answer about a file that has a
 *  number in it - its numbers looked up again on their pages, its sums worked
 *  out again by the calculator. Asked for, never automatic: it takes a while. */
function CheckButton({ turn, index }: { turn: Turn; index: number }) {
  const busy = useAsk((s) => s.busy)
  const branch = useBranch()
  const c = turn.check
  if (c?.status === 'done') {
    return <span className="ask-check done"><Icon name="check" size={11} /> Double checked</span>
  }
  if (c?.status === 'checking') return null
  if (!hasNumbers(turn.check?.original ?? turn.text)) return null
  return (
    <button className="ask-check" disabled={busy || branch?.status !== 'ready'}
      onClick={() => void useAsk.getState().doubleCheck(index)}>
      <Icon name="check" size={11} /> {c?.status === 'failed' || c?.status === 'stopped' ? 'Double check again' : 'Double check numbers'}
    </button>
  )
}

/** what Double check found, under the answer */
function CheckBox({ turn }: { turn: Turn }) {
  const c = turn.check
  if (!c) return null
  if (c.status === 'checking') return <div className="ask-status checking">{c.doing ?? 'Double checking…'}</div>
  if (c.status === 'failed') return <div className="ask-error">Double check failed: {c.error}</div>
  if (c.status === 'stopped') return <div className="ask-note">Double check stopped.</div>
  const marks = c.marks ?? []
  if (!marks.length) return <div className="ask-note">{c.summary ?? 'No numbers to check.'}</div>
  const sign = { ok: '✓', fixed: '✎', unchecked: '?' } as const
  return (
    <div className="checked">
      <div className="checked-lead"><Icon name="check" size={11} /> Double checked — {c.summary}</div>
      {marks.map((m, i) => (
        <div key={i} className={`checked-row ${m.status}`}>
          <span className="checked-sign">{sign[m.status]}</span>
          {m.status === 'fixed' ? <><b>{m.text}</b>, not {m.was}</> : <b>{m.text}</b>}
          {' — '}{m.status === 'unchecked' ? `${m.note}; take it as unchecked` : m.note}
        </div>
      ))}
    </div>
  )
}

function Answer({ turn, offer, question, index }: {
  turn: Turn; offer?: (question: string) => void; question: string; index: number
}) {
  const [adding, setAdding] = useState(false)
  const [showThinking, setShowThinking] = useState(false)
  const branch = useBranch()
  const fromFile = turn.mode === 'file'
  // checked (DESIGN.md 82): its numbers drawn as checked, corrected or not checked
  const shown = turn.check?.status === 'done' && turn.check.marked ? turn.check.marked : turn.text
  const text = (turn.status === 'done' ? shown : hideNext(shown))
    .replace(BEYOND_MARK, BEYOND)
  const at = text.indexOf(BEYOND)
  let board = at >= 0 ? text.slice(0, at) : text
  let beyond = at >= 0 ? text.slice(at + BEYOND.length) : ''
  // Make a table (DESIGN.md 85): the table in place of the part it lays out
  const tbl = turn.table
  if (tbl?.status === 'done' && tbl.shown && tbl.table) {
    if (tbl.part === 'beyond') beyond = tbl.table
    else board = tbl.table
  }
  // Summarise (DESIGN.md 87): each part's summary in its place
  const sum = turn.summary
  if (sum?.status === 'done' && sum.shown) {
    board = sum.board ?? board
    beyond = sum.beyond ?? beyond
  }
  return (
    <div className="turn answer">
      {turn.warning ? <div className="ask-warn">{turn.warning}</div> : null}
      {turn.thinking ? (
        <button className="thinking-toggle" onClick={() => setShowThinking((v) => !v)}>
          {showThinking ? 'Hide' : 'Show'} its thinking
        </button>
      ) : null}
      <TaskSteps turn={turn} />
      {showThinking && turn.thinking ? <div className="thinking">{turn.thinking}</div> : null}
      {!turn.text && statusLine(turn)
        ? <div className="ask-status">{statusLine(turn)}</div> : null}
      {board.trim() ? (
        <div className={`voice board ${turn.mode === 'own' || turn.mode === 'explain' ? 'own' : ''}`}>
          {turn.mode === 'own'
            ? <div className="voice-head">Not from your board — the model's own knowledge</div>
            : turn.mode === 'explain'
              ? <div className="voice-head">Explain more — the model's own knowledge, not {branch ? 'this file' : 'your board'}</div> : null}
          <Rich text={board.trim()} page={fromFile ? pageLink(branch) : undefined} />
        </div>
      ) : null}
      {beyond.trim() ? (
        <div className="voice beyond">
          <div className="voice-head">{fromFile ? 'Beyond this file' : 'Beyond your board'} — the model's own knowledge</div>
          <Rich text={beyond.trim()} />
        </div>
      ) : null}
      {turn.status === 'writing' ? <span className="caret" /> : null}
      {turn.status === 'done' && !fromFile ? <><CitedPictures turn={turn} /><OfferedFiles turn={turn} /></> : null}
      {fromFile ? <><Calculated turn={turn} /><CheckBox turn={turn} /><PagesUsed turn={turn} branch={branch} /></> : null}
      <SummaryNote turn={turn} />
      <TableNote turn={turn} />
      {turn.error ? <div className="ask-error">{turn.error}</div> : null}
      {turn.status === 'stopped' ? <div className="ask-note">Stopped.</div> : null}
      {turn.mode === 'search' && turn.status && ['done', 'stopped'].includes(turn.status) ? (
        <div className="ask-note">
          Searched the board by {turn.how || 'wording'} — used the {turn.used} cards of
          {' '}{turn.total} that match best.
        </div>
      ) : null}
      {turn.leftOut && turn.status === 'done' ? (
        <div className="ask-note">
          {turn.leftOut} picture{turn.leftOut === 1 ? ' was' : 's were'} left out to keep the
          question small enough — add a picture's card to the context to include it.
        </div>
      ) : null}
      {turn.uncited && turn.status === 'done' ? (
        <div className="ask-warn">
          No card is cited in this answer, so nothing shows it comes from your board — it may be
          the model's own knowledge.
        </div>
      ) : null}
      {turn.dropped ? (
        <div className="ask-note">
          Removed {turn.dropped} citation{turn.dropped === 1 ? '' : 's'} of cards that were not
          in context.
        </div>
      ) : null}
      {turn.status === 'done' && turn.text.trim() && !turn.error ? (
        adding ? <AddToBoard question={question} turn={turn} onDone={() => setAdding(false)} />
          : <div className="ask-acts">
              <button className="ask-add" onClick={() => setAdding(true)}>
                <Icon name="plus" size={11} /> Add to board
              </button>
              <SummaryButton turn={turn} index={index} />
              <TableButton turn={turn} index={index} />
              {fromFile ? <CheckButton turn={turn} index={index} /> : null}
            </div>
      ) : null}
      {offer && turn.nexts?.length && turn.status === 'done' ? (
        <div className="ask-next">
          <span className="ask-next-lead">{turn.choices ? 'Your answer' : 'You could ask next'}</span>
          {turn.nexts.map((q, i) => (
            <button key={i} className="starter"
              onClick={() => offer(q)}>{q}</button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/** The conversation: a chat bar always at the bottom of the board, and above
 *  it, once a question is asked, the conversation - which can be expanded to
 *  fill the screen, where *Explain more* answers from the conversation alone
 *  (DESIGN 34). One place to ask, nothing else to learn. */
export function AskPanel() {
  const open = useAsk((s) => s.open)
  const expanded = useAsk((s) => s.expanded)
  const explain = useAsk((s) => s.explain)
  const own = useAsk((s) => s.own)
  const pinned = useAsk((s) => s.pinned)
  const branch = useBranch()
  const context = useAsk((s) => s.context)
  const turns = useAsk((s) => s.turns)
  const busy = useAsk((s) => s.busy)
  const side = useAsk((s) => s.side)
  // the bar rests as a small pill; under the pointer, focused, holding a draft,
  // answering, or with the conversation open, it is the full bar
  const [hot, setHot] = useState(false)
  const [focused, setFocused] = useState(false)
  const cool = useRef(0)
  // "Clear this conversation?", asked above the bar in Mindboard's own style
  const [clearing, setClearing] = useState(false)
  const think = useAsk((s) => s.think)
  const nodes = useStore((s) => s.nodes)
  const [draft, setDraft] = useState('')
  const pasted = usePasted()
  const awake = hot || focused || !!draft.trim() || busy || open || pasted.pictures.length > 0
  useEffect(() => () => window.clearTimeout(cool.current), [])
  const box = useRef<HTMLTextAreaElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const ask = useAsk.getState()

  /** Follow the answer as it is written - but only while the reader is at the
   *  bottom. Scrolling up lets go; scrolling back down picks it up again; a
   *  new question comes back down. */
  const following = useRef(true)
  useEffect(() => {
    const el = scroller.current
    if (el && following.current) el.scrollTop = el.scrollHeight
  }, [turns, open, expanded])
  // Any move upward lets go - wheel, scrollbar, touchpad or keys - and only
  // coming right back to the bottom picks the answer up again. Our own
  // scrolls only ever go down. Before, being within 80 px of the bottom took
  // it back, so a small scroll up was undone at once and the reader had to
  // wait for the answer to finish (DESIGN.md 87).
  const last = useRef({ top: 0, height: 0 })
  const onScroll = () => {
    const el = scroller.current
    if (!el) return
    // an answer that shrank moves the view up by itself: not the reader
    const up = el.scrollTop < last.current.top - 1 && el.scrollHeight >= last.current.height
    last.current = { top: el.scrollTop, height: el.scrollHeight }
    if (up) following.current = false
    else if (el.scrollHeight - el.scrollTop - el.clientHeight < 24) following.current = true
  }
  // Scrolling up lets go at once. Read from the scroll position alone it did
  // not: a smooth wheel scroll moves a few pixels at a time, the next words
  // arrived first and pulled the view back down, and it could never get away
  // while an answer was being written (DESIGN.md 68).
  const letGo = () => { following.current = false }
  const onWheel = (e: React.WheelEvent) => { if (e.deltaY < 0) letGo() }
  const onKeyScroll = (e: React.KeyboardEvent) => {
    if (['ArrowUp', 'PageUp', 'Home'].includes(e.key)) letGo()
  }

  // grow the box with what is typed, up to a few lines - and again whenever
  // its width changes: the bar opens from a small pill, and text measured
  // while it is narrow wraps into lines it no longer needs once it is wide
  //
  // A question that does not fit on one line beside the tools takes a row of
  // its own, the full width of the bar, with the tools under it (DESIGN.md 70)
  // - beside the chips it was squeezed into a column a few letters wide. The
  // test is the text's width against the room one row would leave, which is
  // the same whichever layout is showing, so the bar does not flip between
  // the two; it is taken again as the bar widens while it opens.
  const [tall, setTall] = useState(false)
  const ruler = useRef<CanvasRenderingContext2D | null>(null)
  const fit = () => {
    const el = box.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
    const text = el.value
    const bar = el.parentElement
    const tools = bar?.querySelector<HTMLElement>('.bar-tools')
    if (!text || !bar || !tools || !tools.offsetWidth) { setTall(false); return }
    if (text.includes('\n')) { setTall(true); return }
    ruler.current ??= document.createElement('canvas').getContext('2d')
    const r = ruler.current
    if (!r) return
    r.font = getComputedStyle(el).font
    // one row: the bar less its padding (11), the ankh (36), the tools and two gaps (16)
    const room = bar.clientWidth - 11 - 36 - tools.offsetWidth - 16
    setTall(r.measureText(text).width > room - 6)
  }
  useEffect(fit, [draft, tall])   // again after moving rows: the box's width changed
  useEffect(() => {
    const bar = box.current?.parentElement
    if (!bar || typeof ResizeObserver === 'undefined') return
    let width = bar.clientWidth
    const ro = new ResizeObserver(() => {
      if (bar.clientWidth !== width) { width = bar.clientWidth; fit() }
    })
    ro.observe(bar)
    return () => ro.disconnect()
  }, [])

  const present = context.filter((id) => nodes[id])
  const everything = Object.keys(nodes).length
  const canSend = !busy && (branch ? branch.status === 'ready' : everything > 0 || own)
  // a PDF, Word or Excel file dropped or pasted on the bar opens its own conversation
  const openFiles = (files: File[]) => {
    for (const f of files) void ask.openFile({ file: f, name: f.name })
  }
  const docsIn = (list: FileList | DataTransferItemList | undefined | null): File[] => {
    const out: File[] = []
    for (const item of Array.from((list ?? []) as ArrayLike<File | DataTransferItem>)) {
      const f = item instanceof File ? item : item.kind === 'file' ? item.getAsFile() : null
      if (f && askable(f.name)) out.push(f)
    }
    return out
  }
  const explaining = explain && !own

  const send = (text: string) => {
    if (!canSend || (!text.trim() && !pasted.pictures.length) || pasted.reading) return
    following.current = true
    void ask.send(text, pasted.pictures)
    setDraft('')
    pasted.clear()
  }
  const ready = canSend && !pasted.reading && (!!draft.trim() || pasted.pictures.length > 0)
  // Esc steps back one level: the expanded view, then the conversation
  const escape = (e: React.KeyboardEvent) => {
    if (e.key !== 'Escape') return
    e.stopPropagation()
    if (expanded) ask.setExpanded(false)
    else if (open) ask.setOpen(false)
    else box.current?.blur()
  }

  return (
    <>
      {open && expanded ? <div className="ask-dim" onClick={() => ask.setExpanded(false)} /> : null}
      {open ? (
        <aside className={`ask-sheet nodrag nopan ${expanded ? 'expanded' : ''} ${side === 'left' && !expanded ? 'left' : ''}`}
          aria-label="Ask your board" onKeyDown={escape}>
          <header className="ask-head">
            <span className="ask-title"><Icon name="ask" size={14} /> Ask your board</span>
            <span className="ask-head-actions">
              {!expanded ? (
                <button className={`icon-btn ${explain ? 'on' : ''}`} aria-pressed={explain}
                  aria-label="Explain more"
                  onClick={() => ask.setExplain(!explain)}>
                  <Icon name="explain" size={14} />
                </button>
              ) : null}
              <button className={`icon-btn pin-btn ${pinned ? 'on' : ''}`} aria-pressed={pinned}
                aria-label={pinned ? 'Unpin' : 'Pin open'}
                onClick={() => ask.setPinned(!pinned)}>
                <PinIcon on={pinned} />
              </button>
              <button className="icon-btn" aria-label="Clear the conversation" disabled={!turns.length}
                onClick={() => setClearing(true)}>
                <Icon name="trash" size={14} />
              </button>
              {!expanded ? (
                <button className="icon-btn" aria-label={side === 'left' ? 'Move to middle' : 'Move to left'}
                  onClick={() => ask.setSide(side === 'left' ? 'center' : 'left')}>
                  <Icon name={side === 'left' ? 'dock-center' : 'dock-left'} size={14} />
                </button>
              ) : null}
              <button className="icon-btn" aria-label={expanded ? 'Back to the board' : 'Expand the chat'}
                onClick={() => ask.setExpanded(!expanded)}>
                <Icon name={expanded ? 'shrink' : 'fit'} size={14} />
              </button>
              <button className="icon-btn" aria-label="Close" onClick={() => ask.setOpen(false)}>
                <Icon name="x" size={14} />
              </button>
            </span>
          </header>
          <FileTabs />

          {branch ? <FileStrip branch={branch} /> : <div className="ask-context">
            {own ? (
              <span className="ask-empty"><b>Board off:</b> answers come from the model's own
                knowledge — the board is not searched or sent. Switch it back on in the bar below.</span>
            ) : explaining ? (
              <span className="ask-empty"><b>Explain more:</b> answers come from the model's own
                knowledge and this conversation — nothing from the board is sent.</span>
            ) : present.length === 0 ? (
              <>
                <span className="ctx-chip whole">
                  <span className="ctx-name"><Icon name="grid" size={11} /> Whole board
                    <span className="ctx-count"> · {everything} cards</span></span>
                </span>
                <span className="ask-empty">
                  To narrow it, use <Icon name="askAdd" size={12} /> on a card, or select several
                  and press <b>Ask</b>.
                </span>
              </>
            ) : (
              <>
                {present.map((id) => {
                  const n = nodes[id]
                  const count = cardsInside(id)
                  return (
                    <span key={id} className="ctx-chip">
                      <button className="ctx-name" onClick={() => revealCard(id)}>
                        {n.type === 'meta' ? <Icon name="layers" size={11} /> : null}
                        {n.title || 'Untitled'}
                        {count > 1 ? <span className="ctx-count"> · {count} cards</span> : null}
                      </button>
                      <button className="ctx-x" aria-label="Take out of context" onClick={() => ask.remove(id)}>
                        <Icon name="x" size={10} />
                      </button>
                    </span>
                  )
                })}
                <button className="ctx-clear" onClick={() => ask.clear()}>Clear</button>
              </>
            )}
          </div>}

          <div className="ask-turns" ref={scroller} onScroll={onScroll} onWheel={onWheel}
            onTouchMove={letGo} onPointerDown={(e) => { if (e.target === e.currentTarget) letGo() }}
            onKeyDown={onKeyScroll} tabIndex={-1}>
            {turns.length === 0 && branch ? (
              <div className="ask-hello">
                {branch.status === 'ready'
                  ? <>Ask anything about <b>{branch.name}</b>. Each question searches the file and sends
                      only its best-matching passages; a page that is mostly a figure, a table or
                      formulas goes as a picture of the page. Answers say which page they come from.</>
                  : branch.status === 'preparing' ? <>You can ask as soon as it is prepared.</> : null}
              </div>
            ) : turns.length === 0 ? (
              <div className="ask-hello">
                Ask anything about {present.length ? 'the cards in context' : 'this board'}. What
                comes from your cards is linked to them; anything the model adds from its own
                knowledge is kept apart.
                {everything > 0 ? (
                  <div><button className="starter" onClick={() => send(STARTER)}>{STARTER}</button></div>
                ) : null}
              </div>
            ) : turns.map((t, i) => (
              t.role === 'user'
                ? <div key={i} className="turn question"><AskedWith thumbs={t.images} />{t.text}</div>
                // only the latest answer offers its next question
                : <Answer key={i} index={i} turn={t} question={turns[i - 1]?.text ?? ''}
                    offer={i === turns.length - 1 && canSend ? send : undefined} />
            ))}
          </div>
        </aside>
      ) : null}

      {clearing ? (
        <div className="ask-confirm nodrag nopan" role="alertdialog" aria-label="Clear this conversation?"
          onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setClearing(false) } }}>
          <Icon name="trash" size={15} />
          <div className="ask-confirm-words">
            <b>Clear this conversation?</b>
            <span>Its questions and answers are removed. Your cards are not touched.</span>
          </div>
          <button className="ask-confirm-keep" onClick={() => setClearing(false)}>Keep</button>
          <button className="ask-confirm-go" autoFocus onClick={() => { ask.forget(); setClearing(false) }}>Clear</button>
        </div>
      ) : null}

      {/* the one way in: always at the bottom of the board. At rest a small
          "Ask me" pill; it opens to the full bar under the pointer, while you
          type, and while a conversation or an answer is going */}
      <div className={`ask-bar nodrag nopan ${awake ? 'awake' : 'rest'} ${expanded && open ? 'expanded' : ''} ${busy ? 'busy' : ''} ${pasted.pictures.length || pasted.reading ? 'has-pictures' : ''} ${tall && awake ? 'tall' : ''}`}
        onKeyDown={escape}
        onMouseEnter={() => { window.clearTimeout(cool.current); setHot(true) }}
        onMouseLeave={() => { cool.current = window.setTimeout(() => setHot(false), 160) }}
        onClick={() => box.current?.focus()}
        onDragOver={pasted.onDragOver}
        onDrop={(e) => {
          const docs = docsIn(e.dataTransfer?.files)
          if (docs.length) { e.preventDefault(); e.stopPropagation(); openFiles(docs) } else pasted.onDrop(e)
        }}>
        <PastedStrip pictures={pasted.pictures} reading={pasted.reading} remove={pasted.remove} />
        <span className="bar-mark" aria-hidden><Icon name="ankh" size={15} /></span>
        <textarea ref={box} rows={1} value={draft} aria-label="Ask your board"
          placeholder={!awake ? (everything ? 'Ask me' : 'No cards yet')
            : branch ? (branch.status === 'ready' ? `Ask ${branch.name}…`
              : branch.status === 'preparing' ? `Preparing ${branch.name}…` : 'This file is not ready')
            : own ? 'Ask without your board…'
            : explaining ? 'Ask for an explanation…'
            : present.length ? 'Ask about these cards…'
            : everything ? 'Ask your board anything…' : 'This board has no cards yet'}
          disabled={!branch && everything === 0 && !own}
          onFocus={() => { setFocused(true); if (turns.length && !open) ask.setOpen(true) }}
          onBlur={() => setFocused(false)}
          onChange={(e) => setDraft(e.target.value)}
          onPaste={(e) => {
            const docs = docsIn(e.clipboardData?.items)
            if (docs.length) { e.preventDefault(); openFiles(docs) } else pasted.onPaste(e)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(draft) }
          }} />
        <div className="bar-tools">
          <PickPicture add={pasted.add} className="bar-chip icon-only" size={12} />
          {expanded && open ? (
            <label className={`bar-chip ${explain ? 'on' : ''}`}>
              <input type="checkbox" checked={explain} onChange={(e) => ask.setExplain(e.target.checked)} />
              <Icon name="bullets" size={12} /> Explain more
            </label>
          ) : null}
          {!branch ? <label className={`bar-chip own-chip ${own ? 'on' : ''}`}>
            <input type="checkbox" checked={own} onChange={(e) => ask.setOwn(e.target.checked)} />
            <Icon name="grid" size={12} /> {own ? 'Board off' : 'Board on'}
          </label> : null}
          <label className={`bar-chip ${think ? 'on' : ''}`}>
            <input type="checkbox" checked={think} onChange={(e) => ask.setThink(e.target.checked)} />
            <Icon name="bulb" size={12} /> Think deeper
          </label>
          {busy ? (
            <button className="send stop" aria-label="Stop" onClick={() => ask.stop()}>
              <Icon name="stop" size={13} />
            </button>
          ) : (
            <button className={`send ${ready ? 'ready' : ''}`} aria-label="Ask"
              disabled={!ready} onClick={() => send(draft)}>
              <Icon name="send" size={14} />
            </button>
          )}
        </div>
      </div>
    </>
  )
}
