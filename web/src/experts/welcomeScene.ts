/** The welcome page's 3D scene (DESIGN.md 43): two signs in glowing sand
 *  under the same night sky as the Experts - the Eye of Horus, knowledge,
 *  for your experts; a papyrus scroll and reed pen, writing, for your boards.
 *  They gather slowly out of the dust; one brightens under the pointer and
 *  sends grains up; clicked, it comes apart as the camera glides into it,
 *  and its page opens. The pointer eats the drifting dust as on the Experts
 *  page; the signs are never touched.
 */
import * as THREE from 'three'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'

import { airSand, dot, dunes, eatSand, rising } from './scene'
import { signFor } from './signs'

export type Door = 'boards' | 'experts'
interface SignSpec { door: Door; shape: string; colour: string; at: THREE.Vector3; label: string; note: string }

const SIGNS: SignSpec[] = [
  { door: 'experts', shape: 'eye', colour: '#f0c070', at: new THREE.Vector3(-1.7, 1.15, 0),
    label: 'Your experts', note: 'knowledge' },
  { door: 'boards', shape: 'scroll', colour: '#e9c9a0', at: new THREE.Vector3(1.7, 1.15, 0),
    label: 'Your boards', note: 'writing' },
]

/** a sign of sand: gathering slowly from the dust, glowing under the pointer,
 *  coming apart when chosen */
function sandSign(shape: string, colour: string) {
  const to = signFor(shape)
  const n = to.length / 3, from = new Float32Array(n * 3), seed = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    // each grain starts as dust somewhere around, then drifts to its place
    const a = Math.random() * Math.PI * 2, r = 1.5 + Math.random() * 3.5
    from.set([Math.cos(a) * r, (Math.random() - 0.5) * 3, Math.sin(a) * r * 0.6], i * 3)
    seed[i] = Math.random()
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(to), 3))
  g.setAttribute('aFrom', new THREE.BufferAttribute(from, 3))
  g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1))
  const m = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uGather: { value: 0 }, uHover: { value: 0 }, uGone: { value: 0 },
                uColor: { value: new THREE.Color(colour) } },
    vertexShader: `uniform float uTime, uGather, uHover, uGone; attribute vec3 aFrom; attribute float aSeed; varying float vA;
      void main() {
        float k = smoothstep(aSeed * 0.6, aSeed * 0.6 + 0.4, uGather);        // grain by grain, slowly
        vec3 p = mix(aFrom, position, k);
        vec3 f = fract(vec3(aSeed * 3.7, aSeed * 5.3, aSeed * 7.1));
        p += vec3(sin(uTime * (0.08 + 0.1 * f.x) + aSeed * 30.0), sin(uTime * (0.06 + 0.1 * f.y) + aSeed * 17.0),
                  cos(uTime * (0.07 + 0.1 * f.z) + aSeed * 23.0)) * 0.006;
        // chosen: it comes apart into dust
        vec3 dir = normalize(fract(sin(vec3(aSeed * 57.0, aSeed * 91.0, aSeed * 13.0)) * 43758.5453) - 0.5 + vec3(0.0, 0.1, 0.4));
        p += dir * uGone * (0.8 + 3.5 * f.x);
        vec4 mv = modelViewMatrix * vec4(p, 1.0); gl_Position = projectionMatrix * mv;
        float twinkle = 0.7 + 0.3 * sin(uTime * (0.4 + 0.9 * f.y) + aSeed * 40.0);
        vA = k * twinkle * (0.55 + 0.55 * uHover) * (1.0 - 0.75 * uGone);
        gl_PointSize = (13.0 + 10.0 * f.z) * (1.0 + 0.35 * uHover) / -mv.z; }`,
    fragmentShader: dot('mix(uColor, vec3(1.0, 0.95, 0.85), 0.35 + 0.25 * vA) * 1.8') })
  const pts = new THREE.Points(g, m)
  pts.frustumCulled = false
  return pts
}

interface Live { spec: SignSpec; pts: THREE.Points; rise: THREE.Points; hover: number; label: HTMLDivElement }

