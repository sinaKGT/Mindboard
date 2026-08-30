/**
 * The single mutation path.
 *
 * Every change is an op. Ops apply optimistically to local state, push their
 * inverse onto the undo stack, and queue for the backend. Undo, persistence and
 * (in v2) AI editing are therefore one mechanism rather than three.
 */
import { create } from 'zustand'

import { api } from './api'
import type { BoardMeta, Envelope, MEdge, MNode, Op } from './types'
import { ulid } from './types'

interface Graph { nodes: Record<string, MNode>; edges: Record<string, MEdge> }

/** 'record' = a fresh action; the other two are the undo/redo machinery
 *  moving a batch between the two stacks. */
type History = 'record' | 'undoing' | 'redoing'

// --------------------------------------------------------------- reducer -- //

function descendants(nodes: Record<string, MNode>, roots: string[]): Set<string> {
  const doomed = new Set<string>()
  const walk = (id: string) => {
    if (doomed.has(id)) return
    doomed.add(id)
    for (const n of Object.values(nodes)) if (n.parent === id) walk(n.id)
  }
  roots.forEach(walk)
  return doomed
}

export function applyOp(g: Graph, board: BoardMeta | null, op: Op): { g: Graph; board: BoardMeta | null } {
  const nodes = { ...g.nodes }
  const edges = { ...g.edges }
  let b = board

  switch (op.kind) {
    case 'node.create':
      nodes[op.node.id] = op.node
      break
    case 'node.update': {
      const n = nodes[op.id]
      if (n) nodes[op.id] = { ...n, ...op.patch } as MNode
      break
    }
    case 'node.move':
      for (const m of op.moves) {
        const n = nodes[m.id]
        if (n) nodes[m.id] = { ...n, pos: [m.x, m.y] }
      }
      break
    case 'node.delete': {
      const doomed = descendants(nodes, op.ids)
      for (const id of doomed) delete nodes[id]
      for (const e of Object.values(edges)) {
        if (doomed.has(e.source) || doomed.has(e.target)) delete edges[e.id]
      }
      break
    }
    case 'edge.create':
      edges[op.edge.id] = op.edge
      break
    case 'edge.update': {
      const e = edges[op.id]
      if (e) edges[op.id] = { ...e, ...op.patch } as MEdge
      break
    }
    case 'edge.delete':
      for (const id of op.ids) delete edges[id]
      break
    case 'meta.wrap':
      nodes[op.meta.id] = op.meta
      for (const c of op.children) {
        const n = nodes[c.id]
        if (n) nodes[c.id] = { ...n, parent: op.meta.id, pos: [c.x, c.y] }
      }
      break
    case 'meta.unwrap':
      for (const c of op.children) {
        const n = nodes[c.id]
        if (n) nodes[c.id] = { ...n, parent: c.parent, pos: [c.x, c.y] }
      }
      delete nodes[op.metaId]
      break
    case 'board.update':
      if (b) b = { ...b, ...op.patch, settings: { ...b.settings, ...(op.patch.settings ?? {}) } }
      break
  }
  return { g: { nodes, edges }, board: b }
}

