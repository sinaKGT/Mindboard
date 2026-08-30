/** A miniature of a real graph. Used on launcher cards and inside collapsed
 *  meta-nodes, so both answer "what is in there?" without being opened. */

export interface MiniNode { x: number; y: number; t: string; c?: string }
export type MiniEdge = [number, number]

const TYPE_VAR: Record<string, string> = {
  text: 'var(--mb-type-text)', image: 'var(--mb-type-image)', video: 'var(--mb-type-video)',
  link: 'var(--mb-type-link)', file: 'var(--mb-type-file)', meta: 'var(--mb-type-meta)',
}

export function GraphMini({ nodes, edges, w = 100, h = 56, pad = 9, dense = false }: {
  nodes: MiniNode[]; edges: MiniEdge[]; w?: number; h?: number; pad?: number; dense?: boolean
}) {
  if (!nodes.length) return null
  const px = (n: MiniNode) => pad + n.x * (w - pad * 2)
  const py = (n: MiniNode) => pad + n.y * (h - pad * 2)
  const r = dense ? 2.3 : 2.5

  return (
    <svg className="mini" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet"
      aria-hidden="true">
      <g className="mini-edges">
        {edges.map(([a, b], i) => {
          const s = nodes[a]
          const t = nodes[b]
          if (!s || !t) return null
          const mx = (px(s) + px(t)) / 2
          const my = (py(s) + py(t)) / 2 - Math.abs(px(t) - px(s)) * 0.08
          return (
            <path key={i} style={{ ['--i' as string]: i }} pathLength={1}
              d={`M${px(s)} ${py(s)} Q${mx} ${my} ${px(t)} ${py(t)}`} />
          )
        })}
      </g>
      <g className="mini-nodes">
        {nodes.map((n, i) => (
          <circle key={i} style={{ ['--i' as string]: i }}
            cx={px(n)} cy={py(n)} r={n.t === 'meta' ? r * 1.45 : r}
            fill={n.c || TYPE_VAR[n.t] || 'var(--mb-text-faint)'} />
        ))}
      </g>
    </svg>
  )
}

/** Normalise absolute node positions into the unit square the miniature wants. */
export function toMini<T extends { pos: [number, number]; type: string; style?: { color?: string } }>(
  items: T[],
): MiniNode[] {
  if (!items.length) return []
  const xs = items.map((n) => n.pos[0])
  const ys = items.map((n) => n.pos[1])
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  const spanX = Math.max(Math.max(...xs) - minX, 1)
  const spanY = Math.max(Math.max(...ys) - minY, 1)
  return items.map((n) => ({
    x: (n.pos[0] - minX) / spanX,
    y: (n.pos[1] - minY) / spanY,
    t: n.type,
    ...(n.style?.color ? { c: n.style.color } : {}),
  }))
}

const UNITS: [number, string][] = [
  [60, 'just now'], [3600, 'm'], [86400, 'h'], [604800, 'd'],
]

/** Relative time, because "2h ago" tells you more about a board than a date. */
export function ago(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const secs = Math.max(0, (Date.now() - then) / 1000)
  if (secs < UNITS[0][0]) return 'just now'
  if (secs < UNITS[1][0]) return `${Math.floor(secs / 60)}m ago`
  if (secs < UNITS[2][0]) return `${Math.floor(secs / 3600)}h ago`
  if (secs < UNITS[3][0]) return `${Math.floor(secs / 86400)}d ago`
  return new Date(then).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}
