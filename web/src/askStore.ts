/** Ask your board, on the screen: which cards are in context, and the
 *  conversation about them (DESIGN.md 25).
 *
 *  Screen state, not the board's: never an op, nothing to undo. Kept in the tab
 *  (sessionStorage, per board) so a refresh does not lose it, and gone when the
 *  tab closes - what is worth keeping gets saved as a card.
 */
import { create } from 'zustand'

import { api } from './api'
import { useStore } from './store'
import { useUI } from './ui'

export interface Turn {
  role: 'user' | 'assistant'
  text: string
  /** an answer: what it cited, what was removed, its two voices */
  cited?: string[]
  dropped?: number
  board?: string
  beyond?: string
  thinking?: string
  error?: string
  warning?: string | null
  status?: 'reading' | 'loading' | 'thinking' | 'writing' | 'done' | 'stopped'
  /** what went in: the whole board or chosen cards, and its size */
  whole?: boolean
  tokens?: number
  leftOut?: number
  /** how the cards were chosen: by hand, or a search of the whole board -
   *  never the whole board sent entire */
  mode?: 'chosen' | 'search' | 'explain'
  used?: number
  /** the cards the answer was given, best match first - for the files it offers (DESIGN.md 48) */
  cards?: string[]
  total?: number
  how?: string
  /** how far the model has read, while it reads */
  progress?: { done: number; total: number; cached: number }
  /** the question the model offers to answer next */
  next?: string
  /** the three questions it offers next (DESIGN.md 46) */
  nexts?: string[]
}

interface Ask {
  boardId: string | null
  open: boolean
  context: string[]
  turns: Turn[]
  think: boolean
  /** the conversation large, the board dimmed behind it */
  expanded: boolean
  /** Explain more (in the expanded view): answer from the conversation and
   *  its cards, without searching the board */
  explain: boolean
  busy: boolean
  controller: AbortController | null
  load(boardId: string | null): void
  setOpen(open: boolean): void
  toggle(id: string): void
  add(ids: string[]): void
  remove(id: string): void
  clear(): void
  /** forget the conversation - its questions and answers - keeping the cards in context */
  forget(): void
  /** where the panel sits: in the middle above the bar, or along the left edge */
  side: 'center' | 'left'
  setSide(side: 'center' | 'left'): void
  setThink(on: boolean): void
  setExpanded(on: boolean): void
  setExplain(on: boolean): void
  send(question: string): Promise<void>
  stop(): void
}

const KEY = (b: string) => `mb_ask_${b}`
/** the panel's side is a preference, the same on every board, kept across visits */
const SIDE_KEY = 'mb_ask_side'
const savedSide = (): 'center' | 'left' => {
  try { return localStorage.getItem(SIDE_KEY) === 'left' ? 'left' : 'center' } catch { return 'center' }
}
const HISTORY_TURNS = 10

function save(s: Ask) {
  if (!s.boardId) return
  try {
    sessionStorage.setItem(KEY(s.boardId), JSON.stringify({
      context: s.context, open: s.open, think: s.think, expanded: s.expanded, explain: s.explain,
      // an answer cut off by a refresh is kept as far as it got
      turns: s.turns.map((t) => (t.status && t.status !== 'done'
        ? { ...t, status: 'stopped' as const } : t)),
    }))
  } catch { /* private mode, or full */ }
}

