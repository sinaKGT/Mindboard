import {
  Background, BackgroundVariant, ConnectionMode, MarkerType, MiniMap, ReactFlow,
  ReactFlowProvider, ViewportPortal, applyNodeChanges, useReactFlow, useViewport,
  type Edge, type Node, type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  addConnected, addFile, addTextNode, addUrl, autoLayout, buildClip, clipText, connect,
  deleteEdges, deleteNodes, duplicateNodes, getClip, moveInto, pasteClip, setClip, unwrapMeta,
  wrapSelection,
} from './actions'
import { edgeTypes } from './edges'
import { exportBoardImage, type ImageKind } from './exportImage'
import { Icon } from './icons'
import { CLICK_SLOP, nodeTypes } from './nodes'
import { ancestorAtLevel, currentParent, useStore } from './store'
import { ThemeSheet } from './ThemeSheet'
import type { MEdge, MNode } from './types'
import { useUI } from './ui'

interface Level { rfNodes: Node[]; rfEdges: Edge[] }

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

  const {
    screenToFlowPosition, flowToScreenPosition, fitView, zoomIn, zoomOut, getNodes,
    getInternalNode, setCenter, getZoom,
  } = useReactFlow()
  const { zoom } = useViewport()
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

  // entering or leaving a meta-node lands you looking at its contents
  const firstFit = useRef(true)
  useEffect(() => {
    if (firstFit.current) { firstFit.current = false; return }
    const t = setTimeout(() => fitView({ duration: 320, padding: 0.3, maxZoom: 1 }), 40)
    return () => clearTimeout(t)
  }, [parent, fitView])

  useEffect(() => {
    if (!focusId) return
    const t = setTimeout(() => {
      fitView({ nodes: [{ id: focusId }], duration: 400, maxZoom: 1.1, padding: 0.35 })
      setUI('focusId', null)
    }, 60)
    return () => clearTimeout(t)
  }, [focusId, fitView, setUI])

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

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setRfNodes((ns) => applyNodeChanges(changes, ns))
  }, [])

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

  // --------------------------------------------------------- elastic drag --
  // A mind map is held together by its relationships, not by a grid: pull one
  // card and what it is connected to comes along, less and less with distance,
  // so the shape of an idea survives being moved. Hold Alt to move one alone.
  const HOP_PULL = [0.55, 0.26, 0.1]
  const drag = useRef<{
    from: Map<string, { x: number; y: number }>
    pull: Map<string, number>
    lead: string
  } | null>(null)

  const onNodeDragStart = useCallback((event: MouseEvent | TouchEvent, node: Node,
                                       dragged: Node[]) => {
    drag.current = null
    if ((event as MouseEvent).altKey || node.id.startsWith('ghost:')) return
    const moving = new Set(dragged.map((n) => n.id))
    moving.add(node.id)

    const near = new Map<string, string[]>()
    const join = (a: string, b: string) => {
      const list = near.get(a)
      if (list) list.push(b)
      else near.set(a, [b])
    }
    for (const e of level.rfEdges) {
      if (e.source.startsWith('ghost:') || e.target.startsWith('ghost:')) continue
      join(e.source, e.target)
      join(e.target, e.source)
    }

    const pull = new Map<string, number>()
    let ring = [...moving]
    const seen = new Set(ring)
    for (const strength of HOP_PULL) {
      const next: string[] = []
      for (const id of ring) {
        for (const other of near.get(id) ?? []) {
          if (seen.has(other)) continue
          seen.add(other)
          pull.set(other, strength)
          next.push(other)
        }
      }
      if (!next.length) break
      ring = next
    }
    if (!pull.size) return

    const from = new Map<string, { x: number; y: number }>()
    for (const n of rfNodesRef.current) {
      if (pull.has(n.id) || n.id === node.id) from.set(n.id, { ...n.position })
    }
    drag.current = { from, pull, lead: node.id }
  }, [level.rfEdges])

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

  const onNodeDrag = useCallback((event: MouseEvent | TouchEvent, node: Node, dragged: Node[]) => {
    const carried = new Set(dragged.map((n) => n.id))
    carried.add(node.id)
    const pt = 'touches' in event ? (event as TouchEvent).touches[0] : (event as MouseEvent)
    if (pt) {
      const at = screenToFlowPosition({ x: pt.clientX, y: pt.clientY })
      const over = groupUnder(at.x, at.y, carried)
      if (over !== useUI.getState().dropTarget) setUI('dropTarget', over)
    }

    const d = drag.current
    if (!d || node.id !== d.lead) return
    const start = d.from.get(node.id)
    if (!start) return
    const dx = node.position.x - start.x
    const dy = node.position.y - start.y
    setRfNodes((ns) => ns.map((n) => {
      const k = d.pull.get(n.id)
      const origin = d.from.get(n.id)
      if (!k || !origin) return n
      return { ...n, position: { x: origin.x + dx * k, y: origin.y + dy * k } }
    }))
  }, [groupUnder, screenToFlowPosition, setUI])

  const commitMoves = useCallback((event: MouseEvent | TouchEvent, node: Node,
                                   dragged: Node[]) => {
    const seen = new Set<string>()
    const carried = dragged.filter((n) => !n.id.startsWith('ghost:')).map((n) => n.id)
    if (!carried.includes(node.id) && !node.id.startsWith('ghost:')) carried.push(node.id)
    const moves = carried.map((id) => {
      const n = rfNodesRef.current.find((x) => x.id === id)
      return { id, x: Math.round(n?.position.x ?? 0), y: Math.round(n?.position.y ?? 0) }
    })
    moves.forEach((m) => seen.add(m.id))
    const d = drag.current
    if (d) {
      // the followers land in the same op, so one undo puts everything back
      for (const n of rfNodesRef.current) {
        if (!d.pull.has(n.id) || seen.has(n.id)) continue
        moves.push({ id: n.id, x: Math.round(n.position.x), y: Math.round(n.position.y) })
      }
    }
    drag.current = null

    // Decided here rather than from the last drag event: a quick flick can end
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
    if (moves.length) useStore.getState().run([{ kind: 'node.move', moves }])
    if (into) moveInto(carried, into)
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
        <span className="zoom">{Math.round(zoom * 100)}%</span>
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
    </ReactFlowProvider>
  )
}
