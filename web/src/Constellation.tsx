/** A gold constellation that draws itself above a dark desert horizon, on
 *  the first page (DESIGN.md 54) - the Experts' night desert, without its sand.
 *
 *  Each round places a dozen stars, then joins them one line at a time into a
 *  small graph (a spanning tree, nearest star first, and two more links),
 *  holds it, fades it, and draws another. Lines and stars only: no particles,
 *  nothing on every frame but CSS. For anyone who asked for less motion it is
 *  one constellation, already drawn, that stays.
 */
import { useEffect, useMemo, useState } from 'react'

const W = 520
const H = 190
const STARS = 12
const STEP = 0.42          // seconds between lines
const DRAW = 0.9           // seconds a line takes
const HOLD = 4.5           // seconds the finished constellation stays
const FADE = 1.2

const still = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches

/** a small seeded generator, so each round has its own shape */
function rng(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

interface Star { x: number; y: number; r: number }
interface Line { a: number; b: number; len: number }

function shape(seed: number): { stars: Star[]; lines: Line[] } {
  const rand = rng(seed * 9973 + 17)
  const stars: Star[] = []
  while (stars.length < STARS) {
    const s = { x: 30 + rand() * (W - 60), y: 16 + rand() * 118, r: 1.4 + rand() * 1.4 }
    if (stars.every((o) => Math.hypot(o.x - s.x, o.y - s.y) > 42)) stars.push(s)
  }
  const d = (i: number, j: number) => Math.hypot(stars[i].x - stars[j].x, stars[i].y - stars[j].y)
  // join them nearest first from the first star: a tree, in the order it grows
  const inTree = new Set([0])
  const lines: Line[] = []
  while (inTree.size < STARS) {
    let best: Line | null = null
    for (const i of inTree) for (let j = 0; j < STARS; j++) {
      if (inTree.has(j)) continue
      const len = d(i, j)
      if (!best || len < best.len) best = { a: i, b: j, len }
    }
    lines.push(best!)
    inTree.add(best!.b)
  }
  // and two short links that close small loops, as a real graph has
  const extra: Line[] = []
  for (let i = 0; i < STARS; i++) for (let j = i + 1; j < STARS; j++) {
    if (!lines.some((l) => (l.a === i && l.b === j) || (l.a === j && l.b === i))) extra.push({ a: i, b: j, len: d(i, j) })
  }
  extra.sort((p, q) => p.len - q.len)
  return { stars, lines: [...lines, ...extra.slice(0, 2)] }
}

export function Constellation() {
  const [round, setRound] = useState(() => Math.floor(Math.random() * 1000))
  const [fading, setFading] = useState(false)
  const { stars, lines } = useMemo(() => shape(round), [round])

  useEffect(() => {
    if (still) return
    const drawn = (lines.length * STEP + DRAW + HOLD) * 1000
    const fade = window.setTimeout(() => setFading(true), drawn)
    const next = window.setTimeout(() => { setFading(false); setRound((r) => r + 1) }, drawn + FADE * 1000)
    return () => { window.clearTimeout(fade); window.clearTimeout(next) }
  }, [round, lines.length])

  // when each star first lights: the moment the line that reaches it starts
  const lit = new Map<number, number>([[0, 0]])
  lines.forEach((l, i) => { if (!lit.has(l.b)) lit.set(l.b, i * STEP + DRAW * 0.7) })

  return (
    <svg className={`constellation${fading ? ' fading' : ''}${still ? ' still' : ''}`}
      viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      {/* the desert's horizon: two dune lines */}
      <path className="dune far" d={`M0 ${H - 30} C 110 ${H - 46}, 190 ${H - 22}, 300 ${H - 36} S 450 ${H - 44}, ${W} ${H - 30}`} />
      <path className="dune" d={`M0 ${H - 14} C 90 ${H - 30}, 210 ${H - 6}, 320 ${H - 20} S 470 ${H - 26}, ${W} ${H - 12}`} />
      <g key={round}>
        {lines.map((l, i) => (
          <line key={i} className="c-line" x1={stars[l.a].x} y1={stars[l.a].y} x2={stars[l.b].x} y2={stars[l.b].y}
            style={{ strokeDasharray: l.len, strokeDashoffset: still ? 0 : l.len, animationDelay: `${i * STEP}s` }} />
        ))}
        {stars.map((s, i) => (
          <circle key={i} className="c-star" cx={s.x} cy={s.y} r={s.r}
            style={{ animationDelay: `${lit.get(i) ?? 0}s, ${(lit.get(i) ?? 0) + 1.2 + (i % 4) * 0.7}s` }} />
        ))}
      </g>
    </svg>
  )
}