export const useAsk = create<Ask>()((set, get) => ({
  boardId: null,
  open: false,
  context: [],
  turns: [],
  think: false,
  expanded: false,
  explain: false,
  busy: false,
  controller: null,
  side: savedSide(),

  load(boardId) {
    get().controller?.abort()
    let saved: Partial<Ask> = {}
    if (boardId) {
      try { saved = JSON.parse(sessionStorage.getItem(KEY(boardId)) || '{}') } catch { saved = {} }
    }
    set({ boardId, open: !!saved.open, context: saved.context ?? [], turns: saved.turns ?? [],
          think: !!saved.think, expanded: !!saved.expanded, explain: !!saved.explain,
          busy: false, controller: null })
  },
  setOpen(open) { set(open ? { open } : { open, expanded: false }); save(get()) },
  toggle(id) {
    const s = get()
    const context = s.context.includes(id) ? s.context.filter((x) => x !== id) : [...s.context, id]
    // putting the first card in opens the panel: that is where it went
    set({ context, open: s.open || (context.length > 0 && !s.context.includes(id)) })
    save(get())
  },
  add(ids) {
    const s = get()
    set({ context: [...s.context, ...ids.filter((id) => !s.context.includes(id))], open: true })
    save(get())
  },
  remove(id) { set({ context: get().context.filter((x) => x !== id) }); save(get()) },
  clear() { set({ context: [] }); save(get()) },
  forget() {
    get().controller?.abort()
    set({ turns: [], busy: false, controller: null })
    save(get())
  },
  setSide(side) {
    set({ side })
    try { localStorage.setItem(SIDE_KEY, side) } catch { /* private mode */ }
  },
  setThink(on) { set({ think: on }); save(get()) },
  setExpanded(on) { set({ expanded: on, open: on || get().open }); save(get()) },
  setExplain(on) { set({ explain: on }); save(get()) },

  stop() {
    const { controller, boardId } = get()
    if (!controller) return
    // tell the model first, then hang up: a hang-up alone is not always heard
    if (boardId) void api.askStop(boardId).catch(() => {})
    controller.abort()
  },

  async send(question) {
    const s = get()
    const boardId = s.boardId
    if (!boardId || s.busy || !question.trim()) return
    const nodes = useStore.getState().nodes
    // nothing added means the whole board - the server reads it that way
    const cards = s.context.filter((id) => nodes[id])
    const history = s.turns
      .filter((t) => !t.error && t.text.trim() && (t.role === 'user' || t.status === 'done'))
      .slice(-HISTORY_TURNS)
      .map((t) => ({ role: t.role, text: t.text }))
    // the cards earlier answers drew on stay in the conversation (DESIGN 34)
    const keep = [...new Set(s.turns.flatMap((t) => (t.status === 'done' && t.cited) || []))]
      .filter((id) => nodes[id]).slice(-40)
    const explain = s.expanded && s.explain
    const controller = new AbortController()
    set({
      busy: true, controller, open: true,
      turns: [...s.turns, { role: 'user', text: question.trim() },
              { role: 'assistant', text: '', status: 'reading' }],
    })
    const patch = (p: Partial<Turn>) => set((st) => {
      const turns = st.turns.slice()
      turns[turns.length - 1] = { ...turns[turns.length - 1], ...p }
      return { turns }
    })
    const last = () => get().turns[get().turns.length - 1]

    try {
      await api.ask(boardId, { question: question.trim(), cards, history, think: s.think, keep, explain },
        (name, data) => {
          if (name === 'context') {
            patch({ warning: data.warning, status: data.loading ? 'loading' : 'reading',
                    whole: data.whole, tokens: data.tokens, leftOut: data.pictures_left_out,
                    mode: data.mode, used: data.cards?.length, cards: data.cards, total: data.total, how: data.how })
          } else if (name === 'progress') {
            // the first progress means the model is loaded and reading
            patch({ status: 'reading',
                    progress: { done: data.done, total: data.total, cached: data.cached } })
          } else if (name === 'thinking') {
            patch({ thinking: (last().thinking ?? '') + data.text, status: 'thinking' })
          } else if (name === 'text') {
            patch({ text: last().text + data.text, status: 'writing' })
          } else if (name === 'done') {
            patch({ text: data.answer, board: data.board, beyond: data.beyond,
                    cited: data.cited, dropped: data.dropped.length, next: data.next || undefined,
                    nexts: data.nexts?.length ? data.nexts : data.next ? [data.next] : undefined,
                    status: data.stopped ? 'stopped' : 'done' })
          } else if (name === 'error') {
            patch({ error: data.message, status: 'done' })
          }
        }, controller.signal)
      if (last().status !== 'done') patch({ status: 'stopped' })
    } catch (err: any) {
      if (controller.signal.aborted) patch({ status: 'stopped' })
      else patch({ error: err.message || 'could not ask', status: 'done' })
    } finally {
      set({ busy: false, controller: null })
      save(get())
    }
  },
}))

/** Go to a card wherever it is - inside a group, or a group inside that -
 *  the way a ghost endpoint does. */
export function revealCard(id: string) {
  const nodes = useStore.getState().nodes
  if (!nodes[id]) return
  const chain: string[] = []
  let cur = nodes[id]
  while (cur?.parent) { chain.unshift(cur.parent); cur = nodes[cur.parent] }
  useStore.setState({ path: chain })
  // going to a card means looking at the board: the expanded view steps back
  if (useAsk.getState().expanded) useAsk.getState().setExpanded(false)
  useUI.getState().set('focusId', id)
}

/** How many cards a context entry stands for: a group brings everything inside. */
export function cardsInside(id: string): number {
  const nodes = useStore.getState().nodes
  if (nodes[id]?.type !== 'meta') return 1
  let n = 1
  for (const kid of Object.values(nodes)) if (kid.parent === id) n += cardsInside(kid.id)
  return n
}
