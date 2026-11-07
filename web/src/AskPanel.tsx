/** Ask your board: the cards in context, and a conversation about exactly
 *  those cards with the local model (DESIGN.md 25).
 *
 *  It appears when used and folds away otherwise - not a menu, the place a
 *  question about your own cards goes. Everything it holds is screen state;
 *  nothing here writes to the board.
 */
import { Fragment, useEffect, useRef, useState } from 'react'

import { cardsInside, revealCard, useAsk, type Turn } from './askStore'
import { Icon } from './icons'
import { useStore } from './store'

const BEYOND = 'Beyond your board:'
const STARTER = 'What am I missing here?'

/** The model ends an answer with "NEXT: <a question>"; the server takes that
 *  line off when the answer is done and sends the question on its own. While
 *  the answer is still being written, the line - or the start of it - is kept
 *  out of sight here. */
const NEXT_LINE = /(^|\n)[ \t*_>#-]*(NEXT:[\s\S]*|N(E(X(T)?)?)?)$/
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

/** Plain text with the model's light formatting: **bold**, list lines, and
 *  [[card]] citations turned into chips. Nothing else is interpreted - an
 *  answer is never HTML. */
function Rich({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <>
      {lines.map((line, i) => (
        <Fragment key={i}>
          {line.split(/(\[\[[0-9A-Za-z]{26}\]\]|\*\*[^*]+\*\*)/g).map((piece, j) => {
            const cite = piece.match(/^\[\[([0-9A-Za-z]{26})\]\]$/)
            if (cite) return <Cite key={j} id={cite[1]} />
            const bold = piece.match(/^\*\*([^*]+)\*\*$/)
            if (bold) return <strong key={j}>{bold[1]}</strong>
            return <Fragment key={j}>{piece}</Fragment>
          })}
          {i < lines.length - 1 ? '\n' : null}
        </Fragment>
      ))}
    </>
  )
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

function Answer({ turn, offer }: { turn: Turn; offer?: (question: string) => void }) {
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
      {offer && turn.next && turn.status === 'done' ? (
        <div className="ask-next">
          <span className="ask-next-lead">Would you like to know</span>
          <button className="starter" title="Ask this" onClick={() => offer(turn.next!)}>
            {turn.next}
          </button>
        </div>
      ) : null}
    </div>
  )
}

export function AskPanel() {
  const open = useAsk((s) => s.open)
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
   *  bottom. It used to jump to the end on every word, so nobody could scroll
   *  up to read the start of an answer still being written. Scrolling up lets
   *  go; scrolling back down picks it up again; a new question comes back down. */
  const following = useRef(true)
  useEffect(() => {
    const el = scroller.current
    if (el && following.current) el.scrollTop = el.scrollHeight
  }, [turns])
  const onScroll = () => {
    const el = scroller.current
    if (el) following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }

  // grow the box with what is typed, up to a few lines
  useEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`
  }, [draft])

  if (!open) return null
  const present = context.filter((id) => nodes[id])
  const everything = Object.keys(nodes).length
  const canSend = !busy && everything > 0

  const send = (text: string) => {
    if (!canSend || !text.trim()) return
    following.current = true
    void ask.send(text)
    setDraft('')
  }

  return (
    <aside className="ask-panel nodrag nopan" aria-label="Ask your board"
      onKeyDown={(e) => {
        // Esc closes the panel and nothing else - it must not also walk out of
        // the group the canvas is showing
        if (e.key === 'Escape') { e.stopPropagation(); ask.setOpen(false) }
      }}>
      <header className="ask-head">
        <span className="ask-title"><Icon name="ask" size={14} /> Ask your board</span>
        <button className="icon-btn" title="Close  (Esc)" onClick={() => ask.setOpen(false)}>
          <Icon name="x" size={14} />
        </button>
      </header>

      <div className="ask-context">
        {present.length === 0 ? (
          <>
            {/* nothing added: the question is about everything */}
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
          </div>
        ) : turns.map((t, i) => (
          t.role === 'user'
            ? <div key={i} className="turn question">{t.text}</div>
            // only the latest answer offers its next question - older offers
            // belong to a conversation that has moved on
            : <Answer key={i} turn={t}
                offer={i === turns.length - 1 && canSend ? send : undefined} />
        ))}
      </div>

      <div className="ask-compose">
        {turns.length === 0 && everything > 0 ? (
          <button className="starter" onClick={() => send(STARTER)}>{STARTER}</button>
        ) : null}
        <div className="ask-input">
          <textarea ref={box} rows={1} value={draft}
            placeholder={present.length ? 'Ask about these cards…'
              : everything ? 'Ask about the whole board…' : 'This board has no cards yet'}
            disabled={everything === 0}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(draft) }
            }} />
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
        <label className="think-toggle" title="Slower, and deeper - worth it for what is missing">
          <input type="checkbox" checked={think} onChange={(e) => ask.setThink(e.target.checked)} />
          Think deeper
        </label>
      </div>
    </aside>
  )
}
