import { Handle, NodeResizer, NodeToolbar, Position, useStore as useFlow, type NodeProps } from '@xyflow/react'
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import {
  columnSpots, connect, deleteNodes, linkifyNode, refreshLinkPreview, setStyle, spark, updateNode,
} from './actions'
import { api } from './api'
import { askable, revealCard, useAsk } from './askStore'
import { Icon } from './icons'
import { GraphMini, toMini, type MiniEdge } from './Mini'
import { useStore } from './store'
import { continueList, URL_ONLY } from './text'
import { Prose } from './Prose'
import { withMath } from './Math'
import { NODE_COLOURS } from './theme'
import { newEdge, newNode, type MNode, type NodeTone, type Op } from './types'
import { useUI } from './ui'

const PORTS: [string, Position][] = [
  ['t', Position.Top], ['r', Position.Right], ['b', Position.Bottom], ['l', Position.Left],
]

const TYPE_VAR: Record<string, string> = {
  text: 'var(--mb-type-text)', image: 'var(--mb-type-image)', video: 'var(--mb-type-video)',
  link: 'var(--mb-type-link)', file: 'var(--mb-type-file)', meta: 'var(--mb-type-meta)',
}

/** Under this much pointer travel, a press on a port was a click, not a drag.
 *  The canvas uses the same number - keep them in step. */
export const CLICK_SLOP = 6

const accentOf = (n: MNode) =>
  // a link to a video is a video, and reads better in the video colour
  n.style.color || (n.type === 'link' && n.content.embed ? TYPE_VAR.video : TYPE_VAR[n.type])
const sizeOf = (n: MNode) => n.style.fontSize ?? 13

/** Meta-nodes used to keep their description under `summary`; read both. */
const bodyText = (n: MNode): string => n.content.text ?? n.content.summary ?? ''

/** A port is both a drag handle and a button.
 *
 *  Dragging from it is xyflow's own connection gesture. A *click* - press and
 *  release without travelling - starts click-to-click linking instead. We
 *  detect that here rather than in onConnectEnd, because xyflow does not
 *  reliably report a connection that never moved. The canvas applies the same
 *  travel threshold so a click never also drops a node.
 */
function Ports({ nodeId }: { nodeId: string }) {
  const setUI = useUI((s) => s.set)
  const down = useRef<{ x: number; y: number } | null>(null)

  return (
    <>
      {PORTS.map(([id, position]) => (
        <Handle key={id} id={id} type="source" position={position}
          title="Connect"
          onPointerDown={(e: React.PointerEvent) => {
            down.current = { x: e.clientX, y: e.clientY }
          }}
          onPointerUp={(e: React.PointerEvent) => {
            const start = down.current
            down.current = null
            if (!start) return
            if (Math.hypot(e.clientX - start.x, e.clientY - start.y) < CLICK_SLOP) {
              setUI('linking', { sourceId: nodeId })
            }
          }} />
      ))}
    </>
  )
}

// --------------------------------------------------------------- editing -- //

function useAutoHeight(value: string, enabled: boolean) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el || !enabled) return
    el.style.height = '0px'
    el.style.height = `${el.scrollHeight}px`
  }, [value, enabled])
  return ref
}

/** The editable title + body every node type shares, so an image or a video
 *  carries a thought the same way a text node does. */
function NodeText({ node, placeholder = 'Untitled', bodyPlaceholder = 'Add a note...' }: {
  node: MNode; placeholder?: string; bodyPlaceholder?: string
}) {
  const editing = useUI((s) => s.editingId === node.id)
  const setUI = useUI((s) => s.set)
  const [title, setTitle] = useState(node.title)
  const [text, setText] = useState<string>(bodyText(node))
  const area = useAutoHeight(text, editing)
  const titleRef = useRef<HTMLInputElement>(null)
  const idle = useRef<ReturnType<typeof setTimeout>>()
  /** Once a node is on its way to becoming a link, this edit is over. Without
   *  this the debounced auto-save fires after the conversion lands and writes
   *  the raw address back over the link it just became. */
  const converting = useRef(false)

  useEffect(() => {
    if (!editing) { setTitle(node.title); setText(bodyText(node)) }
  }, [node.title, node.content.text, node.content.summary, editing])

  useEffect(() => {
    if (editing) { converting.current = false; titleRef.current?.focus() }
  }, [editing])

  const commit = () => {
    clearTimeout(idle.current)
    if (converting.current) return
    // A text node whose whole body is a link is a link - fetch it rather than
    // leaving the address sitting there as prose.
    if (node.type === 'text' && URL_ONLY.test(text.trim())) {
      converting.current = true
      void linkifyNode(node.id, text.trim())
      return
    }
    if (title !== node.title || text !== bodyText(node)) {
      // meta-nodes used to store this under `summary`; migrate on first edit
      const { summary: _dropped, ...rest } = node.content as Record<string, unknown>
      updateNode(node.id, { title, content: { ...rest, text } })
    }
  }

  /** Pasting a bare link into an empty node makes the link, not its address. */
  const onPasteUrl = (e: React.ClipboardEvent) => {
    if (node.type !== 'text' || node.title || bodyText(node)) return
    const dropped = e.clipboardData.getData('text/plain').trim()
    if (!URL_ONLY.test(dropped)) return
    e.preventDefault()
    clearTimeout(idle.current)
    converting.current = true
    setUI('editingId', null)
    void linkifyNode(node.id, dropped)
  }
  const touch = () => { clearTimeout(idle.current); idle.current = setTimeout(commit, 1500) }
  const fs = sizeOf(node)

  if (editing) {
    return (
      <div className="body">
        <input ref={titleRef} className="title-input nodrag" value={title} placeholder={placeholder}
          style={{ fontSize: fs + 1 }}
          onPaste={onPasteUrl}
          onChange={(e) => { setTitle(e.target.value); touch() }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); area.current?.focus() }
            if (e.key === 'Escape') { commit(); setUI('editingId', null) }
          }} />
        <textarea ref={area} className="nodrag" value={text} rows={1} placeholder={bodyPlaceholder}
          style={{ fontSize: fs }}
          onPaste={onPasteUrl}
          onChange={(e) => { setText(e.target.value); touch() }}
          onKeyDown={(e) => {
            if (e.key === 'Escape' || (e.key === 'Enter' && (e.metaKey || e.ctrlKey))) {
              e.preventDefault(); commit(); setUI('editingId', null)
              return
            }
            if (e.key === 'Enter' && !e.shiftKey) {
              // carry the list on to the next line, the way any editor does
              const el = e.currentTarget
              const next = continueList(el.value, el.selectionStart ?? el.value.length)
              if (next) {
                e.preventDefault()
                // Put the text and the caret in place synchronously. Deferring
                // the caret to a frame later loses characters when someone
                // types quickly through the line break.
                el.value = next.value
                el.setSelectionRange(next.caret, next.caret)
                setText(next.value)
                touch()
              }
            }
          }}
          onBlur={() => { commit(); setUI('editingId', null) }} />
      </div>
    )
  }

  // double-click is handled once, by the canvas, so it works over the picture
  // in an image node as readily as over the caption
  const text_ = bodyText(node)
  return (
    <div className="body" style={{ textAlign: node.style.align }}>
      <div className="title" style={{ fontSize: fs + 1 }}>
        {node.title ? withMath(node.title) : <span className="placeholder">{placeholder}</span>}
      </div>
      {text_
        // markdown, as in the chat's answers (DESIGN.md 69): headings, bold,
        // italics, strikethrough, code, lists and tasks, quotes, tables,
        // links and formulas - with the line breaks as written
        ? <div className="text" style={{ fontSize: fs }}>
            <Prose text={text_} cite={cardRef} breaks className="card-prose" />
          </div>
        : <div className="text placeholder hint-on-hover" style={{ fontSize: fs }}>
            {bodyPlaceholder}
          </div>}
    </div>
  )
}

