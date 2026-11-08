/** The monuments experts stand as, sampled as grains of sand (DESIGN.md 40).
 *
 *  Each shape is a list of grains: where each sits, whether it draws an edge
 *  (edges are denser and brighter, so the form reads), and whether it belongs
 *  to the crown - the piece that floats apart, glows, blinks and sends grains
 *  up: a pyramid's capstone, an obelisk's pyramidion, the step pyramid's top
 *  block, the gateway's sun disc, the ankh's loop.
 */
import * as THREE from 'three'

export type ShapeName = 'pyramid' | 'obelisk' | 'steps' | 'pylon' | 'ankh'
// in the order they unlock, simplest first (experts.py UNLOCKS)
export const SHAPES: ShapeName[] = ['ankh', 'obelisk', 'pyramid', 'steps', 'pylon']
export const SHAPE_WORDS: Record<ShapeName, string> = {
  pyramid: 'Pyramid', obelisk: 'Obelisk', steps: 'Step pyramid', pylon: 'Temple gate', ankh: 'Ankh',
}

export interface Sampled {
  home: Float32Array; edge: Float32Array; crown: Float32Array
  top: number            // height of the crown's tip: where rising grains start
  middle: number         // height of the body's heart: where the sign appears
}

type V = THREE.Vector3
const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z)

class Grains {
  pos: number[] = []; edge: number[] = []; crown: number[] = []
  add(p: V, e: boolean, c: boolean) { this.pos.push(p.x, p.y, p.z); this.edge.push(e ? 1 : 0); this.crown.push(c ? 1 : 0) }
  /** grains spread over a triangle */
  tri(a: V, b: V, c: V, n: number, crown = false) {
    for (let i = 0; i < n; i++) {
      let u = Math.random(), w = Math.random()
      if (u + w > 1) { u = 1 - u; w = 1 - w }
      this.add(a.clone().multiplyScalar(1 - u - w).add(b.clone().multiplyScalar(u)).add(c.clone().multiplyScalar(w)), false, crown)
    }
  }
  quad(a: V, b: V, c: V, d: V, n: number, crown = false) { this.tri(a, b, c, n / 2, crown); this.tri(a, c, d, n / 2, crown) }
  /** a bright line of grains */
  seg(a: V, b: V, n: number, crown = false, jitter = 0.006) {
    for (let i = 0; i < n; i++) {
      const p = a.clone().lerp(b, Math.random())
      p.x += (Math.random() - 0.5) * jitter; p.y += (Math.random() - 0.5) * jitter; p.z += (Math.random() - 0.5) * jitter
      this.add(p, true, crown)
    }
  }
  /** grains on the surface of a tube along a curve */
  tube(at: (t: number) => V, r: number, n: number, crown = false) {
    for (let i = 0; i < n; i++) {
      const t = Math.random(), p = at(t), q = at(Math.min(1, t + 0.01)).sub(at(Math.max(0, t - 0.01))).normalize()
      const side = new THREE.Vector3(0, 0, 1).cross(q).normalize()
      if (side.lengthSq() < 0.1) side.set(1, 0, 0)
      const up = q.clone().cross(side).normalize(), a = Math.random() * Math.PI * 2
      const onEdge = Math.random() < 0.18
      p.add(side.multiplyScalar(Math.cos(a) * r)).add(up.multiplyScalar(Math.sin(a) * r))
      this.add(p, onEdge, crown)
    }
  }
  /** a square frustum's four sides and their edges */
  frustum(y0: number, y1: number, w0: number, d0: number, w1: number, d1: number, n: number, crown = false, cx = 0) {
    const lo = [v(cx - w0, y0, -d0), v(cx + w0, y0, -d0), v(cx + w0, y0, d0), v(cx - w0, y0, d0)]
    const hi = [v(cx - w1, y1, -d1), v(cx + w1, y1, -d1), v(cx + w1, y1, d1), v(cx - w1, y1, d1)]
    for (let k = 0; k < 4; k++) {
      const j = (k + 1) % 4
      this.quad(lo[k], lo[j], hi[j], hi[k], n / 4, crown)
      this.seg(lo[k], hi[k], n * 0.03, crown)
      this.seg(lo[k], lo[j], n * 0.03, crown)
      this.seg(hi[k], hi[j], n * 0.03, crown)
    }
  }
  done(top: number, middle: number): Sampled {
    return { home: new Float32Array(this.pos), edge: new Float32Array(this.edge),
             crown: new Float32Array(this.crown), top, middle }
  }
}

