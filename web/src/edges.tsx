import {
  EdgeLabelRenderer, Position, getBezierPath, useInternalNode, useReactFlow,
  useStore as useFlow,
  type EdgeProps,
} from '@xyflow/react'
import { memo, useEffect, useRef, useState } from 'react'

import { deleteEdges, updateEdge } from './actions'
import { Icon } from './icons'
import type { EdgeKind, MEdge } from './types'
import { EDGE_KINDS, KIND_STYLE } from './types'
import { useUI } from './ui'

export const kindColour = (k: EdgeKind) => `var(--mb-kind-${k})`

/** Every relationship gets its own mark, not only its own colour - colour alone
 *  cannot be read by everyone, and cannot be read at all in a printed export. */
const GLYPHS: Record<EdgeKind, JSX.Element> = {
  'relates': (
    <>
      <circle cx="4" cy="8" r="1.6" fill="currentColor" stroke="none" />
      <circle cx="12" cy="8" r="1.6" fill="currentColor" stroke="none" />
      <path d="M5.8 8h4.4" />
    </>
  ),
  'causes': <><path d="M3 8h8" /><path d="M8.2 5l3 3-3 3" /></>,
  'supports': <path d="M3.5 8.4l3.1 3.1L12.5 4.9" />,
  'contradicts': <><path d="M4.6 4.6l6.8 6.8" /><path d="M11.4 4.6l-6.8 6.8" /></>,
  'part-of': (
    <>
      <rect x="3" y="3" width="10" height="10" rx="2" />
      <rect x="5.9" y="5.9" width="4.2" height="4.2" rx="1" fill="currentColor" stroke="none" />
    </>
  ),
  'example-of': (
    <>
      <path d="M8 3.4 12.6 8 8 12.6 3.4 8Z" />
      <circle cx="8" cy="8" r="1.2" fill="currentColor" stroke="none" />
    </>
  ),
}

export const KIND_LABEL: Record<EdgeKind, string> = {
  'relates': 'relates to', 'causes': 'causes', 'supports': 'supports',
  'contradicts': 'contradicts', 'part-of': 'is part of', 'example-of': 'is an example of',
}

export function KindGlyph({ kind, size = 13 }: { kind: EdgeKind; size?: number }) {
  return (
    <svg className="kind-glyph" width={size} height={size} viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true">
      {GLYPHS[kind]}
    </svg>
  )
}

type Internal = ReturnType<typeof useInternalNode>

function box(n: NonNullable<Internal>) {
  const p = n.internals.positionAbsolute
  return {
    x: p.x, y: p.y,
    w: n.measured?.width ?? (n.width as number) ?? 160,
    h: n.measured?.height ?? (n.height as number) ?? 60,
  }
}

/** Floating edges: pick the sides that actually face each other, so a
 *  relationship never loops around the back of its own node. */
function facing(a: NonNullable<Internal>, b: NonNullable<Internal>) {
  const A = box(a)
  const B = box(b)
  const dx = (B.x + B.w / 2) - (A.x + A.w / 2)
  const dy = (B.y + B.h / 2) - (A.y + A.h / 2)
  const horizontal = Math.abs(dx) * 1.15 > Math.abs(dy)
  const sp = horizontal ? (dx > 0 ? Position.Right : Position.Left)
    : (dy > 0 ? Position.Bottom : Position.Top)
  const tp = horizontal ? (dx > 0 ? Position.Left : Position.Right)
    : (dy > 0 ? Position.Top : Position.Bottom)
  return { sp, tp, a: anchor(A, sp), b: anchor(B, tp) }
}

function anchor(r: { x: number; y: number; w: number; h: number }, p: Position) {
  switch (p) {
    case Position.Left: return { x: r.x, y: r.y + r.h / 2 }
    case Position.Right: return { x: r.x + r.w, y: r.y + r.h / 2 }
    case Position.Top: return { x: r.x + r.w / 2, y: r.y }
    default: return { x: r.x + r.w / 2, y: r.y + r.h }
  }
}