// ----------------------------------------------------------------- shell -- //

const TONES: { key: NodeTone; label: string }[] = [
  { key: 'accent', label: 'Accent bar' },
  { key: 'tinted', label: 'Tinted card' },
  { key: 'solid', label: 'Solid card' },
]


/** Text sizes by name: the menu offers these four; a card keeps any size it
 *  already has, and the nearest name is marked. */
const NAMED_SIZES: { label: string; size: number }[] = [
  { label: 'S', size: 11 }, { label: 'M', size: 13 }, { label: 'L', size: 16 }, { label: 'XL', size: 20 },
]

/** A menu opens above its bar, so the card stays in view - unless the window's
 *  top would cut it off; then below. Checked again whenever it changes size:
 *  a panel grows after it opens, as its answer or question arrives. */
function useFlip(ref: React.RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    const m = ref.current
    if (!m) return
    const check = () => {
      m.classList.remove('below')
      if (m.getBoundingClientRect().top < 8) m.classList.add('below')
    }
    check()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(check)
    ro.observe(m)
    return () => ro.disconnect()
  })
}

type Menu = 'colour' | 'text' | 'ai'

/** What the chat is asked, in words the person sees in it (DESIGN.md 72). */
/** A card's AI actions through the chat (DESIGN.md 75): the task, known, so
 *  the request is not read first; a short line the chat shows; what it says
 *  while it works; and the request itself - short, because the task's shape
 *  (tasks.SHAPES) carries the rules. */
type Act = 'summarize' | 'explain' | 'quiz' | 'funfact'
const ACT: Record<Act, { label: string; doing: string; ask: (what: string, many: boolean) => string }> = {
  summarize: { label: 'Summarise', doing: 'Summarising…',
    ask: (w, m) => `Summarise ${w}${m ? ' together' : ''}.` },
  explain: { label: 'Explain', doing: 'Explaining…',
    ask: (w) => `Explain ${w} in plain words, with an example or two.` },
  quiz: { label: 'Quiz me', doing: 'Making your quiz…',
    ask: (w, m) => `Quiz me on ${w} - ${m ? 'four' : 'three'} questions, one at a time.` },
  funfact: { label: 'Fun fact', doing: 'Finding a fun fact…',
    ask: (w) => `One fun fact from ${w}.` },
}

/** The words that name these cards to the model: their titles - "this card"
 *  alone was read as whichever card a conversation was last about (DESIGN.md 72). */
function nameCards(ids: string[]): { what: string; many: boolean; first: string } {
  const nodes = useStore.getState().nodes
  const titles = ids.map((i) => (nodes[i]?.title || '').trim().slice(0, 80)).filter(Boolean)
  const many = ids.length > 1
  const what = many
    ? (titles.length ? `these cards: ${titles.map((t) => `"${t}"`).join(', ')}` : 'these cards')
    : (titles[0] ? `the card "${titles[0]}"` : 'this card')
  return { what, many, first: titles[0] || 'this card' }
}

const ANSWER_HEAD: Record<Exclude<Act, 'quiz'>, string> = {
  summarize: 'Summary', explain: 'Explanation', funfact: 'Fun fact',
}
const CITE_MARK = /\[\[[0-9A-Za-z]{26}\]\]/g

/** A card action's answer, in a panel beside the card - not in the chat,
 *  which is for the person's own questions (DESIGN.md 77). Asked through the
 *  same route as the chat, with its task known (§75), and nothing kept in
 *  the conversation. "Add to board" makes it a card, linked to the first. */