/** The inverse of an op, computed against the state *before* it is applied. */
export function invert(g: Graph, board: BoardMeta | null, op: Op): Op[] {
  switch (op.kind) {
    case 'node.create':
      return [{ kind: 'node.delete', ids: [op.node.id] }]
    case 'node.update': {
      const n = g.nodes[op.id]
      if (!n) return []
      const patch: Record<string, any> = {}
      for (const k of Object.keys(op.patch)) patch[k] = (n as any)[k]
      return [{ kind: 'node.update', id: op.id, patch }]
    }
    case 'node.move':
      return [{
        kind: 'node.move',
        moves: op.moves
          .filter((m) => g.nodes[m.id])
          .map((m) => ({ id: m.id, x: g.nodes[m.id].pos[0], y: g.nodes[m.id].pos[1] })),
      }]
    case 'node.delete': {
      const doomed = descendants(g.nodes, op.ids)
      // parents before children, so re-creation never dangles
      const ordered = [...doomed]
        .map((id) => g.nodes[id])
        .filter(Boolean)
        .sort((a, b2) => (doomed.has(a.parent ?? '') ? 1 : 0) - (doomed.has(b2.parent ?? '') ? 1 : 0))
      const back: Op[] = ordered.map((n) => ({ kind: 'node.create', node: n }))
      for (const e of Object.values(g.edges)) {
        if (doomed.has(e.source) || doomed.has(e.target)) back.push({ kind: 'edge.create', edge: e })
      }
      return back
    }
    case 'edge.create':
      return [{ kind: 'edge.delete', ids: [op.edge.id] }]
    case 'edge.update': {
      const e = g.edges[op.id]
      if (!e) return []
      const patch: Record<string, any> = {}
      for (const k of Object.keys(op.patch)) patch[k] = (e as any)[k]
      return [{ kind: 'edge.update', id: op.id, patch }]
    }
    case 'edge.delete':
      return op.ids.filter((id) => g.edges[id]).map((id) => ({ kind: 'edge.create', edge: g.edges[id] }))
    case 'meta.wrap':
      return [{
        kind: 'meta.unwrap',
        metaId: op.meta.id,
        children: op.children
          .filter((c) => g.nodes[c.id])
          .map((c) => ({
            id: c.id,
            parent: g.nodes[c.id].parent,
            x: g.nodes[c.id].pos[0],
            y: g.nodes[c.id].pos[1],
          })),
      }]
    case 'meta.unwrap': {
      const meta = g.nodes[op.metaId]
      if (!meta) return []
      return [{
        kind: 'meta.wrap',
        meta,
        children: op.children
          .filter((c) => g.nodes[c.id])
          .map((c) => ({ id: c.id, x: g.nodes[c.id].pos[0], y: g.nodes[c.id].pos[1] })),
      }]
    }
    case 'board.update': {
      if (!board) return []
      const patch: Record<string, any> = {}
      for (const k of Object.keys(op.patch)) patch[k] = (board as any)[k]
      return [{ kind: 'board.update', patch }]
    }
  }
}

// ---------------------------------------------------------------- outbox -- //

let outbox: Envelope[] = []
let flushTimer: ReturnType<typeof setTimeout> | undefined
const OUTBOX_KEY = 'mb_outbox'

function persistOutbox(boardId: string) {
  try { sessionStorage.setItem(OUTBOX_KEY, JSON.stringify({ boardId, outbox })) } catch { /* ignore */ }
}

async function flush(boardId: string) {
  if (!outbox.length) return
  const batch = outbox
  outbox = []
  try {
    const res = await api.ops(boardId, batch)
    useStore.setState({ revision: res.revision, saving: false })
    if (res.rejected.length) {
      useStore.getState().say(`the server refused ${res.rejected.length} change(s)`, true)
    }
    try { sessionStorage.removeItem(OUTBOX_KEY) } catch { /* ignore */ }
  } catch (err: any) {
    outbox = [...batch, ...outbox]  // keep them; try again on the next edit
    persistOutbox(boardId)
    useStore.setState({ saving: false })
    useStore.getState().say(`could not save: ${err.message}`, true)
  }
}

function enqueue(boardId: string, ops: Op[]) {
  outbox.push(...ops.map((op) => ({ opId: ulid(), op, origin: 'user' as const })))
  persistOutbox(boardId)
  useStore.setState({ saving: true })
  clearTimeout(flushTimer)
  flushTimer = setTimeout(() => void flush(boardId), 300)
}

export function flushNow() {
  const id = useStore.getState().boardId
  if (id) { clearTimeout(flushTimer); void flush(id) }
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', flushNow)
  document.addEventListener('visibilitychange', () => { if (document.hidden) flushNow() })
}

// ----------------------------------------------------------------- store -- //

