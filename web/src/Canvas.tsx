import {
  Background, BackgroundVariant, ConnectionMode, MarkerType, MiniMap, ReactFlow,
  ReactFlowProvider, ViewportPortal, applyNodeChanges, useReactFlow, useStore as useFlow,
  type Edge, type Node, type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  addConnected, addFile, addTextNode, addUrl, autoLayout, buildClip, clipText, connect,
  deleteEdges, deleteNodes, duplicateNodes, getClip, moveInto, pasteClip, setClip, unwrapMeta,
  wrapSelection,
} from './actions'
import { AskPanel } from './AskPanel'
import { useAsk } from './askStore'
import { edgeTypes } from './edges'
import { exportBoardImage, type ImageKind } from './exportImage'
import { Icon } from './icons'
import { CLICK_SLOP, nodeTypes } from './nodes'
import { ancestorAtLevel, currentParent, useStore } from './store'
import { Shortcuts } from './Shortcuts'
import { ThemeSheet } from './ThemeSheet'
import type { MEdge, MNode } from './types'
import { useUI } from './ui'
import { Viewer } from './Viewer'

interface Level { rfNodes: Node[]; rfEdges: Edge[] }

/** A card while the board is in motion: where it is, and where it is going. */
interface Body { x: number; y: number; vx: number; vy: number }

function buildLevel(
  nodes: Record<string, MNode>, edges: Record<string, MEdge>,
  parent: string | null, jump: (id: string) => void,
): Level {
  const visible = Object.values(nodes).filter((n) => (n.parent ?? null) === parent)
  const rfNodes: Node[] = visible.map((n) => ({
    id: n.id,
    type: n.type,
    position: { x: n.pos[0], y: n.pos[1] },
    data: { node: n },
    style: { width: n.size[0] ?? undefined, height: n.size[1] ?? undefined },
  }))

  const groups = new Map<string, { s: string; t: string; items: MEdge[] }>()
  const rfEdges: Edge[] = []
  const ghostTally = new Map<string, number>()

  for (const e of Object.values(edges)) {
    const s = ancestorAtLevel(nodes, e.source, parent)
    const t = ancestorAtLevel(nodes, e.target, parent)
    if (s && t) {
      if (s === t) continue // both endpoints hide inside the same collapsed node
      const key = `${s}->${t}`
      const g = groups.get(key) ?? { s, t, items: [] }
      g.items.push(e)
      groups.set(key, g)
      continue
    }
    // exactly one side is visible: the other becomes a ghost you can click through
    const anchorId = s ?? t
    if (!anchorId) continue
    const anchor = nodes[anchorId]
    const farId = s ? e.target : e.source
    const far = nodes[farId]
    if (!anchor || !far) continue
    const gid = `ghost:${farId}`
    if (!rfNodes.some((n) => n.id === gid)) {
      const idx = ghostTally.get(anchorId) ?? 0
      ghostTally.set(anchorId, idx + 1)
      rfNodes.push({
        id: gid,
        type: 'ghost',
        position: { x: anchor.pos[0] + (s ? 430 : -310), y: anchor.pos[1] + idx * 64 },
        data: { title: far.title || far.type, targetId: farId, onJump: jump },
        selectable: false,
      })
    }
    rfEdges.push({
      id: `e:${e.id}`, source: s ? anchorId : gid, target: s ? gid : anchorId,
      type: 'labeled', data: { edge: e, ghost: true },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: 'var(--mb-edge)' },
    })
  }

  for (const g of groups.values()) {
    const single = g.items.length === 1
    const e = g.items[0]
    rfEdges.push({
      id: `e:${e.id}`, source: g.s, target: g.t, type: 'labeled',
      data: single ? { edge: e } : { edge: e, bundle: g.items },
      markerEnd: {
        type: MarkerType.ArrowClosed, width: 14, height: 14,
        color: getComputedStyle(document.documentElement)
          .getPropertyValue(`--mb-kind-${e.kind}`).trim() || undefined,
      },
    })
  }
  return { rfNodes, rfEdges }
}