function AiAnswer({ ids, act, back }: { ids: string[]; act: Exclude<Act, 'quiz'>; back: () => void }) {
  const boardId = useStore((s) => s.boardId)
  const [text, setText] = useState('')
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')
  const [added, setAdded] = useState(false)
  useEffect(() => {
    if (!boardId) return
    const controller = new AbortController()
    const { what, many } = nameCards(ids)
    let got = ''
    void api.ask(boardId, { question: ACT[act].ask(what, many), cards: ids, history: [], think: false,
                            keep: [], explain: false, images: [], own: false, task: act },
      (name, data) => {
        if (name === 'text') { got += data.text; setText(got) }
        else if (name === 'done') { setText(data.answer ?? got); setDone(true) }
        else if (name === 'error') { setError(data.message || data.detail || 'Could not answer') }
      }, controller.signal)
      .catch((e: any) => { if (!controller.signal.aborted) setError(e.message || 'Could not answer') })
      .finally(() => setDone(true))
    // closed or gone back: hang up, and the model stops
    return () => { controller.abort(); void api.askStop(boardId).catch(() => {}) }
  }, [boardId, act, ids.join()])

  const shown = text.replace(/\n*NEXT:[\s\S]*$/, '').trim()
  const addCard = () => {
    const store = useStore.getState()
    const from = store.nodes[ids[0]]
    if (!from || !shown) return
    const [pos] = columnSpots(from, 1)
    const card = newNode({ pos, parent: from.parent, type: 'text', provenance: 'ai',
                           title: `${ANSWER_HEAD[act]}: ${nameCards(ids).first}`.slice(0, 80),
                           content: { text: shown.replace(CITE_MARK, '').replace(/[ \t]+([.,;:])/g, '$1').trim() } })
    const edge = newEdge(card.id, from.id, { kind: 'relates' })
    store.run([{ kind: 'node.create', node: card }, { kind: 'edge.create', edge }])
    spark(card.id, edge.id)
    setAdded(true)
  }
  return (
    <div className="ai-panel">
      <div className="ai-panel-head">
        <button className="ai-back" onClick={back} aria-label="Back"><Icon name="undo" size={14} /></button>
        <b>{ANSWER_HEAD[act]}</b>
        {!done && <span className="tools-spin" />}
      </div>
      <div className="ai-panel-body nowheel">
        {error ? <div className="tools-note">{error}</div>
          : shown ? <Prose text={shown} cite={cardRef} className="ai-prose" />
          : <div className="tools-wait">{ACT[act].doing}</div>}
      </div>
      {done && shown && !error && (
        <div className="tools-actions">
          <button className="tools-plain" onClick={() => void navigator.clipboard?.writeText(shown.replace(CITE_MARK, ''))}>
            Copy
          </button>
          <button className="tools-go" disabled={added} onClick={addCard}>
            {added ? 'Added' : 'Add to board'}
          </button>
        </div>
      )}
    </div>
  )
}

type QuizQ = { question: string; choices: string[]; answer: number; why: string }

/** A quiz on these cards, played in the panel (DESIGN.md 77): each question
 *  comes as data - the question, its choices, which is right, and why - so the
 *  choices are buttons and the answer is marked here, at once. */
function AiQuiz({ ids, back }: { ids: string[]; back: () => void }) {
  const boardId = useStore((s) => s.boardId)
  const total = ids.length > 1 ? 4 : 3
  const [q, setQ] = useState<QuizQ | null>(null)
  const [n, setN] = useState(1)
  const [asked, setAsked] = useState<string[]>([])
  const [picked, setPicked] = useState<number | null>(null)
  const [score, setScore] = useState(0)
  const [note, setNote] = useState('')
  const [over, setOver] = useState(false)
  const load = async (before: string[]) => {
    if (!boardId) return
    setQ(null); setPicked(null); setNote('')
    try {
      const got = await api.quizCard(boardId, ids, before)
      if ('note' in got) setNote(got.note); else setQ(got)
    } catch (e: any) { setNote(e.message || 'Could not make a question') }
  }
  useEffect(() => { void load([]) }, [boardId, ids.join()])
  const pick = (i: number) => {
    if (!q || picked !== null) return
    setPicked(i)
    if (i === q.answer) setScore((v) => v + 1)
  }
  const next = () => {
    if (!q) return
    const before = [...asked, q.question]
    setAsked(before)
    if (n >= total) { setOver(true); return }
    setN((v) => v + 1)
    void load(before)
  }
  const again = () => { setOver(false); setScore(0); setN(1); setAsked([]); void load([]) }
  const cheer = score === total ? 'Perfect! 🎉' : score >= total / 2 ? 'Nicely done! 👏' : 'Good try! 💪'
  return (
    <div className="ai-panel">
      <div className="ai-panel-head">
        <button className="ai-back" onClick={back} aria-label="Back"><Icon name="undo" size={14} /></button>
        <b>Quiz</b>
        <span className="ai-quiz-count">{over ? `${score} of ${total}` : `Question ${n} of ${total} · ${score} right`}</span>
      </div>
      <div className="ai-panel-body nowheel">
        {over ? (
          <div className="ai-quiz-end"><div className="ai-quiz-score">{score} / {total}</div>{cheer}</div>
        ) : note ? <div className="tools-note">{note}</div>
          : !q ? <div className="tools-wait"><span className="tools-spin" />Making question {n}…</div>
          : (
            <>
              <div className="ai-quiz-q">{q.question}</div>
              <div className="ai-quiz-choices">
                {q.choices.map((c, i) => (
                  <button key={i} disabled={picked !== null}
                    className={`ai-choice ${picked === null ? '' : i === q.answer ? 'right' : i === picked ? 'wrong' : 'dim'}`}
                    onClick={() => pick(i)}>
                    <span className="ai-choice-letter">{'ABCD'[i]}</span>{c}
                  </button>
                ))}
              </div>
              {picked !== null && (
                <div className={`ai-quiz-why ${picked === q.answer ? 'right' : 'wrong'}`}>
                  {picked === q.answer ? 'Right! ' : `Not quite - it is ${'ABCD'[q.answer]}. `}{q.why}
                </div>
              )}
            </>
          )}
      </div>
      <div className="tools-actions">
        {over ? <button className="tools-go" onClick={again}>Play again</button>
          : note ? <button className="tools-go" onClick={() => void load(asked)}>Try again</button>
          : picked !== null ? <button className="tools-go" onClick={next}>{n >= total ? 'See my score' : 'Next question'}</button>
          : null}
      </div>
    </div>
  )
}

/** Summarise, explain, fun fact or quiz on these cards - in the panel. */
function AiPanel({ ids, act, back }: { ids: string[]; act: Act; back: () => void }) {
  return act === 'quiz' ? <AiQuiz ids={ids} back={back} /> : <AiAnswer ids={ids} act={act} back={back} />
}

type AiView =
  | { at: 'menu' }
  | { at: 'panel'; act: Act }
  | { at: 'breaking' }
  | { at: 'break'; cards: { title: string; text: string; keep: boolean }[] }
  | { at: 'finding' }
  | { at: 'related'; cards: { id: string; title: string; snippet: string }[]; linked: string[] }
  | { at: 'said'; note: string }


type ManyView =
  | { at: 'menu' }
  | { at: 'panel'; act: Act }
  | { at: 'merging' }
  | { at: 'merge'; title: string; text: string }
  | { at: 'said'; note: string }

/** The AI menu for several selected cards (DESIGN.md 72, 77): summarise them
 *  or be quizzed on them in a panel beside them, or merge them - one card drafted from
 *  their words, which replaces them, links and all, only when accepted. */
