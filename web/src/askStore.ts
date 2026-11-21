/** Ask your board, on the screen: which cards are in context, and the
 *  conversation about them (DESIGN.md 25).
 *
 *  Screen state, not the board's: never an op, nothing to undo. Kept in the tab
 *  (sessionStorage, per board) so a refresh does not lose it, and gone when the
 *  tab closes - what is worth keeping gets saved as a card.
 */
import { create } from 'zustand'

import { api } from './api'
import { type PastedPicture, PICTURE_ONLY, withSaid } from './Pasted'
import { useStore } from './store'
import { useUI } from './ui'

/** A card's AI action, sent through the chat (DESIGN.md 75): its task, known
 *  already; the short line the chat shows; what it says while it works. */
export interface CardAction { task: 'summarize' | 'explain' | 'quiz' | 'funfact'; label: string; doing: string }

export interface Turn {
  role: 'user' | 'assistant'
  text: string
  /** a card action (DESIGN.md 75): what the model was asked, when the chat
   *  shows a short line ("Fun fact: Analogy") in its place */
  prompt?: string
  /** what it is doing before its first word, in the action's words */
  doing?: string
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
  mode?: 'chosen' | 'search' | 'explain' | 'own' | 'file'
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
  /** the nexts are a quiz question's choices, to pick as the answer (DESIGN.md 72) */
  choices?: boolean
  /** a question: small copies of the pictures pasted with it, and what the
   *  model saw in them - kept for the turns after (DESIGN.md 62) */
  images?: string[]
  said?: string[]
  /** an answer: still looking at the pictures pasted with its question */
  looking?: boolean
  /** an answer from a file (DESIGN.md 64): where each passage it was given comes from */
  passages?: { where: string; page: number | null; figure: boolean; pictured: boolean }[]
  /** a spreadsheet's answer: what the model chose to calculate over whole sheets (DESIGN.md 65) */
  tools?: { why: string; trouble: string; calls: { tool: string; label: string; said: string; error: string }[] }
  /** the request understood, and its steps as they run (DESIGN.md 66) */
  task?: TaskPlan
}

export interface TaskStep { do: string; what: string; status: 'waiting' | 'running' | 'done' | 'failed' | 'stopped'; note?: string }
export interface TaskPlan { task: string; about: string; output: string; reading: string; steps: TaskStep[] }

/** A conversation with one file (DESIGN.md 64): a branch of its own beside
 *  the board's, with its own questions and answers. The file is prepared on
 *  the server first - read, its figures described, indexed - and is gone
 *  there when the branch is closed. */
export interface FileBranch {
  id: string
  name: string
  kind: string
  /** a file on the board: its card, board and hash - empty for one added in the chat */
  board?: string
  card?: string
  sha?: string
  status: 'preparing' | 'ready' | 'failed' | 'gone'
  stage?: string
  done?: number
  total?: number
  pages?: number
  passages?: number
  note?: string
  error?: string
  sheets?: { name: string; rows: number; columns: number }[]
  /** when its preparation last moved on - to say when it is slow */
  moved?: number
  /** its conversation, while another is shown */
  turns: Turn[]
}

export const FILE_ACCEPT = '.pdf,.docx,.xlsx,.xlsm,.csv,.txt,.md'
export const askable = (name: string) => /\.(pdf|docx|xlsx|xlsm|csv|txt|md)$/i.test(name)

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
  /** Board off (DESIGN.md 63): the model alone - no search, no cards */
  own: boolean
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
  /** pinned open: a click on the empty canvas does not close it (DESIGN.md 68) */
  pinned: boolean
  setPinned(on: boolean): void
  setThink(on: boolean): void
  setExpanded(on: boolean): void
  setExplain(on: boolean): void
  setOwn(on: boolean): void
  /** `fresh`: sent without the conversation before it (a card action, DESIGN.md 72) */
  send(question: string, pictures?: PastedPicture[], fresh?: boolean, action?: CardAction): Promise<void>
  /** ask about chosen cards (DESIGN.md 72): they alone in context, on the board's
   *  own conversation with the board on, and the question in plain words */
  askAbout(ids: string[], question: string, action?: CardAction): void
  stop(): void
  /** file conversations, and which is shown - null for the board's own */
  branches: FileBranch[]
  active: string | null
  /** the board's conversation, kept while a file's is shown */
  boardTurns: Turn[]
  openFile(src: { file?: File; board?: string; sha?: string; card?: string; name: string }): Promise<void>
  show(id: string | null): void
  closeBranch(id: string): void
}

