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
import { AnswerPictures } from './AnswerPictures'
import { cardsInside, revealCard, useAsk, type Turn } from './askStore'
import { Icon } from './icons'
import { Prose } from './Prose'
import { useStore } from './store'
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
    <button className="cite" title="Go to this card" onClick={() => revealCard(id)}>
      {node ? (node.title || 'Untitled') : 'a card no longer here'}
    </button>
  )
}

/** An answer laid out (Prose): headings, lists with their depth, bold, and
 *  [[card]] citations as chips. Nothing is ever interpreted as HTML. */
function Rich({ text }: { text: string }) {
  return <Prose text={text} cite={(id) => <Cite id={id} />} />
}

/** The pictures an answer cites, shown under it (DESIGN.md 48) - from the
 *  board's own cards, which this page already has. */
function CitedPictures({ turn }: { turn: Turn }) {
  const nodes = useStore((s) => s.nodes)
  const boardId = useStore((s) => s.boardId)
  if (!boardId) return null
  const pictures = (turn.cited ?? []).flatMap((id) => {
    const n = nodes[id]
    const sha = !n ? undefined : (n.type === 'image' ? n.content.sha256 : n.type === 'video' ? n.content.poster : undefined)
    return typeof sha === 'string' ? [{ card: id, board: boardId, sha, title: n.title || 'Untitled' }] : []
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
  return <AnswerFiles files={files} onShow={(f) => revealCard(f.card)} />
}

const fmt = (n: number) => n.toLocaleString()

/** What the model is doing before its first word - said precisely, because on
 *  a shared graphics card reading a big context can take minutes, and "loading"
 *  for all of it looked like a hang. */
function statusLine(t: Turn): string | null {
  if (t.status === 'loading') return 'Starting the model…'
  if (t.status === 'thinking') return 'Thinking…'
  if (t.status !== 'reading') return null
  if (t.mode === undefined) return 'Looking through your cards…'   // before the cards are chosen
  const what = t.mode === 'search' ? `the ${t.used} best-matching cards` : 'your cards'
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

/** Add to board: the model drafts a card from the answer - the knowledge, not
 *  the chat - and proposes the card it belongs with. Nothing changes until
 *  Add: then one undoable step through the op-log, marked as made by AI, and
 *  the background indexer makes it searchable (DESIGN 34). */
function AddToBoard({ question, turn, onDone }: { question: string; turn: Turn; onDone: () => void }) {
  const boardId = useStore((s) => s.boardId)
  const nodes = useStore((s) => s.nodes)
  const [draft, setDraft] = useState<{ title: string; text: string; link_to: string; kind: string } | null>(null)
  const [error, setError] = useState('')
  const cited = (turn.cited ?? []).filter((id) => nodes[id])

  useEffect(() => {
    if (!boardId) return
    api.askDraft(boardId, { question, answer: turn.text, cited })
      .then(setDraft).catch((e) => setError(e.message || 'could not draft the card'))
    // drafted once, when opened
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const add = () => {
    if (!draft || !draft.title.trim()) return
    const store = useStore.getState()
    const target = draft.link_to ? store.nodes[draft.link_to] : undefined
    // beside the card it belongs with, in its group; else on the level in view
    const pos: [number, number] = target
      ? [target.pos[0] + (target.size[0] ?? 240) + 90, target.pos[1] + 30]
      : [0, 0]
    const parent = target ? target.parent : (store.path[store.path.length - 1] ?? null)
    const card = newNode({ pos, parent, title: draft.title.trim(),
                           content: { text: draft.text.trim() }, provenance: 'ai' })
    const ops: Op[] = [{ kind: 'node.create', node: card }]
    if (target) {
      ops.push({ kind: 'edge.create',
                 edge: newEdge(card.id, target.id, { kind: draft.kind as MEdge['kind'], provenance: 'ai' }) })
    }
    store.run(ops)
    store.say('Added to your board')
    onDone()
    revealCard(card.id)
  }

  if (error) return <div className="ask-error">{error}</div>
  if (!draft) return <div className="ask-note">Drafting a card from this answer…</div>
  return (
    <div className="ask-draft">
      <input className="draft-title" value={draft.title} placeholder="Title"
        onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
      <textarea className="draft-text" rows={3} value={draft.text}
        onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
      <div className="draft-link">
        {cited.length ? (
          <>
            <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
              {Object.entries(KIND_WORDS).map(([k, w]) => <option key={k} value={k}>{w}</option>)}
            </select>
            <select value={draft.link_to} onChange={(e) => setDraft({ ...draft, link_to: e.target.value })}>
              {cited.map((id) => <option key={id} value={id}>{nodes[id].title || 'Untitled'}</option>)}
              <option value="">no card - on its own</option>
            </select>
          </>
        ) : <span>On its own, on the level you are looking at</span>}
      </div>
      <div className="draft-actions">
        <button className="starter" onClick={add} disabled={!draft.title.trim()}>Add</button>
        <button className="ctx-clear" onClick={onDone}>Cancel</button>
      </div>
    </div>
  )
}

function Answer({ turn, offer, question }: {
  turn: Turn; offer?: (question: string) => void; question: string
}) {
  const [adding, setAdding] = useState(false)
  const [showThinking, setShowThinking] = useState(false)
  const text = (turn.status === 'done' ? turn.text : hideNext(turn.text))
    .replace(BEYOND_MARK, BEYOND)
  const at = text.indexOf(BEYOND)
  const board = at >= 0 ? text.slice(0, at) : text
  const beyond = at >= 0 ? text.slice(at + BEYOND.length) : ''
  return (
    <div className="turn answer">
      {turn.warning ? <div className="ask-warn">{turn.warning}</div> : null}
      {turn.thinking ? (
        <button className="thinking-toggle" onClick={() => setShowThinking((v) => !v)}>
          {showThinking ? 'Hide' : 'Show'} its thinking
        </button>
      ) : null}
      {showThinking && turn.thinking ? <div className="thinking">{turn.thinking}</div> : null}
      {!turn.text && statusLine(turn)
        ? <div className="ask-status">{statusLine(turn)}</div> : null}
      {board.trim() ? <div className="voice board"><Rich text={board.trim()} /></div> : null}
      {beyond.trim() ? (
        <div className="voice beyond">
          <div className="voice-head">Beyond your board — the model's own knowledge</div>
          <Rich text={beyond.trim()} />
        </div>
      ) : null}
      {turn.status === 'writing' ? <span className="caret" /> : null}
      {turn.status === 'done' ? <><CitedPictures turn={turn} /><OfferedFiles turn={turn} /></> : null}
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
      {turn.dropped ? (
        <div className="ask-note">
          Removed {turn.dropped} citation{turn.dropped === 1 ? '' : 's'} of cards that were not
          in context.
        </div>
      ) : null}
      {turn.mode === 'explain' && turn.status === 'done' ? (
        <div className="ask-note">Explained from this conversation — the board was not searched.</div>
      ) : null}
      {turn.status === 'done' && turn.text.trim() && !turn.error ? (
        adding ? <AddToBoard question={question} turn={turn} onDone={() => setAdding(false)} />
          : <button className="ask-add" onClick={() => setAdding(true)}>
              <Icon name="plus" size={11} /> Add to board
            </button>
      ) : null}
      {offer && turn.nexts?.length && turn.status === 'done' ? (
        <div className="ask-next">
          <span className="ask-next-lead">You could ask next</span>
          {turn.nexts.map((q, i) => (
            <button key={i} className="starter" title="Ask this" onClick={() => offer(q)}>{q}</button>
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
  const context = useAsk((s) => s.context)
  const turns = useAsk((s) => s.turns)
  const busy = useAsk((s) => s.busy)
  const think = useAsk((s) => s.think)
  const nodes = useStore((s) => s.nodes)
  const [draft, setDraft] = useState('')
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
  const onScroll = () => {
    const el = scroller.current
    if (el) following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }

  // grow the box with what is typed, up to a few lines
  useEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [draft])

  const present = context.filter((id) => nodes[id])
  const everything = Object.keys(nodes).length
  const canSend = !busy && everything > 0
  const explaining = expanded && explain

  const send = (text: string) => {
    if (!canSend || !text.trim()) return
    following.current = true
    void ask.send(text)
    setDraft('')
  }
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
        <aside className={`ask-sheet nodrag nopan ${expanded ? 'expanded' : ''}`}
          aria-label="Ask your board" onKeyDown={escape}>
          <header className="ask-head">
            <span className="ask-title"><Icon name="ask" size={14} /> Ask your board</span>
            <span className="ask-head-actions">
              <button className="icon-btn" title={expanded ? 'Back to the board' : 'Expand the chat'}
                onClick={() => ask.setExpanded(!expanded)}>
                <Icon name={expanded ? 'shrink' : 'fit'} size={14} />
              </button>
              <button className="icon-btn" title="Close  (Esc)" onClick={() => ask.setOpen(false)}>
                <Icon name="x" size={14} />
              </button>
            </span>
          </header>

          <div className="ask-context">
            {explaining ? (
              <span className="ask-empty">Explain more is on: answers come from this conversation,
                and the board is not searched.</span>
            ) : present.length === 0 ? (
              <>
                <span className="ctx-chip whole" title="Every card on this board">
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
                    <span key={id} className="ctx-chip" title={n.title || 'Untitled'}>
                      <button className="ctx-name" onClick={() => revealCard(id)}>
                        {n.type === 'meta' ? <Icon name="layers" size={11} /> : null}
                        {n.title || 'Untitled'}
                        {count > 1 ? <span className="ctx-count"> · {count} cards</span> : null}
                      </button>
                      <button className="ctx-x" title="Take out of context" onClick={() => ask.remove(id)}>
                        <Icon name="x" size={10} />
                      </button>
                    </span>
                  )
                })}
                <button className="ctx-clear" onClick={() => ask.clear()}>Clear</button>
              </>
            )}
          </div>

          <div className="ask-turns" ref={scroller} onScroll={onScroll}>
            {turns.length === 0 ? (
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
                ? <div key={i} className="turn question">{t.text}</div>
                // only the latest answer offers its next question
                : <Answer key={i} turn={t} question={turns[i - 1]?.text ?? ''}
                    offer={i === turns.length - 1 && canSend ? send : undefined} />
            ))}
          </div>
        </aside>
      ) : null}

      {/* the one way in: always at the bottom of the board */}
      <div className={`ask-bar nodrag nopan ${expanded && open ? 'expanded' : ''} ${busy ? 'busy' : ''}`}
        onKeyDown={escape}>
        <textarea ref={box} rows={1} value={draft}
          placeholder={explaining ? 'Ask for an explanation…'
            : present.length ? 'Ask about these cards…'
            : everything ? 'Ask your board…' : 'This board has no cards yet'}
          disabled={everything === 0}
          onFocus={() => { if (turns.length && !open) ask.setOpen(true) }}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(draft) }
          }} />
        {expanded && open ? (
          <label className="bar-toggle"
            title="Answer from this conversation, with examples, without searching the board">
            <input type="checkbox" checked={explain} onChange={(e) => ask.setExplain(e.target.checked)} />
            Explain more
          </label>
        ) : null}
        <label className="bar-toggle" title="Slower, and deeper - worth it for what is missing">
          <input type="checkbox" checked={think} onChange={(e) => ask.setThink(e.target.checked)} />
          Think deeper
        </label>
        {busy ? (
          <button className="send stop" title="Stop" onClick={() => ask.stop()}>
            <Icon name="stop" size={13} />
          </button>
        ) : (
          <button className="send" title="Ask  (Enter)" disabled={!canSend || !draft.trim()}
            onClick={() => send(draft)}>
            <Icon name="send" size={14} />
          </button>
        )}
      </div>
    </>
  )
}