export function SelectionAi({ ids }: { ids: string[] }) {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<ManyView>({ at: 'menu' })
  const boardId = useStore((s) => s.boardId)
  const nodes = useStore((s) => s.nodes)
  const picked = ids.map((i) => nodes[i]).filter(Boolean)
  const textual = picked.filter((n) => n.type !== 'meta')
  const canMerge = picked.length >= 2 && textual.length === picked.length && picked.length <= 8
  const shut = () => { setOpen(false); setView({ at: 'menu' }) }
  const ask = (act: Act) => setView({ at: 'panel', act })
  const root = useRef<HTMLDivElement>(null)
  useFlip(root)

  const merge = async () => {
    if (!boardId) return
    setView({ at: 'merging' })
    try {
      const got = await api.mergeCards(boardId, ids)
      setView({ at: 'merge', title: got.title, text: got.text })
    } catch (e: any) {
      setView({ at: 'said', note: e.message || 'Could not merge these cards' })
    }
  }
  const replace = (title: string, text: string) => {
    const store = useStore.getState()
    const gone = new Set(picked.map((n) => n.id))
    const x = Math.min(...picked.map((n) => n.pos[0]))
    const y = Math.min(...picked.map((n) => n.pos[1]))
    const merged = newNode({ pos: [Math.round(x), Math.round(y)], parent: picked[0].parent, type: 'text',
                             title, content: { text }, provenance: 'ai' })
    const ops: Op[] = [{ kind: 'node.create', node: merged }]
    const seen = new Set<string>()
    // every link the cards had to cards outside them now meets the merged card
    for (const e of Object.values(store.edges)) {
      const s = gone.has(e.source), t = gone.has(e.target)
      if (s === t) continue
      const source = s ? merged.id : e.source
      const target = t ? merged.id : e.target
      const key = `${source}>${target}`
      if (seen.has(key)) continue
      seen.add(key)
      ops.push({ kind: 'edge.create', edge: newEdge(source, target,
        { kind: e.kind, label: e.label, directed: e.directed, style: e.style }) })
    }
    ops.push({ kind: 'node.delete', ids: [...gone] })
    store.run(ops)
    spark(merged.id)
    store.say(`Merged ${gone.size} cards into one - Ctrl+Z to undo`)
    shut()
  }

  return (
    <span className="sel-ai">
      <button className={open ? 'on' : ''} title="AI: summarise, quiz, merge"
        onClick={() => { setOpen((v) => !v); setView({ at: 'menu' }) }}>
        <Icon name="sparkle" size={14} /> AI
      </button>
      {open && (
        <div ref={root} className={`tools-menu list ai ${view.at === 'menu' ? '' : 'wide'} sel-ai-menu nowheel`}>
          {view.at === 'panel' && <AiPanel ids={ids} act={view.act} back={() => setView({ at: 'menu' })} />}
          {view.at === 'menu' && (
            <>
              <button className="tools-item" onClick={() => ask('summarize')}>
                <Icon name="type" size={15} /> Summarise these
              </button>
              <button className="tools-item" onClick={() => ask('quiz')}>
                <Icon name="bulb" size={15} /> Quiz me on these
              </button>
              <span className="tools-rule" />
              <button className="tools-item" disabled={!canMerge} onClick={() => void merge()}
                title={canMerge ? 'Merge into one' : 'Select 2 to 8 cards'}>
                <Icon name="layers" size={15} /> Merge into one card
              </button>
            </>
          )}
          {view.at === 'merging' && (
            <div className="tools-wait"><span className="tools-spin" />Merging {picked.length} cards…</div>
          )}
          {view.at === 'said' && (
            <>
              <div className="tools-note">{view.note}</div>
              <button className="tools-item" onClick={() => setView({ at: 'menu' })}>
                <Icon name="undo" size={15} /> Back
              </button>
            </>
          )}
          {view.at === 'merge' && (
            <>
              <div className="tools-label">
                The merged card - it replaces these {picked.length}, and keeps their links
              </div>
              <div className="tools-merged">
                <input value={view.title} aria-label="Title"
                  onChange={(e) => setView({ ...view, title: e.target.value })} />
                <textarea value={view.text} aria-label="Text" rows={6}
                  onChange={(e) => setView({ ...view, text: e.target.value })} />
              </div>
              <div className="tools-actions">
                <button className="tools-plain" onClick={shut}>Cancel</button>
                <button className="tools-go" disabled={!view.title.trim() && !view.text.trim()}
                  onClick={() => replace(view.title.trim(), view.text.trim())}>Replace with this</button>
              </div>
            </>
          )}
        </div>
      )}
    </span>
  )
}

/** The card's AI menu (DESIGN.md 72, 77): summarise, explain, quiz or a fun
 *  fact in a panel beside it - break it into smaller cards, find the cards that belong
 *  with it, fix its writing, or put it in the chat's context. Drafted cards
 *  and found links are offered here and added only when the person says so. */