function typing(t: EventTarget | null) {
  return t instanceof HTMLElement &&
    (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
}

function CanvasInner() {
  const nodes = useStore((s) => s.nodes)
  const edges = useStore((s) => s.edges)
  const path = useStore((s) => s.path)
  const board = useStore((s) => s.board)
  const enter = useStore((s) => s.enter)
  const goTo = useStore((s) => s.goTo)
  const undo = useStore((s) => s.undo)
  const redo = useStore((s) => s.redo)
  const say = useStore((s) => s.say)
  const setUI = useUI((s) => s.set)
  const minimap = useUI((s) => s.minimap)
  const themeOpen = useUI((s) => s.themeOpen)
  const focusId = useUI((s) => s.focusId)
  const linking = useUI((s) => s.linking)
  const selectedIds = useUI((s) => s.selected)
  const boardId = useStore((s) => s.boardId)

  // each board has its own context and conversation, kept in the tab
  useEffect(() => { useAsk.getState().load(boardId) }, [boardId])

  const {
    screenToFlowPosition, flowToScreenPosition, fitView, zoomIn, zoomOut, getNodes,
    getInternalNode, setCenter, getZoom,
  } = useReactFlow()
  const parent = currentParent(path)
  const wrap = useRef<HTMLDivElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null)
  const pointer = useRef<{ x: number; y: number } | null>(null)
  const connectStart = useRef<{ nodeId: string; x: number; y: number } | null>(null)

  const jump = useCallback((id: string) => {
    const all = useStore.getState().nodes
    const chain: string[] = []
    let cur = all[id]
    while (cur?.parent) { chain.unshift(cur.parent); cur = all[cur.parent] }
    useStore.setState({ path: chain })
    setUI('focusId', id)
  }, [setUI])

  const level = useMemo(() => buildLevel(nodes, edges, parent, jump), [nodes, edges, parent, jump])
  const [rfNodes, setRfNodes] = useState<Node[]>(level.rfNodes)
  const rfNodesRef = useRef<Node[]>(level.rfNodes)
  rfNodesRef.current = rfNodes
  useEffect(() => { setRfNodes(level.rfNodes) }, [level.rfNodes])

  // entering or leaving a meta-node lands you looking at its contents - unless
  // the move was to go to one card, which then gets the view to itself
  const firstFit = useRef(true)
  useEffect(() => {
    if (firstFit.current) { firstFit.current = false; return }
    if (useUI.getState().focusId) return
    const t = setTimeout(() => fitView({ duration: 320, padding: 0.3, maxZoom: 1 }), 40)
    return () => clearTimeout(t)
  }, [parent, fitView])

  /** Go to a card: centre it, zoomed so it reads, clear of the Ask panel, and
   *  flash it so the eye lands on it.
   *
   *  This used fitView on the card, which did nothing for any card off screen:
   *  the canvas renders only the cards in view (onlyRenderVisibleElements), and
   *  xyflow can only fit a card it has measured. So the card's place comes from
   *  the board itself, and its size from the canvas when known, else the card. */
  useEffect(() => {
    if (!focusId) return
    const t = setTimeout(() => {
      setUI('focusId', null)
      const node = useStore.getState().nodes[focusId]
      if (!node) return
      const internal = getInternalNode(focusId)
      const w = internal?.measured?.width ?? node.size[0] ?? 320
      const h = internal?.measured?.height ?? node.size[1] ?? 160
      const box = wrap.current?.getBoundingClientRect()
      // the conversation sits at the bottom: centre the card in what is
      // visible above it
      const sheet = document.querySelector('.ask-sheet')?.getBoundingClientRect()
      const viewW = box?.width ?? 1200
      const fullH = box?.height ?? 800
      const viewH = sheet ? Math.max(sheet.top - (box?.top ?? 0), 200) : fullH
      const z = Math.max(0.3, Math.min(1.15, (viewW * 0.6) / w, (viewH * 0.6) / h))
      setCenter(node.pos[0] + w / 2, node.pos[1] + h / 2 + (fullH - viewH) / 2 / z,
                { zoom: z, duration: 450 })
      setUI('foundId', focusId)
      setTimeout(() => {
        if (useUI.getState().foundId === focusId) setUI('foundId', null)
      }, 2400)
    }, 80)
    return () => clearTimeout(t)
  }, [focusId, getInternalNode, setCenter, setUI])

  // ------------------------------------------------------ image export --
  const runExport = useCallback(async (kind: ImageKind, scale: number) => {
    try {
      say('rendering...')
      const levelName = parent ? (useStore.getState().nodes[parent]?.title ?? '') : ''
      const out = await exportBoardImage({
        nodes: getNodes(), title: board?.title ?? 'board', kind, scale, levelName,
      })
      say(`saved ${out.filename} · ${Math.round(out.width * out.scale)}×`
        + `${Math.round(out.height * out.scale)}px`)
    } catch (err: any) {
      say(err.message || 'could not export that', true)
    }
  }, [board?.title, getNodes, parent, say])

  useEffect(() => {
    setUI('exportImage', runExport)
    return () => setUI('exportImage', null)
  }, [runExport, setUI])

  // -------------------------------------------------------------- aligning --
  // As a card comes within a few pixels of lining up with another - edges or
  // centres, across or down - it settles onto that line. Move a little further
  // and it lets go. Nothing is forced to a grid.
  //
  // This drew a hairline through both cards to say what it had met, the way
  // design tools do, and the verdict was "very annoying" - fairly: when the card
  // it matched was across the board, so was the line. Lining up is worth
  // having, announcing it is not.
  const SNAP = 6

  const alignTo = useCallback((id: string, pos: { x: number; y: number },
                               moving: Set<string>) => {
    const reach = SNAP / (getZoom() || 1)
    const self = getInternalNode(id)
    const w = self?.measured?.width ?? 300
    const h = self?.measured?.height ?? 120
    const mineX = [pos.x, pos.x + w / 2, pos.x + w]
    const mineY = [pos.y, pos.y + h / 2, pos.y + h]

    let dx = 0
    let dy = 0
    let bestX = reach
    let bestY = reach

    for (const n of rfNodesRef.current) {
      if (moving.has(n.id) || n.id.startsWith('ghost:')) continue
      const other = getInternalNode(n.id)
      const ow = other?.measured?.width ?? 300
      const oh = other?.measured?.height ?? 120
      const theirsX = [n.position.x, n.position.x + ow / 2, n.position.x + ow]
      const theirsY = [n.position.y, n.position.y + oh / 2, n.position.y + oh]
      for (const a of mineX) {
        for (const b of theirsX) {
          const d = Math.abs(b - a)
          if (d >= bestX) continue
          bestX = d
          dx = b - a
        }
      }
      for (const a of mineY) {
        for (const b of theirsY) {
          const d = Math.abs(b - a)
          if (d >= bestY) continue
          bestY = d
          dy = b - a
        }
      }
    }
    return { dx, dy }
  }, [getInternalNode, getZoom])

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const drags = changes.filter(
      (c) => c.type === 'position' && c.dragging && c.position,
    ) as (NodeChange & { id: string; position: { x: number; y: number } })[]

    // while Alt has the whole graph in motion, lining up with one card is noise
    if (!drags.length || sim.current) {
      setRfNodes((ns) => applyNodeChanges(changes, ns))
      return
    }

    const moving = new Set(drags.map((c) => c.id))
    const lead = drags[0]
    const { dx, dy } = alignTo(lead.id, lead.position, moving)
    const nudged = dx || dy
      ? changes.map((c) => (c.type === 'position' && c.position && moving.has(c.id)
        ? { ...c, position: { x: c.position.x + dx, y: c.position.y + dy } } : c))
      : changes
    setRfNodes((ns) => applyNodeChanges(nudged, ns))
  }, [alignTo])

  /** Stable, and writes only on a real change.
   *
   *  xyflow calls this from an effect. Something on this screen now *reads*
   *  `selected` (the multi-selection toolbar), so writing a fresh array every
   *  time re-rendered the canvas, which called this again - React tore the whole
   *  board down with "maximum update depth exceeded". */
  const onSelectionChange = useCallback(({ nodes: sel }: { nodes: Node[] }) => {
    const picked = sel.filter((n) => !n.id.startsWith('ghost:')).map((n) => n.id)
    const ui = useUI.getState()
    const same = picked.length === ui.selected.length
      && picked.every((id, i) => ui.selected[i] === id)
    if (!same) ui.set('selected', picked)
    // picking a card puts down whatever relationship was in hand
    if (picked.length && ui.selectedEdges.length) ui.set('selectedEdges', [])
  }, [])

  // ----------------------------------------------------- a map that moves --
  // Dragging a card moves that card, exactly where you put it, and nothing
  // else - arranging a board is the thing you do most, and it has to be
  // precise. Hold **Alt** and the links behave like springs instead: each
  // remembers how long it was when the drag began and pulls only once
  // stretched, cards carry momentum and lose it to damping, anything that
  // would land on top of something else is shoved clear, and the board keeps
  // drifting for a moment after you let go. A card on one link swings round to
  // keep its distance; a card held by four barely stirs.
  //
  // This started the other way round - physics by default - and the verdict
  // was that cards "stick on the canvas" and could not be arranged. Quite
  // right: a simulation is lovely to watch and hopeless to place things with.
  const SPRING = 0.2       // how hard a stretched link pulls back
  const DAMP = 0.82        // how quickly movement dies away
  const SETTLE_MS = 1600   // how long the board may keep drifting after a drop
  const STILL = 0.02       // below this a card has stopped

  const sim = useRef<{
    bodies: Map<string, Body>
    size: Map<string, [number, number]>
    springs: { a: string; b: string; rest: number }[]
    pinned: Set<string>
    raf: number
    until: number
    live: boolean
    done: (() => void) | null
  } | null>(null)

  const stopSim = useCallback(() => {
    if (sim.current?.raf) cancelAnimationFrame(sim.current.raf)
    sim.current = null
  }, [])
  useEffect(() => stopSim, [stopSim])

  /** The group a dragged card is currently over - drop it there and it goes in. */
  const groupUnder = useCallback((fx: number, fy: number, carried: Set<string>) => {
    for (const n of rfNodesRef.current) {
      if (carried.has(n.id) || n.id.startsWith('ghost:')) continue
      const mn = (n.data as { node?: MNode }).node
      if (!mn || mn.type !== 'meta') continue
      const internal = getInternalNode(n.id)
      const w = internal?.measured?.width ?? 320
      const h = internal?.measured?.height ?? 150
      if (fx >= n.position.x && fx <= n.position.x + w
        && fy >= n.position.y && fy <= n.position.y + h) return n.id
    }
    return null
  }, [getInternalNode])

  const stepSim = useCallback(() => {
    const s = sim.current
    if (!s) return
    const centre = (id: string, b: Body) => {
      const [w, h] = s.size.get(id) ?? [300, 120]
      return [b.x + w / 2, b.y + h / 2] as const
    }

    // whatever is under the cursor is wherever xyflow has just put it
    for (const id of s.pinned) {
      const n = rfNodesRef.current.find((x) => x.id === id)
      const b = s.bodies.get(id)
      if (n && b) { b.x = n.position.x; b.y = n.position.y; b.vx = 0; b.vy = 0 }
    }

    for (const sp of s.springs) {
      const A = s.bodies.get(sp.a)
      const B = s.bodies.get(sp.b)
      if (!A || !B) continue
      const [ax, ay] = centre(sp.a, A)
      const [bx, by] = centre(sp.b, B)
      const dx = bx - ax
      const dy = by - ay
      const len = Math.hypot(dx, dy) || 1
      const f = SPRING * (len - sp.rest) / len
      if (!s.pinned.has(sp.a)) { A.vx += f * dx; A.vy += f * dy }
      if (!s.pinned.has(sp.b)) { B.vx -= f * dx; B.vy -= f * dy }
    }

    // nothing should finish its journey sitting on top of something else
    const ids = [...s.bodies.keys()]
    if (ids.length <= 140) {
      for (let i = 0; i < ids.length; i++) {
        for (let j = i + 1; j < ids.length; j++) {
          const A = s.bodies.get(ids[i]) as Body
          const B = s.bodies.get(ids[j]) as Body
          const [aw, ah] = s.size.get(ids[i]) ?? [300, 120]
          const [bw, bh] = s.size.get(ids[j]) ?? [300, 120]
          const [ax, ay] = centre(ids[i], A)
          const [bx, by] = centre(ids[j], B)
          const dx = bx - ax
          const dy = by - ay
          const len = Math.hypot(dx, dy) || 1
          const clear = (Math.hypot(aw, ah) + Math.hypot(bw, bh)) * 0.39 + 18
          if (len >= clear) continue
          const f = ((clear - len) / len) * 0.14
          if (!s.pinned.has(ids[i])) { A.vx -= f * dx; A.vy -= f * dy }
          if (!s.pinned.has(ids[j])) { B.vx += f * dx; B.vy += f * dy }
        }
      }
    }

    let moving = 0
    for (const [id, b] of s.bodies) {
      if (s.pinned.has(id)) continue
      b.vx *= DAMP
      b.vy *= DAMP
      if (Math.abs(b.vx) < STILL) b.vx = 0
      if (Math.abs(b.vy) < STILL) b.vy = 0
      b.x += b.vx
      b.y += b.vy
      moving += Math.abs(b.vx) + Math.abs(b.vy)
    }

    setRfNodes((ns) => ns.map((n) => {
      const b = s.bodies.get(n.id)
      if (!b || s.pinned.has(n.id)) return n
      if (n.position.x === b.x && n.position.y === b.y) return n
      return { ...n, position: { x: b.x, y: b.y } }
    }))

    if (s.live || (moving > 0.4 && Date.now() < s.until)) {
      s.raf = requestAnimationFrame(stepSim)
    } else {
      const finish = s.done
      s.done = null
      finish?.()
    }
  }, [])

  const onNodeDragStart = useCallback((event: MouseEvent | TouchEvent, node: Node,
                                       dragged: Node[]) => {
    stopSim()
    if (!(event as MouseEvent).altKey || node.id.startsWith('ghost:')) return
    const pinned = new Set(dragged.map((n) => n.id))
    pinned.add(node.id)

    const bodies = new Map<string, Body>()
    const size = new Map<string, [number, number]>()
    for (const n of rfNodesRef.current) {
      if (n.id.startsWith('ghost:')) continue
      bodies.set(n.id, { x: n.position.x, y: n.position.y, vx: 0, vy: 0 })
      const internal = getInternalNode(n.id)
      size.set(n.id, [internal?.measured?.width ?? 300, internal?.measured?.height ?? 120])
    }

    const springs: { a: string; b: string; rest: number }[] = []
    for (const e of level.rfEdges) {
      const A = bodies.get(e.source)
      const B = bodies.get(e.target)
      if (!A || !B) continue
      const [aw, ah] = size.get(e.source) as [number, number]
      const [bw, bh] = size.get(e.target) as [number, number]
      const dx = (B.x + bw / 2) - (A.x + aw / 2)
      const dy = (B.y + bh / 2) - (A.y + ah / 2)
      // the length it has now is the length it wants: moving one card should
      // deform the map around it, never tidy the whole board behind your back
      springs.push({ a: e.source, b: e.target, rest: Math.hypot(dx, dy) })
    }
    if (!springs.length) return

    sim.current = { bodies, size, springs, pinned, raf: 0, until: 0, live: true, done: null }
    stepSim()
  }, [level.rfEdges, getInternalNode, stopSim, stepSim])

  const onNodeDrag = useCallback((event: MouseEvent | TouchEvent, node: Node,
                                  dragged: Node[]) => {
    const carried = new Set(dragged.map((n) => n.id))
    carried.add(node.id)
    const pt = 'touches' in event ? (event as TouchEvent).touches[0] : (event as MouseEvent)
    if (!pt) return
    const at = screenToFlowPosition({ x: pt.clientX, y: pt.clientY })
    const over = groupUnder(at.x, at.y, carried)
    if (over !== useUI.getState().dropTarget) setUI('dropTarget', over)
  }, [groupUnder, screenToFlowPosition, setUI])

  const commitMoves = useCallback((event: MouseEvent | TouchEvent, node: Node,
                                   dragged: Node[]) => {
    const carried = dragged.filter((n) => !n.id.startsWith('ghost:')).map((n) => n.id)
    if (!carried.includes(node.id) && !node.id.startsWith('ghost:')) carried.push(node.id)

    // Read here rather than from the last drag event: a quick flick can end
    // before one more onNodeDrag fires, and dropping into a group has to depend
    // on where the card actually landed.
    const pt = 'changedTouches' in event
      ? (event as TouchEvent).changedTouches[0] : (event as MouseEvent)
    const held = rfNodesRef.current.find((x) => x.id === node.id)
    const fallback = held
      ? { x: held.position.x + (getInternalNode(node.id)?.measured?.width ?? 300) / 2,
          y: held.position.y + 24 }
      : null
    const where = pt ? screenToFlowPosition({ x: pt.clientX, y: pt.clientY }) : fallback
    const into = where ? groupUnder(where.x, where.y, new Set(carried)) : null
    setUI('dropTarget', null)

    /** Written once the board has stopped moving - the card you dragged and
     *  everything that drifted with it, in one op, so one undo puts the whole
     *  arrangement back. */
    const settled = () => {
      const before = useStore.getState().nodes
      const moves: { id: string; x: number; y: number }[] = []
      for (const n of rfNodesRef.current) {
        if (n.id.startsWith('ghost:')) continue
        const was = before[n.id]
        const x = Math.round(n.position.x)
        const y = Math.round(n.position.y)
        if (!was || (was.pos[0] === x && was.pos[1] === y)) continue
        moves.push({ id: n.id, x, y })
      }
      if (moves.length) useStore.getState().run([{ kind: 'node.move', moves }])
      if (into) moveInto(carried, into)
    }

    const s = sim.current
    if (!s) { settled(); return }
    s.live = false
    s.until = Date.now() + SETTLE_MS
    s.done = () => { sim.current = null; settled() }
  }, [setUI, groupUnder, screenToFlowPosition, getInternalNode])

  const centre = useCallback((): [number, number] => {
    const box = wrap.current?.getBoundingClientRect()
    const p = screenToFlowPosition({
      x: (box?.left ?? 0) + (box?.width ?? 800) / 2,
      y: (box?.top ?? 0) + (box?.height ?? 600) / 2,
    })
    return [Math.round(p.x - 90), Math.round(p.y - 30)]
  }, [screenToFlowPosition])

  const stopLinking = useCallback(() => { setUI('linking', null); setCursor(null) }, [setUI])

  /** Bring a just-created node into view, but only if it landed off-screen —
   *  panning when you can already see it is just motion for its own sake. */
  const reveal = useCallback((id: string | null) => {
    if (!id) return
    setTimeout(() => {
      const box = wrap.current?.getBoundingClientRect()
      const internal = getInternalNode(id)
      if (!box || !internal) return
      const w = internal.measured?.width ?? 320
      const h = internal.measured?.height ?? 200
      const { x, y } = internal.internals.positionAbsolute
      const tl = flowToScreenPosition({ x, y })
      const br = flowToScreenPosition({ x: x + w, y: y + h })
      const visible = tl.x >= box.left && tl.y >= box.top
        && br.x <= box.right && br.y <= box.bottom
      if (!visible) setCenter(x + w / 2, y + h / 2, { duration: 320, zoom: getZoom() })
    }, 120)
  }, [flowToScreenPosition, getInternalNode, getZoom, setCenter])

  // ------------------------------------------------------------ keyboard --
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      if (typing(e.target)) {
        if (e.key === 'Escape') (e.target as HTMLElement).blur()
        return
      }
      const selected = useUI.getState().selected
      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault(); e.shiftKey ? redo() : undo(); return
      }
      if (mod && e.key.toLowerCase() === 'g') {
        e.preventDefault()
        if (e.shiftKey) { selected.filter((id) => nodes[id]?.type === 'meta').forEach(unwrapMeta) }
        else if (selected.length) wrapSelection(selected)
        return
      }
      if (mod && e.key.toLowerCase() === 'l') { e.preventDefault(); autoLayout(parent); return }
      if (mod && e.key.toLowerCase() === 'e') { e.preventDefault(); void runExport('png', 2); return }
      if (mod && e.key === '0') { e.preventDefault(); fitView({ duration: 300 }); return }
      if (mod && e.key.toLowerCase() === 'j') {
        e.preventDefault()
        // the chat bar is always there: Ctrl+J goes to it, or closes the
        // conversation if it is open
        const ask = useAsk.getState()
        if (ask.open) ask.setOpen(false)
        else document.querySelector<HTMLTextAreaElement>('.ask-bar textarea')?.focus()
        return
      }
      if (e.key === 'Escape') {
        if (useUI.getState().linking) { stopLinking(); return }
        if (useUI.getState().editingEdgeId) { setUI('editingEdgeId', null); return }
        if (path.length) goTo(path.length - 1)
        return
      }
      const selEdges = useUI.getState().selectedEdges
      if ((e.key === 'Delete' || e.key === 'Backspace') && (selected.length || selEdges.length)) {
        e.preventDefault()
        if (selEdges.length) deleteEdges(selEdges)
        if (selected.length) deleteNodes(selected)
        return
      }
      if (mod && e.key.toLowerCase() === 'd' && selected.length) {
        e.preventDefault()
        void duplicateNodes(selected).then((ids) => reveal(ids[0] ?? null))
        return
      }
      if (e.key === 'Enter' && selected.length === 1) {
        e.preventDefault(); setUI('editingId', selected[0]); return
      }
      // a — a new card lands where you are looking, not in the middle of nowhere
      if (!mod && e.key.toLowerCase() === 'a') {
        e.preventDefault()
        const at = pointer.current
        addTextNode(at ? [Math.round(at.x - 80), Math.round(at.y - 24)] : centre(), parent)
        return
      }
      // c — start a connection from the selected card, then click the other one
      if (!mod && e.key.toLowerCase() === 'c' && selected.length === 1) {
        e.preventDefault(); setUI('linking', { sourceId: selected[0] })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nodes, parent, path, undo, redo, goTo, setUI, fitView, runExport, stopLinking, reveal,
      centre])

  // ---------------------------------------------------------- copy / cut --
  // The clipboard gets the words, so pasting into any other app reads sensibly;
  // the copy kept here is what a paste back into Mindboard rebuilds from.
  useEffect(() => {
    const take = (e: ClipboardEvent, cut: boolean) => {
      if (typing(e.target)) return
      const picked = useUI.getState().selected
      if (!picked.length) return
      const clip = buildClip(picked)
      if (!clip) return
      e.preventDefault()
      e.clipboardData?.setData('text/plain', clipText(clip))
      setClip(clip)
      const n = clip.nodes.length
      say(`${cut ? 'cut' : 'copied'} ${n} node${n === 1 ? '' : 's'}`)
      if (cut) deleteNodes(picked)
    }
    const onCopy = (e: ClipboardEvent) => take(e, false)
    const onCut = (e: ClipboardEvent) => take(e, true)
    window.addEventListener('copy', onCopy)
    window.addEventListener('cut', onCut)
    return () => {
      window.removeEventListener('copy', onCopy)
      window.removeEventListener('cut', onCut)
    }
  }, [say])

  // --------------------------------------------------------------- paste --
  useEffect(() => {
    const onPaste = async (e: ClipboardEvent) => {
      if (typing(e.target)) return
      const files = Array.from(e.clipboardData?.files ?? [])
      if (files.length) {
        e.preventDefault()
        const [x, y] = centre()
        files.forEach((f, i) => void addFile(f, [x + i * 30, y + i * 30], parent,
          files.length === 1).then((id) => i === 0 && reveal(id)))
        return
      }
      const text = e.clipboardData?.getData('text/plain')?.replace(/\r\n/g, '\n').trim()
      // nodes copied from a board come back as nodes - matched against the copy
      // kept at copy time, so an unrelated clipboard never turns into cards
      const clip = getClip()
      if (clip && text === clipText(clip)) {
        e.preventDefault()
        const [cx, cy] = centre()
        void pasteClip(clip, [cx, cy], parent).then((ids) => reveal(ids[0] ?? null))
        return
      }
      if (!text) return
      e.preventDefault()
      const [x, y] = centre()
      if (/^https?:\/\/\S+$/.test(text)) { void addUrl(text, [x, y], parent).then(reveal); return }
      const node = addTextNode([x, y], parent)
      const lines = text.split('\n')
      useStore.getState().run([{
        kind: 'node.update', id: node.id,
        patch: { title: lines[0].slice(0, 80), content: { text: lines.slice(1).join('\n') } },
      }])
      setUI('editingId', null)
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [centre, parent, setUI, reveal])

  const crumbs = path.map((id) => nodes[id]).filter(Boolean)

  /** With more than one card in hand, the things you can do to them together
   *  should be in front of you - grouping especially, which nobody discovers
   *  as a keyboard shortcut. */
  const selectionBox = useMemo(() => {
    if (selectedIds.length < 2) return null
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const id of selectedIds) {
      const n = rfNodes.find((x) => x.id === id)
      if (!n) continue
      const internal = getInternalNode(id)
      const w = internal?.measured?.width ?? 320
      const h = internal?.measured?.height ?? 140
      minX = Math.min(minX, n.position.x); minY = Math.min(minY, n.position.y)
      maxX = Math.max(maxX, n.position.x + w); maxY = Math.max(maxY, n.position.y + h)
    }
    return Number.isFinite(minX) ? { x: (minX + maxX) / 2, y: minY } : null
  }, [selectedIds, rfNodes, getInternalNode])

  // the line that follows the cursor while a click-started link is open
  const linkPath = useMemo(() => {
    if (!linking || !cursor) return null
    const src = getInternalNode(linking.sourceId)
    if (!src) return null
    const p = src.internals.positionAbsolute
    const w = src.measured?.width ?? 160
    const h = src.measured?.height ?? 60
    return { x1: p.x + w / 2, y1: p.y + h / 2, x2: cursor.x, y2: cursor.y }
  }, [linking, cursor, getInternalNode])

  return (
    <div className={`stage ${linking ? 'linking' : ''}${selectedIds.length > 1 ? ' multi' : ''}`}
      ref={wrap}
      onMouseMove={(e) => {
        const at = screenToFlowPosition({ x: e.clientX, y: e.clientY })
        pointer.current = at
        if (useUI.getState().linking) setCursor(at)
      }}
      onDoubleClick={(e) => {
        const el = e.target as HTMLElement
        if (!el.classList.contains('react-flow__pane')) return
        const p = screenToFlowPosition({ x: e.clientX, y: e.clientY })
        addTextNode([Math.round(p.x - 80), Math.round(p.y - 24)], parent)
      }}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragOver(false) }}
      onDrop={(e) => {
        e.preventDefault(); setDragOver(false)
        const p = screenToFlowPosition({ x: e.clientX, y: e.clientY })
        const files = Array.from(e.dataTransfer.files)
        files.forEach((f, i) => void addFile(f, [p.x + i * 28, p.y + i * 28], parent,
          files.length === 1))
        const uri = e.dataTransfer.getData('text/uri-list')
        if (!files.length && uri) void addUrl(uri, [p.x, p.y], parent)
      }}>
      <ReactFlow
        nodes={rfNodes}
        edges={level.rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStart={onNodeDragStart}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={commitMoves}
        onSelectionChange={onSelectionChange}
        onConnect={(c) => {
          if (c.source && c.target) connect(c.source, c.target)
          stopLinking()
        }}
        onConnectStart={(event, params) => {
          const pt = 'touches' in event ? (event as TouchEvent).touches[0] : (event as MouseEvent)
          connectStart.current = params.nodeId
            ? { nodeId: params.nodeId, x: pt?.clientX ?? 0, y: pt?.clientY ?? 0 }
            : null
        }}
        onConnectEnd={(event, state) => {
          const start = connectStart.current
          connectStart.current = null
          if (state.isValid) return
          const pt = 'changedTouches' in event
            ? (event as TouchEvent).changedTouches[0] : (event as MouseEvent)
          if (!start) return
          const travelled = Math.hypot(pt.clientX - start.x, pt.clientY - start.y)
          if (travelled < CLICK_SLOP) {
            // a click on a port, not a drag - the port itself opened linking,
            // so all we must do here is not drop a node
            setCursor(screenToFlowPosition({ x: pt.clientX, y: pt.clientY }))
            return
          }
          const src = useStore.getState().nodes[start.nodeId]
          if (!src) return
          const p = screenToFlowPosition({ x: pt.clientX, y: pt.clientY })
          addConnected(src, [Math.round(p.x - 80), Math.round(p.y - 24)])
        }}
        onNodeClick={(_, n) => {
          const active = useUI.getState().linking
          if (!active || n.id.startsWith('ghost:') || n.id === active.sourceId) return
          connect(active.sourceId, n.id)
          stopLinking()
        }}
        onPaneClick={(e) => {
          setUI('selectedEdges', [])
          const active = useUI.getState().linking
          if (!active) return
          const src = useStore.getState().nodes[active.sourceId]
          stopLinking()
          if (!src) return
          const p = screenToFlowPosition({ x: e.clientX, y: e.clientY })
          addConnected(src, [Math.round(p.x - 80), Math.round(p.y - 24)])
        }}
        onNodeDoubleClick={(_, n) => {
          const node = nodes[n.id]
          if (!node) return                       // ghost endpoints are not editable
          if (node.type === 'meta') { enter(n.id); return }
          setUI('editingId', n.id)
        }}
        onEdgesDelete={(es) =>
          deleteEdges(es.map((e) => String(e.id).replace(/^e:/, '')).filter((id) => edges[id]))}
        connectionMode={ConnectionMode.Loose}
        connectOnClick={false}
        deleteKeyCode={null}
        multiSelectionKeyCode={['Shift', 'Meta', 'Control']}
        selectionOnDrag
        panOnDrag={[1, 2]}
        panOnScroll
        zoomOnDoubleClick={false}
        minZoom={0.08}
        maxZoom={2.5}
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
        fitView
        fitViewOptions={{ padding: 0.3, maxZoom: 1 }}>
        <Background variant={BackgroundVariant.Dots} gap={24} size={1.9} color="var(--mb-grid)" />
        {linkPath && (
          <ViewportPortal>
            <svg className="link-ghost" width={1} height={1}
              style={{ position: 'absolute', left: 0, top: 0, overflow: 'visible',
                pointerEvents: 'none' }}>
              <path d={`M${linkPath.x1} ${linkPath.y1} L${linkPath.x2} ${linkPath.y2}`} />
              <circle cx={linkPath.x2} cy={linkPath.y2} r={4.5} />
            </svg>
          </ViewportPortal>
        )}
        {selectionBox && (
          <ViewportPortal>
            <div className="selection-bar nodrag nopan"
              style={{ position: 'absolute', transform:
                `translate(-50%,-100%) translate(${selectionBox.x}px, ${selectionBox.y - 14}px)` }}>
              <span className="count">{selectedIds.length} selected</span>
              <span className="sep" />
              <button onClick={() => wrapSelection(selectedIds)}>
                <Icon name="layers" size={14} /> Group
              </button>
              <button onClick={() => void duplicateNodes(selectedIds)}>
                <Icon name="copy" size={14} /> Duplicate
              </button>
              <button title="Add these cards to the context and ask about them"
                onClick={() => useAsk.getState().add(selectedIds)}>
                <Icon name="askAdd" size={14} /> Ask
              </button>
              <button className="danger" onClick={() => deleteNodes(selectedIds)}>
                <Icon name="trash" size={14} />
              </button>
            </div>
          </ViewportPortal>
        )}
        {minimap && (
          <MiniMap pannable zoomable position="bottom-right"
            style={{ display: 'block', bottom: 62, right: 0, background: 'var(--mb-surface)',
              border: '1px solid var(--mb-border)', borderRadius: 8 }}
            maskColor="#0b0d1188"
            nodeColor={(n) => {
              const node = (n.data as { node?: MNode })?.node
              return node?.style?.color ?? 'var(--mb-border-strong)'
            }} />
        )}
      </ReactFlow>

      <Shortcuts />

      <div className="breadcrumb">
        <button onClick={() => useStore.getState().close()} title="All boards">
          <Icon name="grid" size={13} />
        </button>
        <span className="sep">/</span>
        {path.length === 0
          ? <span className="current">{board?.title ?? 'Board'}</span>
          : <button onClick={() => goTo(0)}>{board?.title ?? 'Board'}</button>}
        {crumbs.map((c, i) => (
          <span key={c.id} style={{ display: 'contents' }}>
            <span className="sep">/</span>
            {i === crumbs.length - 1
              ? <span className="current">{c.title || 'Group'}</span>
              : <button onClick={() => goTo(i + 1)}>{c.title || 'Group'}</button>}
          </span>
        ))}
      </div>

      <div className="dock">
        <button className="icon-btn" title="Zoom out" onClick={() => zoomOut({ duration: 150 })}>
          <Icon name="minus" size={14} />
        </button>
        <ZoomLevel />
        <button className="icon-btn" title="Zoom in" onClick={() => zoomIn({ duration: 150 })}>
          <Icon name="plus" size={14} />
        </button>
        <span className="sep" style={{ width: 1, height: 18, background: 'var(--mb-border)' }} />
        <button className="icon-btn" title="Fit to view  (Ctrl+0)" onClick={() => fitView({ duration: 300 })}>
          <Icon name="fit" size={14} />
        </button>
        <button className="icon-btn" title="Export this level as an image  (Ctrl+E)"
          onClick={() => void runExport('png', 2)}>
          <Icon name="image" size={14} />
        </button>
        <button className="icon-btn" title="Minimap" onClick={() => setUI('minimap', !minimap)}>
          <Icon name="grid" size={14} />
        </button>
        <button className="icon-btn" title="Theme" onClick={() => setUI('themeOpen', !themeOpen)}>
          <Icon name="palette" size={14} />
        </button>
      </div>

      <AskPanel />

      {themeOpen && <ThemeSheet />}
      {dragOver && <div className="drop-veil">drop to add</div>}
      {linking && (
        <div className="hint linking-hint">
          click a node to connect · click empty canvas for a new one · Esc to cancel
        </div>
      )}
      {!linking && Object.keys(nodes).length === 0 && (
        <div className="hint">double-click anywhere to start · drop a file · Ctrl+K for everything else</div>
      )}
    </div>
  )
}

export function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
      {/* outside the stage on purpose: pointer, double-click and drop events
          on the viewer must never bubble into the canvas's own handlers */}
      <Viewer />
    </ReactFlowProvider>
  )
}

/** The zoom percentage, subscribed on its own. It used to come from
 *  useViewport() in the canvas itself, which made the whole canvas - and with
 *  it every card and relationship - render again on every frame of a zoom or
 *  pan: 2 frames a second with 3,000 cards on a level (DESIGN.md 36). */
function ZoomLevel() {
  const pct = useFlow((s) => Math.round(s.transform[2] * 100))
  return <span className="zoom">{pct}%</span>
}