export class WelcomeScene {
  private renderer: THREE.WebGLRenderer
  private composer: EffectComposer
  private scene = new THREE.Scene()
  private camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200)
  private clock = new THREE.Clock()
  private air = airSand()
  private ambient: THREE.Points[] = []
  private signs: Live[] = []
  private mouse = { x: -1e4, y: -1e4 }
  private tmp = new THREE.Vector3()
  private camPos = new THREE.Vector3(0, 1.4, 7.2)
  private look = new THREE.Vector3(0, 1.1, 0)
  private frameId = 0
  private hovered = -1
  private chosen = -1
  private chosenAt = 0
  private gather = 0

  constructor(private host: HTMLElement, private onOpen: (door: Door) => void) {
    this.renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' })
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
    host.prepend(this.renderer.domElement)
    this.scene.fog = new THREE.FogExp2(0x030304, 0.045)
    this.composer = new EffectComposer(this.renderer)
    this.composer.addPass(new RenderPass(this.scene, this.camera))
    this.composer.addPass(new UnrealBloomPass(new THREE.Vector2(1, 1), 0.8, 0.6, 0.15))
    this.ambient = [this.air, dunes()]
    this.scene.add(...this.ambient)
    for (const spec of SIGNS) {
      const pts = sandSign(spec.shape, spec.colour)
      pts.position.copy(spec.at)
      pts.scale.setScalar(1.55)
      const rise = rising(spec.colour, 0.42)
      rise.scale.setScalar(0.38)
      pts.add(rise)
      this.scene.add(pts)
      const label = document.createElement('div')
      label.className = 'wl-door'
      label.innerHTML = `<div class="wl-door-name">${spec.label}</div><div class="wl-door-note">${spec.note}</div>`
      label.onclick = () => this.choose(SIGNS.indexOf(spec))
      host.appendChild(label)
      this.signs.push({ spec, pts, rise, hover: 0, label })
    }
    this.resize()
    addEventListener('resize', this.resize)
    addEventListener('pointermove', this.onMove)
    this.renderer.domElement.addEventListener('click', this.onClick)
    this.frame()
  }

  private resize = () => {
    const w = this.host.clientWidth || innerWidth, h = this.host.clientHeight || innerHeight
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(w, h)
    this.composer.setSize(w, h)
    // side by side on a wide window; one above the other on a narrow one
    const wide = this.camera.aspect >= 1.1
    this.signs.forEach((s, i) => {
      s.pts.position.set(wide ? (i ? 1 : -1) * Math.min(1.7, 0.95 * this.camera.aspect) : 0,
                         wide ? 1.15 : (i ? -0.15 : 2.35), 0)
    })
    this.back = wide ? 1 : Math.max(1, 1.15 / this.camera.aspect) ** 0.75
  }
  private back = 1
  private onMove = (e: PointerEvent) => { this.mouse = { x: e.clientX, y: e.clientY } }
  // a tap may come with no pointer move before it: find the sign from the click itself
  private onClick = (e: MouseEvent) => {
    this.mouse = { x: e.clientX, y: e.clientY }
    const i = this.signAt(e.clientX, e.clientY)
    if (i >= 0) this.choose(i)
  }

  /** the sign under a point on screen, or -1 */
  private signAt(px: number, py: number) {
    const box = this.renderer.domElement.getBoundingClientRect()
    let found = -1
    this.signs.forEach((s, i) => {
      const c = this.tmp.copy(s.pts.position).project(this.camera)
      const x = box.left + (c.x + 1) / 2 * box.width, y = box.top + (1 - c.y) / 2 * box.height
      if (Math.hypot(x - px, y - py) < Math.min(box.width, box.height) * 0.2) found = i
    })
    return found
  }

  private choose(i: number) {
    if (this.chosen >= 0) return
    this.chosen = i
    this.chosenAt = this.clock.getElapsedTime()
    setTimeout(() => this.onOpen(this.signs[i].spec.door), 1700)
  }

  private frame = () => {
    this.frameId = requestAnimationFrame(this.frame)
    const t = this.clock.getElapsedTime()
    const box = this.renderer.domElement.getBoundingClientRect()
    // the signs gather slowly out of the dust: six seconds, whatever the frame rate
    this.gather = Math.min(1, t / 6)
    // which sign the pointer rests on
    this.hovered = this.chosen < 0 ? this.signAt(this.mouse.x, this.mouse.y) : -1
    this.renderer.domElement.style.cursor = this.hovered >= 0 ? 'pointer' : ''
    // the dust drifts, and the pointer eats it
    for (const a of this.ambient) {
      const u = (a.material as THREE.ShaderMaterial).uniforms
      if (u?.uTime) u.uTime.value = t
    }
    const px = ((this.mouse.x - box.left) / box.width) * 2 - 1, py = -(((this.mouse.y - box.top) / box.height) * 2 - 1)
    eatSand(this.air, this.camera, t, px, py)
    const gone = this.chosen >= 0 ? Math.min(1, (t - this.chosenAt) / 1.5) : 0
    this.signs.forEach((s, i) => {
      s.hover += ((this.hovered === i || this.chosen === i ? 1 : 0) - s.hover) * 0.06
      const u = (s.pts.material as THREE.ShaderMaterial).uniforms
      u.uTime.value = t; u.uGather.value = this.gather; u.uHover.value = s.hover
      u.uGone.value = this.chosen === i ? gone : this.chosen >= 0 ? gone * 0.4 : 0
      const ru = (s.rise.material as THREE.ShaderMaterial).uniforms
      ru.uTime.value = t; ru.uOn.value = s.hover * (1 - gone)
      // turning slowly, a little, so their depth shows
      s.pts.rotation.y = Math.sin(t * 0.18 + i * 2) * 0.35
      const at = this.tmp.copy(s.pts.position).setY(s.pts.position.y - 1.05).project(this.camera)
      s.label.style.left = `${(at.x + 1) / 2 * box.width}px`
      s.label.style.top = `${(1 - at.y) / 2 * box.height}px`
      s.label.classList.toggle('on', this.hovered === i)
      s.label.style.opacity = String((0.25 + 0.75 * this.gather) * (1 - gone))
    })
    // chosen: the camera glides into its sign
    const target = this.chosen >= 0 ? this.signs[this.chosen].pts.position : null
    this.look.lerp(target ? this.tmp.set(target.x, target.y, target.z - 2) : this.tmp.set(0, 1.1, 0), 0.04)
    this.camPos.lerp(target ? new THREE.Vector3(target.x, target.y, target.z + 1.2)
                            : new THREE.Vector3(Math.sin(t * 0.07) * 0.4, 1.4, 7.2 * this.back), target ? 0.035 : 0.02)
    this.camera.position.copy(this.camPos)
    this.camera.lookAt(this.look)
    this.composer.render()
  }

  dispose() {
    cancelAnimationFrame(this.frameId)
    removeEventListener('resize', this.resize)
    removeEventListener('pointermove', this.onMove)
    for (const s of this.signs) s.label.remove()
    this.renderer.dispose()
    this.renderer.domElement.remove()
  }
}