function pyramid(n: number): Sampled {
  const g = new Grains(), B = 1.0, H = 1.3, CUT = 1.0, s = B * (1 - CUT / H)
  const apex = v(0, H, 0), base = [v(-B, 0, -B), v(B, 0, -B), v(B, 0, B), v(-B, 0, B)]
  const cut = base.map((c) => c.clone().lerp(apex, CUT / H))
  for (let k = 0; k < 4; k++) {
    const j = (k + 1) % 4
    g.quad(base[k], base[j], cut[j], cut[k], n * 0.18)
    g.tri(cut[k], cut[j], apex, n * 0.02, true)
    g.seg(base[k], cut[k], n * 0.035); g.seg(cut[k], apex, n * 0.012, true)
    g.seg(base[k], base[j], n * 0.02)
    g.seg(cut[k], cut[j], n * 0.008); g.seg(cut[k], cut[j], n * 0.008, true)
  }
  void s
  return g.done(H, 0.42)
}

function obelisk(n: number): Sampled {
  const g = new Grains(), H = 2.3, w0 = 0.26, w1 = 0.17
  g.frustum(0, H, w0, w0, w1, w1, n * 0.85)
  const top = v(0, H + 0.34, 0), c = [v(-w1, H, -w1), v(w1, H, -w1), v(w1, H, w1), v(-w1, H, w1)]
  for (let k = 0; k < 4; k++) {
    const j = (k + 1) % 4
    g.tri(c[k], c[j], top, n * 0.03, true); g.seg(c[k], top, n * 0.01, true); g.seg(c[k], c[j], n * 0.006, true)
  }
  return g.done(H + 0.34, 0.9)
}

function steps(n: number): Sampled {
  const g = new Grains(), levels = 5, h = 0.22
  for (let k = 0; k < levels; k++) {
    const w = 1.05 - k * 0.19, y = k * h
    g.frustum(y, y + h, w, w * 0.9, w - 0.02, w * 0.9 - 0.02, n * 0.15)
    // the ledge on top of each terrace
    const nw = w - 0.19, top = y + h
    g.quad(v(-w, top, -w * 0.9), v(w, top, -w * 0.9), v(nw, top, -nw * 0.9), v(-nw, top, -nw * 0.9), n * 0.012)
    g.quad(v(-w, top, w * 0.9), v(w, top, w * 0.9), v(nw, top, nw * 0.9), v(-nw, top, nw * 0.9), n * 0.012)
  }
  const y = levels * h
  g.frustum(y, y + 0.2, 0.16, 0.14, 0.14, 0.12, n * 0.05, true)     // the crown block
  return g.done(y + 0.2, 0.45)
}

function pylon(n: number): Sampled {
  const g = new Grains(), H = 1.25
  // two battered towers, leaning in as they rise, with the gate between
  g.frustum(0, H, 0.42, 0.3, 0.32, 0.22, n * 0.36, false, -0.62)
  g.frustum(0, H, 0.42, 0.3, 0.32, 0.22, n * 0.36, false, 0.62)
  // the gate's lintel
  g.frustum(0.82, 1.0, 0.24, 0.18, 0.24, 0.18, n * 0.07, false, 0)
  // the winged sun disc above the gate: the crown
  const cy = H + 0.28
  for (let i = 0; i < n * 0.08; i++) {
    const a = Math.random() * Math.PI * 2, r = 0.16 * Math.sqrt(Math.random())
    g.add(v(Math.cos(a) * r, cy + Math.sin(a) * r, (Math.random() - 0.5) * 0.06), r > 0.14, true)
  }
  for (const side of [-1, 1]) {
    g.tube((t) => v(side * (0.18 + 0.6 * t), cy + 0.02 + 0.1 * Math.sin(Math.PI * t) - 0.05 * t, 0), 0.025, n * 0.03, true)
  }
  return g.done(cy + 0.16, 0.55)
}

function ankh(n: number): Sampled {
  const g = new Grains(), r = 0.075
  g.tube((t) => v(0, t * 1.12, 0), r, n * 0.32)                       // the staff
  g.tube((t) => v(-0.48 + 0.96 * t, 1.0, 0), r, n * 0.22)             // the arms
  // the loop, the crown: an upright ellipse above the arms
  g.tube((t) => { const a = -Math.PI / 2 + t * Math.PI * 2; return v(Math.cos(a) * 0.27, 1.43 + Math.sin(a) * 0.35, 0) }, r, n * 0.36, true)
  return g.done(1.78, 0.6)
}

const MAKERS: Record<ShapeName, (n: number) => Sampled> = { pyramid, obelisk, steps, pylon, ankh }

export function sample(shape: string, n = 26000): Sampled {
  return (MAKERS[shape as ShapeName] ?? pyramid)(n)
}
