/** The sign each monument holds inside, shown in light when the pointer rests
 *  on it (DESIGN.md 40) - one per shape, each with real depth:
 *
 *  - pyramid: the Eye of Horus, knowledge
 *  - obelisk: the sun disc of Ra, with its rays
 *  - step pyramid: the djed pillar, stability
 *  - temple gate: the winged scarab, renewal
 *  - ankh: the feather of Maat, truth
 *
 *  Each returns the grains' places, about a unit across, centred on 0.
 */
type Pts = number[]
const R = Math.random

function pts() {
  const to: Pts = []
  const add = (x: number, y: number, z: number) => to.push(x, y, z)
  /** grains along a 2D curve, given depth */
  const curve = (f: (t: number) => [number, number], n: number, w = 0.012, depth = 0.12) => {
    for (let k = 0; k < n; k++) {
      const [x, y] = f(R())
      add(x + (R() - 0.5) * w, y + (R() - 0.5) * w, (R() - 0.5) * depth)
    }
  }
  /** grains on a sphere's surface */
  const sphere = (cx: number, cy: number, r: number, n: number, sz = 1) => {
    for (let k = 0; k < n; k++) {
      const u = R() * 2 - 1, a = R() * Math.PI * 2, q = Math.sqrt(1 - u * u)
      add(cx + r * q * Math.cos(a), cy + r * u, r * q * Math.sin(a) * sz)
    }
  }
  /** grains on a vertical cylinder */
  const cylinder = (y0: number, y1: number, r: number, n: number, rz = r) => {
    for (let k = 0; k < n; k++) {
      const a = R() * Math.PI * 2
      add(Math.cos(a) * r, y0 + (y1 - y0) * R(), Math.sin(a) * rz)
    }
  }
  return { to, add, curve, sphere, cylinder }
}

function eye(): Pts {
  const { to, add, curve } = pts()
  curve((t) => [-0.46 + 0.92 * t, 0.2 * Math.sin(Math.PI * t)], 700)
  curve((t) => [-0.46 + 0.92 * t, -0.11 * Math.sin(Math.PI * t)], 600)
  curve((t) => [0.46 + 0.22 * t, 0.01 - 0.03 * t], 160)
  curve((t) => { const a = t * Math.PI * 2; return [0.02 + Math.cos(a) * 0.1, 0.035 + Math.sin(a) * 0.1] }, 500)
  for (let k = 0; k < 700; k++) {
    const u = R() * 2 - 1, a = R() * 6.283, q = Math.sqrt(1 - u * u)
    add(0.02 + 0.095 * q * Math.cos(a), 0.035 + 0.095 * u, 0.095 * q * Math.sin(a))
  }
  curve((t) => [-0.5 + 1.02 * t, 0.31 + 0.05 * Math.sin(Math.PI * t) - 0.04 * t], 700, 0.03)
  curve((t) => [-0.03 - 0.03 * t, -0.11 - 0.33 * t], 300, 0.02)
  curve((t) => {
    if (t < 0.45) { const u = t / 0.45; return [0.06 + 0.22 * u, -0.1 - 0.16 * u] }
    const u = (t - 0.45) / 0.55, a = -Math.PI / 2 + u * Math.PI * 1.7, r = 0.09 * (1 - u * 0.7)
    return [0.28 + r * Math.cos(a), -0.26 + r * Math.sin(a)]
  }, 700, 0.016)
  return to
}

function sun(): Pts {
  const { to, add, sphere } = pts()
  sphere(0, 0, 0.2, 1700)                                     // the disc of Ra
  for (let k = 0; k < 1300; k++) {                            // its rays, all around, in depth
    const ray = Math.floor(R() * 16), a = ray / 16 * Math.PI * 2 + 0.1, tilt = (ray % 2 ? 0.5 : -0.5)
    const r = 0.27 + R() * (ray % 2 ? 0.2 : 0.3)
    add(Math.cos(a) * r, Math.sin(a) * r, tilt * (r - 0.25) + (R() - 0.5) * 0.02)
  }
  for (let k = 0; k < 500; k++) {                             // a ring around it
    const a = R() * Math.PI * 2
    add(Math.cos(a) * 0.25, Math.sin(a) * 0.25, (R() - 0.5) * 0.03)
  }
  return to
}

function djed(): Pts {
  const { to, add, cylinder } = pts()
  cylinder(-0.45, 0.12, 0.075, 1100)                          // the pillar
  for (let i = 0; i < 4; i++) cylinder(0.14 + i * 0.085, 0.18 + i * 0.085, 0.26 - i * 0.012, 420, 0.1)   // its four bars
  cylinder(-0.5, -0.45, 0.16, 300, 0.1)                       // the base
  for (let k = 0; k < 300; k++) add((R() - 0.5) * 0.06, 0.5 + R() * 0.04, (R() - 0.5) * 0.06)  // the top
  return to
}

function scarab(): Pts {
  const { to, add, curve, sphere } = pts()
  sphere(0, -0.06, 0.15, 1100, 0.6)                           // the body, a flattened sphere
  sphere(0, 0.13, 0.075, 400, 0.6)                            // the head
  curve((t) => [0, 0.2 + 0.16 * t], 120, 0.01)                // the sun it pushes
  for (let k = 0; k < 350; k++) { const a = R() * 6.283; add(Math.cos(a) * 0.07, 0.42 + Math.sin(a) * 0.07, (R() - 0.5) * 0.04) }
  for (const s of [-1, 1]) {
    for (let f = 0; f < 6; f++) {                             // the wings, feather by feather
      curve((t) => [s * (0.14 + 0.42 * t), -0.02 + f * 0.035 + 0.12 * Math.sin(Math.PI * t * 0.8) - f * 0.03 * t], 150, 0.01, 0.06)
    }
    for (let l = 0; l < 3; l++) curve((t) => [s * (0.1 + 0.12 * t), -0.12 - l * 0.06 - 0.06 * t], 60, 0.01, 0.04)  // its legs
  }
  return to
}

function feather(): Pts {
  const { to, add, curve } = pts()
  const shaft = (t: number): [number, number] => [0.06 * Math.sin(t * Math.PI * 0.9), -0.48 + 0.96 * t]
  curve(shaft, 500, 0.01, 0.02)
  for (let k = 0; k < 2600; k++) {                            // the barbs, curling into depth
    const t = R(), [sx, sy] = shaft(t), side = R() < 0.5 ? -1 : 1
    const len = 0.17 * Math.sin(Math.PI * Math.min(1, t * 1.15)) + 0.02, u = R()
    add(sx + side * len * u, sy + len * u * 0.35, Math.sin(u * 2.2) * 0.07 * side)
  }
  // the curl at the tip
  curve((t) => { const a = t * Math.PI * 1.2; return [0.05 + 0.06 * Math.cos(a), 0.46 + 0.06 * Math.sin(a)] }, 160, 0.01)
  return to
}

const SIGNS: Record<string, () => Pts> = { pyramid: eye, obelisk: sun, steps: djed, pylon: scarab, ankh: feather }

export function signFor(shape: string): Pts {
  return (SIGNS[shape] ?? eye)()
}