const KEY = (b: string) => `mb_ask_${b}`
/** the panel's side is a preference, the same on every board, kept across visits */
const SIDE_KEY = 'mb_ask_side'
const savedSide = (): 'center' | 'left' => {
  try { return localStorage.getItem(SIDE_KEY) === 'left' ? 'left' : 'center' } catch { return 'center' }
}
const HISTORY_TURNS = 10

// an answer cut off by a refresh is kept as far as it got
const settled = (turns: Turn[]) => turns.map((t) => (t.status && t.status !== 'done'
  ? { ...t, status: 'stopped' as const } : t))

function save(s: Ask) {
  if (!s.boardId) return
  // the shown conversation is `turns`; the others wait where they belong
  const boardTurns = s.active ? s.boardTurns : s.turns
  const branches = s.branches.map((b) => ({ ...b, turns: settled(b.id === s.active ? s.turns : b.turns) }))
  try {
    sessionStorage.setItem(KEY(s.boardId), JSON.stringify({
      context: s.context, open: s.open, think: s.think, expanded: s.expanded, explain: s.explain, own: s.own,
      turns: settled(boardTurns), branches,
    }))
  } catch { /* private mode, or full */ }
}

const polling = new Set<string>()

export const useAsk = create<Ask>()((set, get) => ({
  boardId: null,
  open: false,
  context: [],
  turns: [],
  think: false,
  expanded: false,
  explain: false,
  own: false,
  busy: false,
  controller: null,
  side: savedSide(),
  pinned: (() => { try { return localStorage.getItem('mb_ask_pinned') === '1' } catch { return false } })(),
  branches: [],
  active: null,
  boardTurns: [],

  load(boardId) {
    get().controller?.abort()
    let saved: Partial<Ask> = {}
    if (boardId) {
      try { saved = JSON.parse(sessionStorage.getItem(KEY(boardId)) || '{}') } catch { saved = {} }
    }
    set({ boardId, open: !!saved.open, context: saved.context ?? [], turns: saved.turns ?? [],
          think: !!saved.think, expanded: !!saved.expanded, explain: !!saved.explain, own: !!saved.own,
          busy: false, controller: null, branches: saved.branches ?? [], active: null, boardTurns: [] })
    // a file's preparation lives on the server: see whether it still does
    for (const b of get().branches) if (b.status !== 'gone' && b.status !== 'failed') poll(b.id)
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
  setPinned(on) {
    set({ pinned: on })
    try { localStorage.setItem('mb_ask_pinned', on ? '1' : '0') } catch { /* private mode */ }
  },
  setSide(side) {
    set({ side })
    try { localStorage.setItem(SIDE_KEY, side) } catch { /* private mode */ }
  },
  setThink(on) { set({ think: on }); save(get()) },
  setExpanded(on) { set({ expanded: on, open: on || get().open }); save(get()) },
  setExplain(on) { set({ explain: on }); save(get()) },
  setOwn(on) { set({ own: on }); save(get()) },

  async openFile(src) {
    const s = get()
    // the same file on the board, already open: go to it
    const had = src.sha ? s.branches.find((b) => b.sha === src.sha && b.status !== 'gone') : undefined
    if (had) { get().show(had.id); set({ open: true }); return }
    try {
      const info = await api.openFile(src.file ? { file: src.file } : { board: src.board!, sha: src.sha!, name: src.name })
      const branch: FileBranch = { ...info, card: src.card, turns: [] }
      set((st) => ({ branches: [...st.branches, branch] }))
      get().show(branch.id)
      set({ open: true })
      save(get())
      poll(branch.id)
    } catch (err: any) {
      useStore.getState().say(err.message || `could not open ${src.name}`, true)
    }
  },

  show(id) {
    const s = get()
    if (s.busy || id === s.active) return
    // put the shown conversation back where it belongs, then show the other
    const branches = s.branches.map((b) => (b.id === s.active ? { ...b, turns: s.turns } : b))
    const boardTurns = s.active ? s.boardTurns : s.turns
    const next = id ? branches.find((b) => b.id === id) : undefined
    set({ branches, boardTurns, active: next ? next.id : null, turns: next ? next.turns : boardTurns })
    save(get())
  },

  closeBranch(id) {
    const s = get()
    if (s.active === id) { if (s.busy) return; get().show(null) }
    set((st) => ({ branches: st.branches.filter((b) => b.id !== id) }))
    save(get())
    void api.closeFile(id).catch(() => {})       // gone on the server too: temporary
  },

  stop() {
    const { controller, boardId } = get()
    if (!controller) return
    // tell the model first, then hang up: a hang-up alone is not always heard
    if (boardId) void api.askStop(boardId).catch(() => {})
    controller.abort()
  },

  askAbout(ids, question, action) {
    const s = get()
    if (s.busy) { useStore.getState().say('Still answering - try again when it is done', true); return }
    if (s.active) get().show(null)
    set({ context: ids, own: false, open: true })
    save(get())
    void get().send(question, [], true, action)
  },

  async send(question, pictures = [], fresh = false, action) {
    const s = get()
    const boardId = s.boardId
    if (!question.trim() && pictures.length) question = PICTURE_ONLY
    if (!boardId || s.busy || !question.trim()) return
    const nodes = useStore.getState().nodes
    // nothing added means the whole board - the server reads it that way
    const cards = s.context.filter((id) => nodes[id])
    const branch = s.active ? s.branches.find((b) => b.id === s.active) : undefined
    if (branch && branch.status !== 'ready') return
    const own = s.own && !branch
    // Board off (DESIGN.md 63) carries only Board off turns: an earlier answer
    // from the board would bring the board's facts back in by the side door
    const ownTurn = (i: number) => {
      const answer = s.turns[s.turns[i].role === 'user' ? i + 1 : i]
      return answer?.role === 'assistant' && answer.mode === 'own'
    }
    const history = (fresh ? [] : s.turns)
      .filter((t, i) => !t.error && t.text.trim() && (t.role === 'user' || t.status === 'done')
        && (!own || ownTurn(i)))
      .slice(-HISTORY_TURNS)
      .map((t) => ({ role: t.role, text: t.role === 'user' ? withSaid(t.prompt ?? t.text, t.said) : t.text }))
    // the cards earlier answers drew on stay in the conversation (DESIGN 34)
    const keep = [...new Set((fresh ? [] : s.turns).flatMap((t) => (t.status === 'done' && t.cited) || []))]
      .filter((id) => nodes[id]).slice(-40)
    // Explain more holds in either view - the header icon in the small one,
    // the chip in the full screen one
    const explain = s.explain && !own && !branch
    const controller = new AbortController()
    set({
      busy: true, controller, open: true,
      turns: [...s.turns,
              { role: 'user', text: action ? action.label : question.trim(),
                ...(action ? { prompt: question.trim() } : {}),
                ...(pictures.length ? { images: pictures.map((p) => p.thumb) } : {}) },
              { role: 'assistant', text: '', status: 'reading', ...(action ? { doing: action.doing } : {}), ...(branch ? { mode: 'file' as const } : {}),
                ...(pictures.length && !own && !branch ? { looking: true } : {}) }],
    })
    const patch = (p: Partial<Turn>) => set((st) => {
      const turns = st.turns.slice()
      turns[turns.length - 1] = { ...turns[turns.length - 1], ...p }
      return { turns }
    })
    const last = () => get().turns[get().turns.length - 1]

    try {
      const body = { question: question.trim(), cards, history, think: s.think, keep, explain,
                     images: pictures.map((p) => p.url), own, ...(action ? { task: action.task } : {}) }
      await (branch ? api.askFile(branch.id, body, (n, d) => handle(n, d), controller.signal)
        : api.ask(boardId, body, (n, d) => handle(n, d), controller.signal))
      if (last().status !== 'done') patch({ status: 'stopped' })
    } catch (err: any) {
      if (controller.signal.aborted) patch({ status: 'stopped' })
      else patch({ error: err.message || 'could not ask', status: 'done' })
    } finally {
      set({ busy: false, controller: null })
      save(get())
    }

    function handle(name: string, data: any) {
          if (name === 'task') {
            patch({ task: { task: data.task, about: data.about, output: data.output, reading: data.reading,
                            steps: (data.steps ?? []).map((x: any) => ({ do: x.do, what: x.what, status: 'waiting' })) } })
          } else if (name === 'step' || name === 'task_step') {
            const t = last().task
            if (t) {
              const steps = t.steps.slice()
              if (name === 'task_step') steps.push({ do: data.do, what: data.what, status: 'waiting' })
              else if (steps[data.i]) steps[data.i] = { ...steps[data.i], status: data.status, note: data.note }
              patch({ task: { ...t, steps } })
            }
          } else if (name === 'tools') {
            patch({ tools: { why: data.why, trouble: data.trouble, calls: data.calls } })
          } else if (name === 'pasted') {
            // what the model saw in the pictures, kept with the question
            set((st) => {
              const turns = st.turns.slice()
              const q = turns.length - 2
              if (q >= 0) turns[q] = { ...turns[q], said: data.said }
              turns[turns.length - 1] = { ...turns[turns.length - 1], looking: false }
              return { turns }
            })
          } else if (name === 'context') {
            patch({ warning: data.warning, status: data.loading ? 'loading' : 'reading', looking: false,
                    whole: data.whole, tokens: data.tokens, leftOut: data.pictures_left_out,
                    mode: data.mode, used: data.mode === 'file' ? data.passages?.length : data.cards?.length,
                    cards: data.cards, total: data.total, how: data.how,
                    ...(data.passages?.length ? { passages: data.passages } : {}) })
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
                    choices: !!data.choices || undefined,
                    status: data.stopped ? 'stopped' : 'done' })
          } else if (name === 'error') {
            patch({ error: data.message, status: 'done' })
          }
    }
  },
}))