function AiMenu({ node, close }: { node: MNode; close: () => void }) {
  const [view, setView] = useState<AiView>({ at: 'menu' })
  const boardId = useStore((s) => s.boardId)
  const hasText = !!(node.title.trim() || String(node.content.text ?? '').trim())
  const ask = (act: Act) => setView({ at: 'panel', act })
  // above the bar unless the window's top would cut it off (as CardTools does)
  const root = useRef<HTMLDivElement>(null)
  useFlip(root)

  const breakUp = async () => {
    if (!boardId) return
    setView({ at: 'breaking' })
    try {
      const got = await api.breakCard(boardId, node.id)
      setView(got.cards.length ? { at: 'break', cards: got.cards.map((c) => ({ ...c, keep: true })) }
        : { at: 'said', note: got.note || 'Nothing to break up.' })
    } catch (e: any) {
      setView({ at: 'said', note: e.message || 'Could not break this card up' })
    }
  }
  const addDrafts = (cards: { title: string; text: string; keep: boolean }[]) => {
    const kept = cards.filter((c) => c.keep)
    if (!kept.length) return
    const store = useStore.getState()
    // beside the card, where there is room (DESIGN.md 72)
    const spots = columnSpots(node, kept.length)
    const ops: Op[] = []
    const ids: string[] = []
    kept.forEach((c, i) => {
      const child = newNode({ pos: spots[i], parent: node.parent,
                              type: 'text', title: c.title, content: { text: c.text }, provenance: 'ai' })
      const edge = newEdge(child.id, node.id, { kind: 'part-of' })
      ops.push({ kind: 'node.create', node: child }, { kind: 'edge.create', edge })
      ids.push(child.id, edge.id)
    })
    store.run(ops)
    spark(...ids)
    store.say(`Added ${kept.length} cards - Ctrl+Z to undo`)
    close()
  }
  const findRelated = async () => {
    if (!boardId) return
    setView({ at: 'finding' })
    try {
      const got = await api.relatedCards(boardId, node.id)
      setView(got.results.length ? { at: 'related', cards: got.results, linked: [] }
        : { at: 'said', note: 'No other cards on this board seem to belong with this one.' })
    } catch (e: any) {
      setView({ at: 'said', note: e.message || 'Could not search the board' })
    }
  }

  if (view.at === 'panel') return (
    <div ref={root} className="tools-menu list ai wide nowheel">
      <AiPanel ids={[node.id]} act={view.act} back={() => setView({ at: 'menu' })} />
    </div>
  )
  if (view.at === 'breaking' || view.at === 'finding') return (
    <div ref={root} className="tools-menu list ai wide nowheel">
      <div className="tools-wait"><span className="tools-spin" />
        {view.at === 'breaking' ? 'Breaking it into cards…' : 'Looking for related cards…'}</div>
    </div>
  )
  if (view.at === 'said') return (
    <div ref={root} className="tools-menu list ai wide nowheel">
      <div className="tools-note">{view.note}</div>
      <button className="tools-item" onClick={() => setView({ at: 'menu' })}>
        <Icon name="undo" size={15} /> Back
      </button>
    </div>
  )
  if (view.at === 'break') {
    const n = view.cards.filter((c) => c.keep).length
    return (
      <div ref={root} className="tools-menu list ai wide nowheel">
        <div className="tools-label">New cards, linked to this one - untick any you don't want</div>
        <div className="tools-drafts">
          {view.cards.map((c, i) => (
            <label key={i} className={`tools-draft ${c.keep ? '' : 'off'}`}>
              <input type="checkbox" checked={c.keep} onChange={() => setView({
                ...view, cards: view.cards.map((x, j) => (j === i ? { ...x, keep: !x.keep } : x)) })} />
              <span><b>{c.title}</b>{c.text && c.text !== c.title ? <em>{c.text}</em> : null}</span>
            </label>
          ))}
        </div>
        <div className="tools-actions">
          <button className="tools-plain" onClick={() => setView({ at: 'menu' })}>Cancel</button>
          <button className="tools-go" disabled={!n} onClick={() => addDrafts(view.cards)}>
            Add {n} {n === 1 ? 'card' : 'cards'}
          </button>
        </div>
      </div>
    )
  }
  if (view.at === 'related') return (
    <div ref={root} className="tools-menu list ai wide nowheel">
      <div className="tools-label">Cards that belong with this one</div>
      <div className="tools-drafts">
        {view.cards.map((c) => {
          const done = view.linked.includes(c.id)
          return (
            <div key={c.id} className="tools-found">
              <button className="tools-found-card" title="Go to this card" onClick={() => revealCard(c.id)}>
                <b>{c.title}</b>{c.snippet ? <em>{c.snippet}</em> : null}
              </button>
              <button className={`tools-link ${done ? 'done' : ''}`} disabled={done}
                onClick={() => { connect(node.id, c.id); setView({ ...view, linked: [...view.linked, c.id] }) }}>
                {done ? <><Icon name="check" size={13} /> Linked</> : <><Icon name="link" size={13} /> Link</>}
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
  return (
    <div ref={root} className="tools-menu list ai nowheel">
      <button className="tools-item" disabled={!hasText} onClick={() => ask('summarize')}>
        <Icon name="type" size={15} /> Summarise
      </button>
      <button className="tools-item" disabled={!hasText} onClick={() => ask('explain')}>
        <Icon name="explain" size={15} /> Explain this card
      </button>
      <button className="tools-item" disabled={!hasText} onClick={() => ask('quiz')}>
        <Icon name="bulb" size={15} /> Quiz me on this
      </button>
      <button className="tools-item" disabled={!hasText} onClick={() => ask('funfact')}>
        <Icon name="sparkle" size={15} /> Fun fact
      </button>
      <span className="tools-rule" />
      <button className="tools-item" disabled={!String(node.content.text ?? '').trim()}
        onClick={() => void breakUp()}>
        <Icon name="branch" size={15} /> Break it into cards
      </button>
      <button className="tools-item" disabled={!hasText} onClick={() => void findRelated()}>
        <Icon name="search" size={15} /> Find related cards
      </button>
      <span className="tools-rule" />
      <FixButton node={node} labelled after={close} />
      <AskButton id={node.id} group={node.type === 'meta'} labelled after={close} />
    </div>
  )
}

/** The selected card's tools (DESIGN.md 71): one compact bar just above the
 *  card, centred - colour, text size, AI, connect. Each button opens only what
 *  belongs to it, above the bar, so the card stays in view. In xyflow's node
 *  toolbar, so it keeps one size at any zoom and shows only while this card
 *  alone is selected. */
function CardTools({ node, selected }: { node: MNode; selected?: boolean }) {
  const [menu, setMenu] = useState<Menu | null>(null)
  const any = useRef<HTMLInputElement>(null)
  useEffect(() => { if (!selected) setMenu(null) }, [selected])
  // the full picker commits once, when it is closed - not an op for every
  // step of a drag across the colour field
  useEffect(() => {
    const el = any.current
    if (!el) return
    const done = () => setStyle(node, { color: el.value })
    el.addEventListener('change', done)
    return () => el.removeEventListener('change', done)
  })
  const size = sizeOf(node)
  const nearest = NAMED_SIZES.reduce((m, x) => (Math.abs(x.size - size) < Math.abs(m.size - size) ? x : m))
  const tone: NodeTone = node.style.tone ?? 'accent'
  const colour = node.style.color
  const own = !!colour && !NODE_COLOURS.slice(0, 6).includes(colour)
  const flip = (m: Menu) => setMenu((v) => (v === m ? null : m))
  const close = () => setMenu(null)
  // a menu opens above the bar, so the card stays in view - unless the top
  // of the window would cut it off; then it opens below, over the card
  const bar = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const m = bar.current?.querySelector<HTMLElement>(':scope > .tools-menu')
    if (!m) return
    m.classList.remove('below')
    if (m.getBoundingClientRect().top < 8) m.classList.add('below')
  })
  const btn = (m: Menu) => `tools-btn ${menu === m ? 'open' : ''}`
  return (
    <NodeToolbar position={Position.Top} align="center" offset={30}
      className="card-tools-bar nodrag nopan nowheel">
      <div ref={bar} className="tools-bar" role="toolbar" aria-label="This card"
        style={{ ['--node-color' as string]: accentOf(node) }}
        onDoubleClick={(e) => e.stopPropagation()}>
        <button className={btn('colour')} title="Colour" aria-expanded={menu === 'colour'}
          onClick={() => flip('colour')}>
          <span className="tools-dot" />
        </button>
        <button className={`${btn('text')} text`} title="Text size"
          aria-expanded={menu === 'text'} onClick={() => flip('text')}>Aa</button>
        <button className={btn('ai')} title="AI"
          aria-expanded={menu === 'ai'} onClick={() => flip('ai')}>
          <Icon name="sparkle" size={16} />
        </button>
        <span className="sep" />
        <button className="tools-btn" title="Connect  (C)"
          onClick={() => useUI.getState().set('linking', { sourceId: node.id })}>
          <Icon name="link" size={15} />
        </button>
        <button className="tools-btn danger" title="Delete  (Del)" onClick={() => deleteNodes([node.id])}>
          <Icon name="trash" size={15} />
        </button>

        {menu === 'colour' && (
          <div className="tools-menu colours">
            {NODE_COLOURS.slice(0, 6).map((c) => (
              <button key={c} className={`tools-swatch ${colour === c ? 'on' : ''}`}
                title="Colour" style={{ background: c }}
                onClick={() => setStyle(node, { color: c })} />
            ))}
            <label className={`tools-swatch any ${own ? 'on' : ''}`} title="Any colour"
              style={own ? { background: colour } : undefined}>
              <input ref={any} type="color" value={colour && colour.startsWith('#') ? colour : '#8a8f98'}
                onChange={() => { /* committed on change, above */ }} />
            </label>
            <button className="tools-swatch none" title="Default colour"
              onClick={() => setStyle(node, { color: undefined })}>
              <Icon name="x" size={11} />
            </button>
            <span className="sep" />
            <span className="tone-group" title="How far the colour carries">
              {TONES.map((t) => (
                <button key={t.key} className={`tone-chip ${t.key} ${tone === t.key ? 'on' : ''}`}
                  title={t.label} aria-label={t.label}
                  onClick={() => setStyle(node, { tone: t.key })} />
              ))}
            </span>
          </div>
        )}
        {menu === 'text' && (
          <div className="tools-menu list text">
            <div className="tools-sizes">
              {NAMED_SIZES.map((x) => (
                <button key={x.label} className={x === nearest ? 'on' : ''}
                  title={`${x.size} px`} style={{ fontSize: 10 + x.size * 0.25 }}
                  onClick={() => setStyle(node, { fontSize: x.size })}>{x.label}</button>
              ))}
            </div>
          </div>
        )}
        {menu === 'ai' && <AiMenu node={node} close={close} />}
      </div>
    </NodeToolbar>
  )
}

/** Put this card in what Ask your board reads - or take it out again. */
/** Fix spelling and grammar (DESIGN.md 56): the local model corrects the
 *  card's title and text - the person's words, not a rewrite - and the card
 *  changes in one undoable step. */
function FixButton({ node, labelled, after }: { node: MNode; labelled?: boolean; after?: () => void }) {
  const [busy, setBusy] = useState(false)
  const boardId = useStore((s) => s.boardId)
  const text = String(node.content.text ?? '')
  if (!node.title.trim() && !text.trim()) return null
  const fix = async () => {
    if (!boardId || busy) return
    setBusy(true)
    const store = useStore.getState()
    try {
      const got = await api.proofread(boardId, { title: node.title, text })
      const now = useStore.getState().nodes[node.id]
      if (!now) return
      if (!got.changed) { store.say('Nothing to fix - it reads well'); return }
      store.run([{ kind: 'node.update', id: node.id,
                   patch: { title: got.title, content: { ...now.content, text: got.text } } }])
      store.say('Fixed spelling and grammar - Ctrl+Z to undo')
    } catch (e: any) {
      store.say(e.message || 'Could not fix the writing', true)
    } finally { setBusy(false) }
  }
  if (labelled) return (
    <button className={`tools-item fix-btn ${busy ? 'busy' : ''}`} disabled={busy}
      onClick={() => { void fix().then(() => after?.()) }}>
      <Icon name="wand" size={15} /> {busy ? 'Fixing the writing…' : 'Fix the writing'}
    </button>
  )
  return (
    <button className={`icon-btn fix-btn ${busy ? 'busy' : ''}`} disabled={busy}
      title={busy ? 'Fixing…' : 'Fix the writing'}
      onClick={() => void fix()}>
      <Icon name="wand" size={15} />
    </button>
  )
}

function AskButton({ id, group, labelled, after }: {
  id: string; group: boolean; labelled?: boolean; after?: () => void
}) {
  const inside = useAsk((s) => s.context.includes(id))
  if (labelled) return (
    <button className={`tools-item ${inside ? 'on' : ''}`}
      onClick={() => { useAsk.getState().toggle(id); after?.() }}>
      <Icon name="askAdd" size={15} />
      {inside ? 'Take out of the chat' : group ? 'Ask about this group' : 'Ask about this card'}
    </button>
  )
  return (
    <button className={`icon-btn ${inside ? 'on' : ''}`}
      title={inside ? 'In the chat'
        : group ? 'Ask about this' : 'Add to context'}
      onClick={() => useAsk.getState().toggle(id)}>
      <Icon name="askAdd" size={15} />
    </button>
  )
}

function Shell({ node, selected, resizable, children }: {
  node: MNode; selected?: boolean; resizable?: boolean; children: React.ReactNode
}) {
  const sparked = useUI((s) => s.sparkIds.includes(node.id))
  const dropping = useUI((s) => s.dropTarget === node.id)
  const inContext = useAsk((s) => s.context.includes(node.id))
  const found = useUI((s) => s.foundId === node.id)
  // Zoomed far out a card is too small to read: draw its title alone, not
  // its editor, list, picture or link preview (DESIGN.md 36). A boolean, so
  // the card renders again only when the line is crossed.
  const far = useFlow((s) => s.transform[2] < 0.35)
  if (far && !selected) children = <div className="far-title">{node.title || 'Untitled'}</div>
  return (
    <>
      {resizable && (
        <NodeResizer isVisible={!!selected} minWidth={150} minHeight={60}
          lineStyle={{ borderColor: 'var(--mb-accent)' }}
          handleStyle={{ width: 7, height: 7, borderRadius: 2, background: 'var(--mb-accent)' }}
          onResizeEnd={(_, p) => updateNode(node.id, { size: [p.width, p.height] })} />
      )}
      <CardTools node={node} selected={selected} />
      <div className={`mb-node ${node.type} ${node.provenance === 'ai' ? 'ai' : ''}`
        + ` tone-${node.style.tone ?? 'accent'}${node.style.color ? ' has-colour' : ''}`
        + `${sparked ? ' spark' : ''}${node.size[1] ? ' sized-h' : ''}`
        + `${dropping ? ' dropping' : ''}${inContext ? ' in-context' : ''}${found ? ' found' : ''}`}
        style={{
          ['--node-color' as string]: accentOf(node),
          width: node.size[0] ? '100%' : undefined,
          height: node.size[1] ? '100%' : undefined,
          // the default max-width keeps unsized nodes readable; once you have
          // resized one by hand, your size wins
          maxWidth: node.size[0] ? 'none' : undefined,
        }}>
        <div className="accent" style={{ background: accentOf(node) }} />
        {inContext ? (
          <span className="ctx-mark" title="In the chat">
            <Icon name="ask" size={10} />
          </span>
        ) : null}
        {children}
      </div>
      <Ports nodeId={node.id} />
    </>
  )
}

// ----------------------------------------------------------------- types -- //

export function TextNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  return (
    <Shell node={node} selected={selected} resizable>
      <NodeText node={node} placeholder="Untitled" bodyPlaceholder="Add a note..." />
    </Shell>
  )
}

/** A click that opens what a card holds - a picture full screen, a PDF in your
 *  reader - the way a click on a link's cover opens the link.
 *
 *  Detected from pointer travel, like a port: a press that moves is xyflow
 *  dragging the card, and xyflow swallows the `click` after any movement at
 *  all, so a hand that wobbles by a pixel would otherwise never get it.
 *  Modifier clicks are selection, and a click while connecting is finishing
 *  the connection - neither opens anything. */
function useClickToOpen(open: () => void) {
  const down = useRef<{ x: number; y: number } | null>(null)
  return {
    onPointerDown: (e: React.PointerEvent) => {
      const plain = e.button === 0 && !(e.shiftKey || e.ctrlKey || e.metaKey || e.altKey)
      down.current = plain ? { x: e.clientX, y: e.clientY } : null
    },
    onPointerUp: (e: React.PointerEvent) => {
      const start = down.current
      down.current = null
      if (!start || Math.hypot(e.clientX - start.x, e.clientY - start.y) >= CLICK_SLOP) return
      if (useUI.getState().linking) return
      open()
    },
  }
}

export function ImageNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  const sha = node.content.sha256
  const press = useClickToOpen(() =>
    useUI.getState().set('viewing', { nodeId: node.id, openedAt: Date.now() }))

  return (
    <Shell node={node} selected={selected} resizable>
      {sha && boardId
        ? <img className="media viewable" src={api.assetUrl(boardId, sha)}
            alt={node.content.alt ?? ''} draggable={false} title="Click to view full screen"
            {...press} />
        : null}
      <NodeText node={node} placeholder="Untitled image"
        bodyPlaceholder="What does this image show?" />
    </Shell>
  )
}

export function VideoNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  const sha = node.content.sha256
  const poster = node.content.poster
  const posterUrl = poster && boardId ? api.assetUrl(boardId, poster) : undefined
  return (
    <Shell node={node} selected={selected} resizable>
      {sha && boardId ? (
        <>
          <video className="media nodrag nopan" controls preload="metadata"
            poster={posterUrl} src={api.assetUrl(boardId, sha)}
            onDoubleClick={(e) => e.stopPropagation()} />
          {/* stands in for the video when the board is exported as an image */}
          {posterUrl ? <img className="media video-poster" src={posterUrl} alt="" /> : null}
        </>
      ) : null}
      <NodeText node={node} placeholder="Untitled video"
        bodyPlaceholder="What is worth noticing here?" />
      {node.content.duration_ms ? (
        <div className="meta-line duration">
          {Math.floor(node.content.duration_ms / 60000)}:
          {String(Math.floor((node.content.duration_ms % 60000) / 1000)).padStart(2, '0')}
        </div>
      ) : null}
    </Shell>
  )
}

