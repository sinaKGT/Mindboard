import { Position, EdgeLabelRenderer, getBezierPath, useInternalNode, type EdgeProps } from '@xyflow/react'
import { useEffect, useRef, useState } from 'react'

import { deleteEdges, updateEdge } from './actions'
import { Icon } from './icons'
import type { EdgeKind, MEdge } from './types'
import { EDGE_KINDS, KIND_STYLE } from './types'
import { useUI } from './ui'

export const kindColour = (k: EdgeKind) => `var(--mb-kind-${k})`

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
  const setUI = useUI((s) => s.set)
  const [label, setLabel] = useState(edge?.label ?? '')
  const inputRef = useRef<HTMLInputElement>(null)

  const sourceNode = useInternalNode(source)
  const targetNode = useInternalNode(target)
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
      <path d={path} className={`mb-edge-path ${sparked ? 'spark' : ''}`} pathLength={1}
        style={{ stroke: colour, strokeWidth: spec.width, strokeDasharray: spec.dash }}
        markerEnd={edge?.directed !== false && spec.arrow ? props.markerEnd : undefined} />
      <path d={path} className="mb-edge-hit" onClick={() => edge && setUI('editingEdgeId', id)} />
      <EdgeLabelRenderer>
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
              {EDGE_KINDS.map((k) => (
                <button key={k} title={k} onMouseDown={(e) => e.preventDefault()}
                  onClick={() => updateEdge(edge.id, { kind: k })}
                  style={{ display: 'flex', padding: 2, opacity: k === kind ? 1 : 0.4 }}>
                  <span className="kind-dot" style={{ background: kindColour(k) }} />
                </button>
              ))}
              <button title="Delete" onMouseDown={(e) => e.preventDefault()}
                onClick={() => deleteEdges([edge.id])} style={{ display: 'flex', padding: 2 }}>
                <Icon name="trash" size={11} />
              </button>
            </div>
          ) : (
            <div className={`mb-edge-label ${edge?.label || bundled ? '' : 'empty'}`}
              onClick={() => edge && setUI('editingEdgeId', id)}
              title={bundled ? bundle.map((b) => b.label || b.kind).join(' · ') : undefined}>
              <span className="kind-dot" style={{ background: colour }} />
              {bundled ? `${bundle.length} relationships` : (edge?.label || 'name it')}
            </div>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}

export const edgeTypes = { labeled: LabeledEdge }
