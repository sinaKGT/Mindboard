import {
  Background, BackgroundVariant, ConnectionMode, MarkerType, MiniMap, ReactFlow,
  ReactFlowProvider, applyNodeChanges, useReactFlow, useViewport,
  type Edge, type Node, type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  addConnected, addFile, addTextNode, addUrl, autoLayout, connect, deleteEdges,
  deleteNodes, unwrapMeta, wrapSelection,
} from './actions'
import { edgeTypes, kindColour } from './edges'
import { Icon } from './icons'
import { nodeTypes } from './nodes'
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
        color: kindColour(e.kind).replace('var(', '').replace(')', '')
          ? getComputedStyle(document.documentElement).getPropertyValue(`--mb-kind-${e.kind}`).trim()
          : undefined,
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
  const setUI = useUI((s) => s.set)
  const minimap = useUI((s) => s.minimap)
  const themeOpen = useUI((s) => s.themeOpen)
  const focusId = useUI((s) => s.focusId)

  const { screenToFlowPosition, fitView, zoomIn, zoomOut } = useReactFlow()
  const { zoom } = useViewport()
  const parent = currentParent(path)
  const wrap = useRef<HTMLDivElement>(null)
  const [dragOver, setDragOver] = useState(false)

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

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setRfNodes((ns) => applyNodeChanges(changes, ns))
  }, [])

  const commitMoves = useCallback((_: unknown, __: Node, dragged: Node[]) => {
    const moves = dragged
      .filter((n) => !n.id.startsWith('ghost:'))
      .map((n) => ({ id: n.id, x: Math.round(n.position.x), y: Math.round(n.position.y) }))
    if (moves.length) useStore.getState().run([{ kind: 'node.move', moves }])
  }, [])

  const centre = useCallback((): [number, number] => {
    const box = wrap.current?.getBoundingClientRect()
    const p = screenToFlowPosition({
      x: (box?.left ?? 0) + (box?.width ?? 800) / 2,
      y: (box?.top ?? 0) + (box?.height ?? 600) / 2,
    })
    return [Math.round(p.x - 90), Math.round(p.y - 30)]
  }, [screenToFlowPosition])

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
      if (mod && e.key === '0') { e.preventDefault(); fitView({ duration: 300 }); return }
      if (e.key === 'Escape') {
        if (useUI.getState().editingEdgeId) { setUI('editingEdgeId', null); return }
        if (path.length) goTo(path.length - 1)
        return
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && selected.length) {
        e.preventDefault(); deleteNodes(selected); return
      }
      if (e.key === 'Enter' && selected.length === 1) {
        e.preventDefault(); setUI('editingId', selected[0])
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nodes, parent, path, undo, redo, goTo, setUI, fitView])

  // --------------------------------------------------------------- paste --
  useEffect(() => {
    const onPaste = async (e: ClipboardEvent) => {
      if (typing(e.target)) return
      const files = Array.from(e.clipboardData?.files ?? [])
      if (files.length) {
        e.preventDefault()
        const [x, y] = centre()
        files.forEach((f, i) => void addFile(f, [x + i * 30, y + i * 30], parent))
        return
      }
      const text = e.clipboardData?.getData('text/plain')?.trim()
      if (!text) return
      e.preventDefault()
      const [x, y] = centre()
      if (/^https?:\/\/\S+$/.test(text)) { void addUrl(text, [x, y], parent); return }
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
  }, [centre, parent, setUI])

  const crumbs = path.map((id) => nodes[id]).filter(Boolean)

  return (
    <div className="stage" ref={wrap}
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
        files.forEach((f, i) => void addFile(f, [p.x + i * 28, p.y + i * 28], parent))
        const uri = e.dataTransfer.getData('text/uri-list')
        if (!files.length && uri) void addUrl(uri, [p.x, p.y], parent)
      }}>
      <ReactFlow
        nodes={rfNodes}
        edges={level.rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStop={commitMoves}
        onSelectionChange={({ nodes: sel }) =>
          setUI('selected', sel.filter((n) => !n.id.startsWith('ghost:')).map((n) => n.id))}
        onConnect={(c) => c.source && c.target && connect(c.source, c.target)}
        onConnectEnd={(event, state) => {
          if (state.isValid) return
          const fromId = state.fromNode?.id
          const src = fromId ? useStore.getState().nodes[fromId] : undefined
          if (!src) return
          const pt = 'changedTouches' in event ? (event as TouchEvent).changedTouches[0] : (event as MouseEvent)
          const p = screenToFlowPosition({ x: pt.clientX, y: pt.clientY })
          // one gesture, one undo step - the node and its edge go in together
          addConnected(src, [Math.round(p.x - 80), Math.round(p.y - 24)])
        }}
        onNodeDoubleClick={(_, n) => { if (nodes[n.id]?.type === 'meta') enter(n.id) }}
        onEdgesDelete={(es) =>
          deleteEdges(es.map((e) => String(e.id).replace(/^e:/, '')).filter((id) => edges[id]))}
        connectionMode={ConnectionMode.Loose}
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
        <Background variant={BackgroundVariant.Dots} gap={26} size={1.4} color="var(--mb-grid)" />
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
        <button className="icon-btn" title="Minimap" onClick={() => setUI('minimap', !minimap)}>
          <Icon name="grid" size={14} />
        </button>
        <button className="icon-btn" title="Theme" onClick={() => setUI('themeOpen', !themeOpen)}>
          <Icon name="palette" size={14} />
        </button>
      </div>

      {themeOpen && <ThemeSheet />}
      {dragOver && <div className="drop-veil">drop to add</div>}
      {Object.keys(nodes).length === 0 && (
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