export function LinkNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  const url = node.content.url as string | undefined
  const provider = node.content.provider as string | undefined
  const thumb = node.content.thumb && boardId
    ? api.assetUrl(boardId, node.content.thumb) : undefined
  const lastOpen = useRef(0)

  /** A link opens where links belong: in your browser. Playing it inside the
   *  node meant embedding the provider's player, which is a request to them
   *  every time you open a board - and which YouTube refuses outright unless
   *  you hand over a referrer. */
  const open = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!url) return
    const now = Date.now()
    if (now - lastOpen.current < 500) return   // a double-click is still one intent
    lastOpen.current = now
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  const label = provider ? `Watch on ${provider}` : 'Open in your browser'

  return (
    <Shell node={node} selected={selected} resizable>
      {url && (
        <div className={`media-frame ${thumb ? 'has-cover' : ''}`}
          onClick={thumb ? open : undefined}
          onDoubleClick={(e) => e.stopPropagation()}
          title={thumb ? label : undefined}>
          {thumb ? (
            <>
              <img className="media" src={thumb} alt="" draggable={false} />
              <span className="open-badge" aria-hidden="true">
                {provider
                  ? <svg viewBox="0 0 24 24" width="24" height="24">
                      <path d="M8 5.2v13.6L19 12z" fill="currentColor" />
                    </svg>
                  : <Icon name="external" size={18} />}
              </span>
              <span className="open-hint">{label}</span>
            </>
          ) : (
            <div className="no-cover">
              <button className="cover-btn nodrag nopan"
                onClick={(e) => { e.stopPropagation(); void refreshLinkPreview(node.id) }}>
                <Icon name="image" size={13} /> Get cover
              </button>
              <button className="cover-btn nodrag nopan" onClick={open}>
                <Icon name="external" size={13} /> {label}
              </button>
            </div>
          )}
        </div>
      )}
      <div className="link-head">
        <span className="fav" style={{ background: accentOf(node) }} />
        <span className="host">{provider || node.content.host}</span>
        <a className="icon-btn nodrag" href={node.content.url} target="_blank" rel="noreferrer"
          title={label} onClick={(e) => e.stopPropagation()}>
          <Icon name="external" size={12} />
        </a>
      </div>
      <NodeText node={node} placeholder="Untitled link"
        bodyPlaceholder="Why does this matter?" />
      {node.content.excerpt
        ? <div className="excerpt">{node.content.excerpt}</div>
        : null}
    </Shell>
  )
}