/** Follow a file's preparation until it is ready, failed, or gone. */
function poll(id: string) {
  if (polling.has(id)) return
  polling.add(id)
  const tick = async () => {
    const b = useAsk.getState().branches.find((x) => x.id === id)
    if (!b) { polling.delete(id); return }
    let next: Partial<FileBranch>
    try {
      const info = await api.fileInfo(id)
      const moved = info.stage !== b.stage || info.done !== b.done ? Date.now() : (b.moved ?? Date.now())
      next = { status: info.status, stage: info.stage, done: info.done, total: info.total, moved,
               pages: info.pages, passages: info.passages, note: info.note, error: info.error,
               sheets: info.sheets }
      misses = 0
    } catch (err: any) {
      // only "no such file" means it is gone; a hiccup - the server busy or
      // restarting - is tried again, more slowly, a few times
      if (/ended|not found|404/i.test(err?.message || '') || ++misses > 8) next = { status: 'gone' }
      else { window.setTimeout(tick, Math.min(700 * 2 ** misses, 10000)); return }
    }
    useAsk.setState((st) => ({ branches: st.branches.map((x) => (x.id === id ? { ...x, ...next } : x)) }))
    if (next.status === 'preparing') window.setTimeout(tick, 700)
    else { polling.delete(id); save(useAsk.getState()) }
  }
  let misses = 0
  void tick()
}

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