interface Store extends Graph {
  boardId: string | null
  board: BoardMeta | null
  path: string[]
  revision: number
  loading: boolean
  saving: boolean
  toast: { msg: string; err?: boolean } | null
  undoStack: Op[][]
  redoStack: Op[][]

  open(id: string): Promise<void>
  close(): void
  run(ops: Op[], history?: History): void
  undo(): void
  redo(): void
  enter(metaId: string): void
  goTo(depth: number): void
  say(msg: string, err?: boolean): void
}

export const useStore = create<Store>()((set, get) => ({
  nodes: {}, edges: {},
  boardId: null, board: null, path: [], revision: 0,
  loading: false, saving: false, toast: null,
  undoStack: [], redoStack: [],

  async open(id) {
    set({ loading: true })
    try {
      const g = await api.graph(id)
      set({
        boardId: id,
        board: g.board,
        revision: g.revision,
        nodes: Object.fromEntries(g.nodes.map((n) => [n.id, n])),
        edges: Object.fromEntries(g.edges.map((e) => [e.id, e])),
        path: [], undoStack: [], redoStack: [], loading: false,
      })
    } catch (err: any) {
      set({ loading: false })
      get().say(`could not open that board: ${err.message}`, true)
    }
  },

  close() {
    flushNow()
    set({ boardId: null, board: null, nodes: {}, edges: {}, path: [], undoStack: [], redoStack: [] })
  },

  run(ops, history = 'record') {
    const state = get()
    if (!state.boardId || !ops.length) return
    let g: Graph = { nodes: state.nodes, edges: state.edges }
    let board = state.board
    let inverses: Op[] = []
    for (const op of ops) {
      inverses = [...invert(g, board, op), ...inverses]
      const next = applyOp(g, board, op)
      g = next.g
      board = next.board
    }
    const patch: Partial<Store> = { nodes: g.nodes, edges: g.edges, board }
    if (history === 'record') {
      // a fresh action invalidates anything that was waiting to be redone
      patch.undoStack = [...state.undoStack.slice(-99), inverses]
      patch.redoStack = []
    } else if (history === 'undoing') {
      patch.redoStack = [...state.redoStack.slice(-99), inverses]
    } else {
      patch.undoStack = [...state.undoStack.slice(-99), inverses]
    }
    set(patch as Store)
    enqueue(state.boardId, ops)
  },

  undo() {
    const { undoStack } = get()
    const batch = undoStack[undoStack.length - 1]
    if (!batch) return
    set({ undoStack: undoStack.slice(0, -1) })
    get().run(batch, 'undoing')
  },

  redo() {
    const { redoStack } = get()
    const batch = redoStack[redoStack.length - 1]
    if (!batch) return
    set({ redoStack: redoStack.slice(0, -1) })
    get().run(batch, 'redoing')
  },

  enter(metaId) { set({ path: [...get().path, metaId] }) },
  goTo(depth) { set({ path: get().path.slice(0, depth) }) },

  say(msg, err) {
    set({ toast: { msg, err } })
    setTimeout(() => { if (get().toast?.msg === msg) set({ toast: null }) }, err ? 5000 : 2400)
  },
}))

/** The node whose children the canvas is currently showing. */
export function currentParent(path: string[]): string | null {
  return path.length ? path[path.length - 1] : null
}

/** Resolve a node to its visible ancestor at this level, or null if it lives
 *  outside this subtree entirely (which is what makes a ghost endpoint). */
export function ancestorAtLevel(
  nodes: Record<string, MNode>, id: string, parent: string | null,
): string | null {
  let cur: MNode | undefined = nodes[id]
  const guard = new Set<string>()
  while (cur && !guard.has(cur.id)) {
    guard.add(cur.id)
    if ((cur.parent ?? null) === parent) return cur.id
    cur = cur.parent ? nodes[cur.parent] : undefined
  }
  return null
}

export function childCount(nodes: Record<string, MNode>, id: string): number {
  return Object.values(nodes).filter((n) => n.parent === id).length
}