export function LabeledEdge(props: EdgeProps) {
  const { id, source, target, data } = props
  const d = (data ?? {}) as { edge?: MEdge; bundle?: MEdge[]; ghost?: boolean }
  const edge = d.edge
  const bundle = d.bundle ?? []
  const kind: EdgeKind = edge?.kind ?? 'relates'
  const spec = KIND_STYLE[kind]
  const editing = useUI((s) => s.editingEdgeId === id)
  // zoomed far out a label is unreadable and only costs: leave it out
  // (a boolean, so this renders again only when the line is crossed)
  const far = useFlow((s) => s.transform[2] < 0.45)
  const setUI = useUI((s) => s.set)
  const [label, setLabel] = useState(edge?.label ?? '')
  const inputRef = useRef<HTMLInputElement>(null)

  const sourceNode = useInternalNode(source)
  const targetNode = useInternalNode(target)
  const { setNodes } = useReactFlow()
  const selected = useUI((s) => (edge ? s.selectedEdges.includes(edge.id) : false))

  /** A relationship you can pick up: one click selects it (Delete removes it,
   *  and its own X sits right there), a double click renames it. Selection is
   *  kept here rather than in the canvas library, which owns only the nodes. */
  const pick = () => {
    if (!edge) return
    setUI('selectedEdges', [edge.id])
    setNodes((ns) => ns.map((n) => (n.selected ? { ...n, selected: false } : n)))
  }
  const remove = () => {
    if (!edge) return
    setUI('selectedEdges', [])
    deleteEdges([edge.id])
  }
  const sparked = useUI((s) => (edge ? s.sparkIds.includes(edge.id) : false))

  useEffect(() => { if (!editing) setLabel(edge?.label ?? '') }, [edge?.label, editing])
  useEffect(() => { if (editing) { inputRef.current?.focus(); inputRef.current?.select() } }, [editing])

  if (!sourceNode || !targetNode) return null
  const f = facing(sourceNode, targetNode)
  const [path, lx, ly] = getBezierPath({
    sourceX: f.a.x, sourceY: f.a.y, sourcePosition: f.sp,
    targetX: f.b.x, targetY: f.b.y, targetPosition: f.tp,
    curvature: 0.24,
  })

  const commit = () => {
    if (edge && label !== edge.label) updateEdge(edge.id, { label })
    setUI('editingEdgeId', null)
  }

  const colour = d.ghost ? 'var(--mb-edge)' : kindColour(kind)
  const bundled = bundle.length > 1

  return (
    <>
      {/* fill and stroke live on the element, not only in CSS: an exported
          image is a clone, and a path that loses `fill: none` fills itself black */}
      {selected && (
        <path d={path} className="mb-edge-halo" fill="none"
          style={{ fill: 'none', stroke: colour, strokeWidth: spec.width + 7, opacity: 0.22 }} />
      )}
      <path d={path} className={`mb-edge-path ${sparked ? 'spark' : ''}`} pathLength={1}
        fill="none"
        style={{ fill: 'none', stroke: colour, strokeWidth: spec.width + (selected ? 1.2 : 0),
                 strokeDasharray: spec.dash }}
        markerEnd={edge?.directed !== false && spec.arrow ? props.markerEnd : undefined} />
      <path d={path} className="mb-edge-hit" fill="none" stroke="transparent"
        style={{ fill: 'none', stroke: 'transparent', strokeWidth: 22 }}
        onClick={pick}
        onDoubleClick={() => edge && setUI('editingEdgeId', id)} />
      {far && !selected && !editing ? null : <EdgeLabelRenderer>
        <div className="nodrag nopan"
          style={{ position: 'absolute', transform: `translate(-50%,-50%) translate(${lx}px,${ly}px)`, pointerEvents: 'all' }}>
          {editing && edge ? (
            <div className="mb-edge-label editing" style={{ borderColor: colour }}>
              <input ref={inputRef} value={label} placeholder="name this relationship"
                onChange={(e) => setLabel(e.target.value)}
                onBlur={commit}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commit()
                  if (e.key === 'Escape') { setLabel(edge.label); setUI('editingEdgeId', null) }
                }} />
              <span className="kind-picker">
                {EDGE_KINDS.map((k) => (
                  <button key={k} title={KIND_LABEL[k]} onMouseDown={(e) => e.preventDefault()}
                    onClick={() => updateEdge(edge.id, { kind: k })}
                    className={k === kind ? 'on' : ''}
                    style={{ color: kindColour(k) }}>
                    <KindGlyph kind={k} size={14} />
                  </button>
                ))}
              </span>
              <button title="Delete" className="edge-del" onMouseDown={(e) => e.preventDefault()}
                onClick={remove}>
                <Icon name="trash" size={12} />
              </button>
            </div>
          ) : (
            <div className={`mb-edge-label ${selected ? 'picked' : ''}`}
              style={selected ? { borderColor: colour } : undefined}
              onClick={pick}
              onDoubleClick={() => edge && setUI('editingEdgeId', id)}
              title={bundled
                ? bundle.map((b) => b.label || KIND_LABEL[b.kind]).join(' · ')
                : (edge ? `${KIND_LABEL[kind]}${edge.label ? ` — ${edge.label}` : ''}` : '')}>
              <span style={{ color: colour, display: 'flex' }}>
                <KindGlyph kind={kind} />
              </span>
              {/* the relationship says what it is in words, not colour alone */}
              <span className="kind-word">{KIND_LABEL[kind]}</span>
              {edge?.label ? <span className="edge-note">{edge.label}</span> : null}
              {bundled ? <span className="count">+{bundle.length - 1}</span> : null}
              {selected && edge ? (
                <button className="edge-del" title="Remove this relationship"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={(e) => { e.stopPropagation(); remove() }}>
                  <Icon name="x" size={11} />
                </button>
              ) : null}
            </div>
          )}
        </div>
      </EdgeLabelRenderer>}
    </>
  )
}

export const edgeTypes = { labeled: memo(LabeledEdge) }