/** What a file's name says it is, for its badge: PDF, DOCX, ZIP. */
const extOf = (name: string) => (name.match(/\.([a-z0-9]{1,5})$/i)?.[1] ?? 'file').toUpperCase()

/** a card named in a card's text ([[id]]): its title, a click away */
function cardRef(id: string) {
  return <CardRef key={id} id={id} />
}

function CardRef({ id }: { id: string }) {
  const title = useStore((s) => s.nodes[id]?.title)
  if (title === undefined) return <>{`[[${id}]]`}</>
  return (
    <button className="card-ref nodrag" title="Go to this card"
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => { e.stopPropagation(); revealCard(id) }}>{title || 'Untitled'}</button>
  )
}

export function FileNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  const say = useStore((s) => s.say)
  const sha = node.content.sha256 as string | undefined
  // files fetched from a pasted link kept their name under `alt` before `filename`
  const name: string = node.content.filename || node.content.alt || node.title || 'file'
  const openable = !!sha && (node.content.mime === 'application/pdf' || /\.pdf$/i.test(name))
  const lastOpen = useRef(0)

  /** A PDF opens where you read PDFs - in your own reader, from a read-only
   *  copy the server hands it. A double-click is still one intent. */
  const press = useClickToOpen(() => {
    if (!boardId || !sha) return
    const now = Date.now()
    if (now - lastOpen.current < 500) return
    lastOpen.current = now
    api.openAsset(boardId, sha)
      .then((r) => say(`opened ${r.opened} in your PDF reader`))
      .catch((err: Error) => say(err.message, true))
  })
  const stop = (e: React.SyntheticEvent) => e.stopPropagation()

  return (
    <Shell node={node} selected={selected} resizable>
      <div className={`doc-face${openable ? ' openable' : ''}`}
        title={openable ? 'Open in your PDF reader' : undefined}
        // the click opens the file; writing on the card is the caption's job
        onDoubleClick={openable ? stop : undefined}
        {...(openable ? press : {})}>
        <span className="doc-badge" style={{ ['--node-color' as string]: accentOf(node) }}>
          {extOf(name)}
        </span>
        <span className="doc-label">
          <span className="doc-name">{name}</span>
          {openable ? <span className="doc-hint">Opens in your PDF reader</span> : null}
        </span>
        {boardId && sha && askable(name) ? (
          <button className="icon-btn nodrag" title="Ask this file"
            aria-label={`Ask ${name}`} onPointerDown={stop} onPointerUp={stop} onDoubleClick={stop}
            onClick={(e) => { stop(e); void useAsk.getState().openFile({ board: boardId, sha, card: node.id, name }) }}>
            <Icon name="ask" size={13} />
          </button>
        ) : null}
        {boardId && sha ? (
          <a className="icon-btn nodrag" href={api.assetUrl(boardId, sha)} download={name}
            title="Save a copy somewhere else"
            onClick={stop} onPointerDown={stop} onPointerUp={stop}>
            <Icon name="download" size={13} />
          </a>
        ) : null}
      </div>
      <NodeText node={node} placeholder="Untitled file" bodyPlaceholder="What is in it?" />
    </Shell>
  )
}

export function MetaNode({ id, data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const allNodes = useStore((s) => s.nodes)
  const allEdges = useStore((s) => s.edges)
  const kids = useMemo(
    () => Object.values(allNodes).filter((n) => n.parent === id), [allNodes, id])
  const miniNodes = useMemo(() => toMini(kids), [kids])
  const miniEdges = useMemo<MiniEdge[]>(() => {
    const index = new Map(kids.map((k, i) => [k.id, i]))
    const out: MiniEdge[] = []
    for (const e of Object.values(allEdges)) {
      const a = index.get(e.source)
      const b = index.get(e.target)
      if (a !== undefined && b !== undefined && a !== b) out.push([a, b])
    }
    return out
  }, [allEdges, kids])
  const count = kids.length
  const setUI = useUI((s) => s.set)

  // Double-clicking a group used to open it wherever you clicked, so its title
  // and description could not be written that way. Now, as on a picture card,
  // double-clicking the words edits them; double-clicking the rest of the card
  // - its count, its little map - opens the group, as does its Open button.
  return (
    <Shell node={node} selected={selected}>
      <div onDoubleClick={(e) => { e.stopPropagation(); setUI('editingId', id) }}>
        <NodeText node={node} placeholder="Group" bodyPlaceholder="What is this group for?" />
      </div>
      <div className="badge-row" title="Open group">
        <span className="badge">
          <Icon name="layers" size={11} />
          {count === 0 ? 'empty' : `${count} ${count === 1 ? 'node' : 'nodes'}`}
        </span>
      </div>
      {miniNodes.length > 1 && (
        <div className="meta-mini">
          <GraphMini nodes={miniNodes} edges={miniEdges} w={150} h={54} pad={11} dense />
        </div>
      )}
    </Shell>
  )
}

export function GhostNode({ data }: NodeProps) {
  const d = data as { title: string; targetId: string; onJump: (id: string) => void }
  return (
    <div className="mb-node ghost" onClick={() => d.onJump(d.targetId)} title="Jump to this node">
      <div className="body">
        <div className="title" style={{ color: 'var(--mb-text-dim)', fontWeight: 500 }}>
          {d.title || 'elsewhere'} <Icon name="external" size={11} />
        </div>
      </div>
      <Handle id="l" type="source" position={Position.Left} />
      <Handle id="r" type="source" position={Position.Right} />
    </div>
  )
}

// memoised: a card renders again only when its own data changes, not
// whenever the canvas does (DESIGN.md 36)
export const nodeTypes = {
  text: memo(TextNode), image: memo(ImageNode), video: memo(VideoNode),
  link: memo(LinkNode), file: memo(FileNode), meta: memo(MetaNode), ghost: memo(GhostNode),
}
