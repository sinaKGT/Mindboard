/** The Experts page's 3D desert (DESIGN.md 40): each expert a monument of
 *  glowing sand, fine sand drifting in the air, the camera gliding between
 *  them and flying into one as it comes apart.
 *
 *  Plain three.js, bundled - nothing is fetched, so it works offline. The
 *  page drives it: which experts, which one is in front, inside or not,
 *  thinking, speaking, which the guide calls. It reports hovers and clicks.
 */
import * as THREE from 'three'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'

import { sample } from './shapes'
import { signFor } from './signs'

export interface SceneExpert { id: string; name: string; colour: string; shape: string; cards: number }

const VERT = /* glsl */`
  uniform float uTime, uThink, uSpeak, uAmp, uSize, uDissolve, uCall, uTop, uAspect;
  uniform vec2 uPointer;
  attribute vec3 aHome; attribute float aSeed; attribute float aEdge; attribute float aCap;
  varying float vAlpha; varying float vGlow;
  vec3 hash3(float n) { return fract(sin(vec3(n, n + 1.7, n + 3.1)) * 43758.5453) - 0.5; }
  // the pointer is a small black hole: grains near it spiral in, glow at the
  // rim and vanish at the centre; where it has passed, they fade back in
  float holeEat = 1.0, holeRim = 0.0;
  vec2 hole(vec4 clip) {
    vec2 ndc = clip.xy / clip.w, dv = (uPointer - ndc) * vec2(uAspect, 1.0);
    float d = length(dv), R = 0.2;
    float pull = smoothstep(R, 0.0, d);
    vec2 swirl = vec2(-dv.y, dv.x);
    vec2 off = (dv * pull * 0.88 + swirl * pull * 0.45) / vec2(uAspect, 1.0);
    float left = d * (1.0 - 0.88 * pull);                  // how far from the centre it ends up
    holeEat = smoothstep(0.0, 0.035, left);
    holeRim = pull * (1.0 - pull) * 4.0;
    return off * clip.w;
  }

  // a slowly changing current: sums of sines over the grain's own position,
  // so neighbours move together and the sand gathers into veils and ribbons
  vec3 current(vec3 q, float t) {
    return vec3(sin(q.y * 1.3 + t * 0.11) + sin(q.z * 0.7 - t * 0.07) + 0.5 * sin(q.x * 2.1 + q.y * 1.7 + t * 0.05),
                sin(q.z * 1.1 + t * 0.09) + sin(q.x * 0.9 + t * 0.06) + 0.5 * sin(q.y * 2.3 - q.z * 1.4 - t * 0.04),
                sin(q.x * 1.2 - t * 0.10) + sin(q.y * 0.8 + t * 0.05) + 0.5 * sin(q.z * 1.9 + q.x * 1.3 + t * 0.06));
  }
  void main() {
    vec3 p = aHome;
    // every grain wanders on its own slow path
    vec3 f = fract(vec3(aSeed * 3.7, aSeed * 5.3, aSeed * 7.1));
    vec3 wander = vec3(sin(uTime * (0.07 + 0.12 * f.x) + aSeed * 30.0), sin(uTime * (0.05 + 0.1 * f.y) + aSeed * 17.0),
                       cos(uTime * (0.06 + 0.11 * f.z) + aSeed * 23.0));
    p.y += aCap * (0.16 + 0.03 * sin(uTime * 1.6));          // the crown floats, breathing
    float h = clamp(aHome.y / uTop, 0.0, 1.0);
    float life = fract(uTime * (0.022 + 0.035 * fract(aSeed * 7.3)) + aSeed);     // slow
    // gusts come and go; the wind wanders all around
    float gust = 0.5 + 0.5 * sin(uTime * 0.21) * sin(uTime * 0.083 + 1.7);
    float blown = step(0.88 - 0.38 * gust, fract(aSeed * 13.7)) * smoothstep(0.0, 1.0, life) * (1.0 - aCap);
    float born = uTime - life / (0.022 + 0.035 * fract(aSeed * 7.3));
    float wa = born * 0.11 + 2.2 * sin(born * 0.047 + 0.6) + (fract(aSeed * 5.7) - 0.5) * 1.6;
    vec3 wind = vec3(cos(wa), 0.25 + 0.6 * h + 0.3 * fract(aSeed * 2.3), sin(wa))
              * blown * (0.8 + 2.6 * fract(aSeed * 3.1)) * (0.6 + 0.8 * gust);
    p += wind + hash3(aSeed * 91.0) * blown * 0.9 + wander * (0.012 + blown * 0.35);
    // thinking: a sandstorm around it
    float ang = atan(p.z, p.x) + uThink * (uTime * (1.4 + fract(aSeed * 5.0)));
    float rad = length(p.xz) * (1.0 + uThink * (0.6 + 0.9 * fract(aSeed * 17.0)));
    vec3 storm = vec3(cos(ang) * rad, p.y + uThink * (sin(uTime * 2.0 + aSeed * 40.0) * 0.5 + 0.4), sin(ang) * rad);
    p = mix(p, storm, uThink);
    // speaking: waves climb it with the voice
    float wave = sin(h * 18.0 - uTime * 9.0) * 0.5 + 0.5;
    p += normalize(vec3(aHome.x, 0.35, aHome.z) + 1e-4) * uSpeak * uAmp * wave * 0.22 * (1.0 - blown);
    // going in: it comes apart into a cloud of sand
    vec3 dir = normalize(hash3(aSeed * 57.0) + vec3(0.0, 0.2, 0.15));
    // inside, the grains float loose - each on its own drift, the cloud never turning as one
    vec3 cloud = dir * uDissolve * (1.2 + 5.5 * fract(aSeed * 5.0));
    // inside, the sand follows the current - veils that fold, thin out and reform, never snow
    vec3 c0 = p + cloud;
    // inside: dust in a sunbeam - the grains hang almost still, a faint drift,
    // and now and then a slow gust sweeps across and lets them settle again
    vec3 drift = current(c0 * 0.4 + aSeed * 3.0, uTime * 0.25) * 0.05;
    float gx = mod(uTime * 0.5, 30.0) - 15.0;                  // a gust front, once a minute
    float sweep = exp(-pow((c0.x - gx) * 0.55, 2.0)) * (0.5 + fract(aSeed * 9.0));
    p = c0 + (drift + vec3(0.32, 0.1, -0.08) * sweep) * uDissolve;
    // the beam: a soft diagonal band where the dust catches the light
    float beam = exp(-pow(dot(c0.xy - vec2(0.0, 1.2), normalize(vec2(1.0, 0.55))), 2.0) * 0.9);
    p *= 1.0 + uDissolve * uSpeak * uAmp * 0.06 * sin(uTime * 8.0 + length(p) * 3.0);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    // the black hole leaves the monuments alone - it eats only loose sand, inside
    gl_Position.xy += hole(gl_Position) * uDissolve;
    holeEat = mix(1.0, holeEat, uDissolve); holeRim *= uDissolve;
    vAlpha = (1.0 - blown * smoothstep(0.4, 1.0, life)) * (0.55 + 0.45 * fract(aSeed * 29.0)) * (1.0 - 0.5 * uDissolve);
    // inside, each grain brightens and dims at its own pace, more in the beam
    float twinkle = 0.5 + 0.5 * pow(0.5 + 0.5 * sin(uTime * (0.25 + 0.8 * fract(aSeed * 11.0)) + aSeed * 50.0), 2.0);
    vAlpha *= mix(1.0, twinkle * (1.25 + 1.6 * beam), uDissolve) * holeEat;
    float blink = 0.55 + 0.45 * sin(uTime * 2.2 + aSeed * 0.5);
    vGlow = aEdge * 0.8 + h * 0.3 + uSpeak * uAmp * wave * 0.6 + uThink * 0.3 + aCap * (0.75 * blink + uCall * 0.35) + uDissolve * beam * 0.6 + holeRim;
    gl_PointSize = uSize * (0.6 + fract(aSeed * 41.0)) * (1.0 + aEdge * 0.7 + aCap * (0.35 + 0.15 * uCall)) * (1.0 + 0.5 * uDissolve) / -mv.z;
  }`
const FRAG = /* glsl */`
  uniform vec3 uColor; varying float vAlpha; varying float vGlow;
  void main() {
    float d = length(gl_PointCoord - 0.5); if (d > 0.5) discard;
    vec3 col = mix(uColor * 0.55, uColor * 1.6 + 0.15, clamp(vGlow, 0.0, 1.0));
    gl_FragColor = vec4(col, smoothstep(0.5, 0.0, d) * vAlpha);
  }`
const dot = (a: string) => `uniform vec3 uColor; varying float vA;
  void main() { float d = length(gl_PointCoord - 0.5); if (d > 0.5) discard;
    gl_FragColor = vec4(${a}, smoothstep(0.5, 0.0, d) * vA); }`

function monument(shape: string, colour: string) {
  const s = sample(shape)
  const n = s.edge.length, seed = new Float32Array(n)
  for (let i = 0; i < n; i++) seed[i] = Math.random()
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.BufferAttribute(s.home.slice(), 3))
  g.setAttribute('aHome', new THREE.BufferAttribute(s.home, 3))
  g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1))
  g.setAttribute('aEdge', new THREE.BufferAttribute(s.edge, 1))
  g.setAttribute('aCap', new THREE.BufferAttribute(s.crown, 1))
  const m = new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, transparent: true,
    depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uThink: { value: 0 }, uSpeak: { value: 0 }, uAmp: { value: 0 },
      uDissolve: { value: 0 }, uCall: { value: 0 }, uSize: { value: 14 }, uTop: { value: s.top },
      uPointer: { value: new THREE.Vector2(9, 9) }, uAspect: { value: 1 },
      uColor: { value: new THREE.Color(colour) } } })
  const pts = new THREE.Points(g, m)
  pts.frustumCulled = false
  return { pts, top: s.top, middle: s.middle }
}

/** grains of light rising from the crown's tip into the sky */
function rising(colour: string, from: number) {
  const n = 4500, seed = new Float32Array(n)
  for (let i = 0; i < n; i++) seed[i] = Math.random()
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3))
  g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1))
  const m = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uOn: { value: 0 }, uFrom: { value: from }, uColor: { value: new THREE.Color(colour) } },
    vertexShader: `uniform float uTime, uOn, uFrom; attribute float aSeed; varying float vA;
      void main() { float H = 9.0;
        float y = mod(aSeed * H + uTime * (0.22 + aSeed * 0.3), H);       // slowly upward
        float a = aSeed * 40.0 + uTime * (0.3 + 0.5 * fract(aSeed * 13.0)) + y * 0.6;
        float r = 0.34 * sqrt(fract(aSeed * 9.1)) * pow(y / H, 0.75);
        vec4 mv = modelViewMatrix * vec4(cos(a) * r, uFrom + 0.17 + y, sin(a) * r, 1.0); gl_Position = projectionMatrix * mv;
        vA = uOn * pow(1.0 - y / H, 1.3) * (0.45 + 0.55 * fract(aSeed * 31.0)) * smoothstep(0.0, 0.25, y + 0.05);
        gl_PointSize = (12.0 + 14.0 * fract(aSeed * 7.0)) / -mv.z; }`,
    fragmentShader: dot('uColor * 1.5 + 0.2') })
  const pts = new THREE.Points(g, m)
  pts.frustumCulled = false
  return pts
}

/** the sign inside a monument (signs.ts), gathering from scattered sand */
function sign_(shape: string, colour: string) {
  const to = signFor(shape)
  const n = to.length / 3, from = new Float32Array(n * 3), seed = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    // each grain appears where it belongs, rising a touch into place - the sign draws itself in its own shape
    from.set([to[i * 3] + (Math.random() - 0.5) * 0.03, to[i * 3 + 1] - 0.05 - Math.random() * 0.06, to[i * 3 + 2] + (Math.random() - 0.5) * 0.03], i * 3)
    seed[i] = Math.random()
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(to), 3))
  g.setAttribute('aFrom', new THREE.BufferAttribute(from, 3))
  g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1))
  const m = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uShow: { value: 0 }, uTime: { value: 0 }, uColor: { value: new THREE.Color(colour) } },
    vertexShader: `uniform float uShow, uTime; attribute vec3 aFrom; attribute float aSeed; varying float vA;
      void main() { float k = smoothstep(aSeed * 0.7, aSeed * 0.7 + 0.3, uShow);     // grain by grain
        vec3 p = mix(aFrom, position, k) + vec3(sin(uTime * 2.0 + aSeed * 50.0), cos(uTime * 1.7 + aSeed * 40.0), 0.0) * 0.006;
        vec4 mv = modelViewMatrix * vec4(p, 1.0); gl_Position = projectionMatrix * mv;
        vA = k * (0.5 + 0.5 * fract(aSeed * 17.0)) * (0.75 + 0.25 * sin(uTime * 3.0 + aSeed * 20.0));
        gl_PointSize = (14.0 + 10.0 * fract(aSeed * 7.0)) / -mv.z; }`,
    fragmentShader: dot('mix(uColor, vec3(1.0, 0.95, 0.85), 0.5) * 2.0') })
  const pts = new THREE.Points(g, m)
  pts.frustumCulled = false
  return pts
}

function airSand() {
  const n = 9000, pos = new Float32Array(n * 3), seed = new Float32Array(n)
  for (let i = 0; i < n; i++) { pos.set([(Math.random() - 0.5) * 60, Math.random() * 7 - 0.5, (Math.random() - 0.5) * 40 - 6], i * 3); seed[i] = Math.random() }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1))
  const m = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uColor: { value: new THREE.Color('#f2c070') },
      uPointer: { value: new THREE.Vector2(9, 9) }, uAspect: { value: 1 }, uDim: { value: 1 } },
    vertexShader: `uniform float uTime, uAspect, uDim; uniform vec2 uPointer; attribute float aSeed; varying float vA;
  // the pointer is a small black hole: grains near it spiral in, glow at the
        // rim and vanish at the centre; where it has passed, they fade back in
        float holeEat = 1.0, holeRim = 0.0;
        vec2 hole(vec4 clip) {
          vec2 ndc = clip.xy / clip.w, dv = (uPointer - ndc) * vec2(uAspect, 1.0);
          float d = length(dv), R = 0.2;
          float pull = smoothstep(R, 0.0, d);
          vec2 swirl = vec2(-dv.y, dv.x);
          vec2 off = (dv * pull * 0.88 + swirl * pull * 0.45) / vec2(uAspect, 1.0);
          float left = d * (1.0 - 0.88 * pull);                  // how far from the centre it ends up
          holeEat = smoothstep(0.0, 0.035, left);
          holeRim = pull * (1.0 - pull) * 4.0;
          return off * clip.w;
        }
      
      void main() { vec3 p = position;
        p.x = mod(p.x + uTime * (0.12 + aSeed * 0.3) + 30.0, 60.0) - 30.0;
        p.y += sin(uTime * (0.15 + 0.2 * fract(aSeed * 3.3)) + aSeed * 30.0) * 0.35;
        p.z += cos(uTime * (0.1 + 0.18 * fract(aSeed * 7.7)) + aSeed * 20.0) * 0.45;
        p.x += sin(uTime * (0.09 + 0.15 * fract(aSeed * 5.1)) + aSeed * 11.0) * 0.4;
        vec4 mv = modelViewMatrix * vec4(p, 1.0); gl_Position = projectionMatrix * mv;
        gl_Position.xy += hole(gl_Position);
        vA = (0.25 + 0.5 * aSeed) * 0.6 * uDim * holeEat * (1.0 + holeRim); gl_PointSize = (6.0 + aSeed * 10.0) / -mv.z; }`,
    fragmentShader: dot('uColor') })
  const pts = new THREE.Points(g, m)
  pts.frustumCulled = false
  return pts
}

function dunes() {
  const n = 30000, pos = new Float32Array(n * 3)
  for (let i = 0; i < n; i++) {
    const x = (Math.random() - 0.5) * 70, z = (Math.random() - 0.5) * 50 - 6
    pos.set([x, Math.sin(x * 0.35 + z * 0.15) * 0.18 + Math.sin(z * 0.6) * 0.08 - 0.02, z], i * 3)
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  return new THREE.Points(g, new THREE.PointsMaterial({ size: 0.03, color: 0x8a6a3c, transparent: true, opacity: 0.55,
    blending: THREE.AdditiveBlending, depthWrite: false }))
}

interface Piece { pts: THREE.Points; top: number; middle: number; rise: THREE.Points; sign: THREE.Points;
  call: number; show: number; label: HTMLDivElement }

export class ExpertScene {
  private renderer: THREE.WebGLRenderer
  private composer: EffectComposer
  private scene = new THREE.Scene()
  private camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200)
  private clock = new THREE.Clock()
  private pieces: Piece[] = []
  private experts: SceneExpert[] = []
  private ambient: THREE.Points[] = []
  private look = new THREE.Vector3()
  private camPos = new THREE.Vector3(0, 1.6, 6.5)
  private tmp = new THREE.Vector3()
  private mouse = { x: -1e4, y: -1e4 }
  private frameId = 0
  private lastT = 0
  current = 0
  inside = false
  thinking = false
  speaking = false
  guiding = false
  amp = 0
  calls = new Set<string>()
  private dissolve = 0
  private think = 0
  private speak = 0
  hovered = -1
  hoveredBody = -1

  constructor(private host: HTMLElement, private onPick: (i: number) => void,
              private onEdit: (i: number) => void = () => {}) {
    this.renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' })
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
    host.prepend(this.renderer.domElement)
    this.scene.fog = new THREE.FogExp2(0x030304, 0.045)
    this.composer = new EffectComposer(this.renderer)
    this.composer.addPass(new RenderPass(this.scene, this.camera))
    this.composer.addPass(new UnrealBloomPass(new THREE.Vector2(1, 1), 0.9, 0.6, 0.15))
    this.ambient = [airSand(), dunes()]
    this.scene.add(...this.ambient)
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
  }
  private onMove = (e: PointerEvent) => { this.mouse = { x: e.clientX, y: e.clientY } }
  private onClick = () => {
    const i = this.hovered >= 0 ? this.hovered : this.hoveredBody
    if (i >= 0 && !this.inside) this.onPick(i)
  }

  setExperts(list: SceneExpert[]) {
    for (const p of this.pieces) {
      this.scene.remove(p.pts, p.sign); p.pts.geometry.dispose(); p.sign.geometry.dispose(); p.label.remove()
    }
    this.experts = list
    const most = Math.max(1, ...list.map((e) => e.cards))
    this.pieces = list.map((e) => {
      const { pts, top, middle } = monument(e.shape, e.colour)
      const rise = rising(e.colour, top)
      pts.add(rise)
      const sign = sign_(e.shape, e.colour)
      this.scene.add(pts, sign)
      const label = document.createElement('div')
      label.className = 'xp-name'
      label.textContent = e.name                     // its name alone; its size says how much it knows
      label.onclick = () => this.onPick(list.indexOf(e))
      // the one in front can be edited, from a small pen beside its name
      const pen = document.createElement('button')
      pen.className = 'xp-pen'; pen.title = `Edit ${e.name}`; pen.textContent = '✎'
      pen.onclick = (ev) => { ev.stopPropagation(); this.onEdit(list.indexOf(e)) }
      label.appendChild(pen)
      this.host.appendChild(label)
      // a monument's size is how much it knows: its cards, on a log scale
      const size = 0.55 + 0.6 * Math.log10(1 + e.cards) / Math.log10(1 + most)
      pts.userData.size = size
      return { pts, top, middle, rise, sign, call: 0, show: 0, label }
    })
    this.current = Math.min(this.current, Math.max(list.length - 1, 0))
  }

  private place(i: number) {
    const n = this.pieces.length, k = ((i - this.current) % n + n) % n
    const side = k === 0 ? 0 : (k <= n / 2 ? k : k - n)
    // the others stand back in depth along a gentle arc, so sizes can be compared
    return this.tmp.set(side * 3.9, 0, -Math.abs(side) * 2.9 - (Math.abs(side) > 1 ? 1.5 : 0))
  }

  private hoverCheck() {
    this.hovered = -1; this.hoveredBody = -1
    const box = this.renderer.domElement.getBoundingClientRect()
    if (!this.inside) {
      let best = 1e9
      this.pieces.forEach((p, i) => {
        const s = p.pts.scale.y
        const top = this.tmp.copy(p.pts.position).setY(s * (p.top + 0.1)).project(this.camera)
        const x = box.left + (top.x + 1) / 2 * box.width, y = box.top + (1 - top.y) / 2 * box.height
        const d = Math.hypot(x - this.mouse.x, y - this.mouse.y), reach = 34 + 30 * s
        if (top.z < 1 && d < reach && d < best) { best = d; this.hovered = i }
      })
      if (this.hovered < 0) this.pieces.forEach((p, i) => {
        const s = p.pts.scale.y
        const mid = this.tmp.copy(p.pts.position).setY(s * p.middle).project(this.camera)
        const x = box.left + (mid.x + 1) / 2 * box.width, y = box.top + (1 - mid.y) / 2 * box.height
        const reach = 60 + 90 * s * (i === this.current ? 1.6 : 0.8)
        if (mid.z < 1 && Math.hypot(x - this.mouse.x, (y - this.mouse.y) * 1.4) < reach) this.hoveredBody = i
      })
    }
    this.renderer.domElement.style.cursor = this.hovered >= 0 || this.hoveredBody >= 0 ? 'pointer' : ''
  }

  private frame = () => {
    this.frameId = requestAnimationFrame(this.frame)
    const t = this.clock.getElapsedTime(), dt = Math.min(t - this.lastT, 0.1)
    this.lastT = t
    this.hoverCheck()
    this.dissolve += ((this.inside ? 1 : 0) - this.dissolve) * 0.035
    this.think += ((this.thinking ? 1 : 0) - this.think) * 0.04
    this.speak += ((this.speaking ? 1 : 0) - this.speak) * 0.08
    if (!this.speaking) this.amp *= 0.9
    const box0 = this.renderer.domElement.getBoundingClientRect()
    const px = ((this.mouse.x - box0.left) / box0.width) * 2 - 1, py = -(((this.mouse.y - box0.top) / box0.height) * 2 - 1)
    const feed = (m: THREE.Material) => {
      const u = (m as THREE.ShaderMaterial).uniforms
      if (u?.uPointer) { u.uPointer.value.set(px, py); u.uAspect.value = this.camera.aspect }
    }
    for (const a of this.ambient) {
      const u = (a.material as THREE.ShaderMaterial).uniforms
      if (u?.uTime) u.uTime.value = t
      if (u?.uDim) u.uDim.value = 1 - this.dissolve * 0.88     // inside, the air sand all but fades
      feed(a.material as THREE.Material)
    }
    for (const p of this.pieces) feed(p.pts.material as THREE.Material)
    const box = this.renderer.domElement.getBoundingClientRect()
    this.pieces.forEach((p, i) => {
      const e = this.experts[i], me = i === this.current
      p.pts.position.lerp(this.place(i), 0.05)
      // one scale for all: a monument's size is only how much it knows
      const s = 1.05 * (p.pts.userData.size as number) * (me ? 1.06 : 1)
      p.pts.scale.lerp(this.tmp.set(s, s, s), 0.05)
      // only the one in focus turns; the others stand, a corner towards you
      const spin = (p.pts.userData.spin as number | undefined) ?? Math.PI / 4
      const settle = Math.PI / 4 + Math.round((spin - Math.PI / 4) / (Math.PI / 2)) * (Math.PI / 2)
      p.pts.userData.spin = me && !this.inside ? spin + dt * 0.15 : spin + (settle - spin) * 0.03
      p.pts.rotation.y = p.pts.userData.spin as number
      p.pts.visible = me || this.dissolve < 0.95
      const u = (p.pts.material as THREE.ShaderMaterial).uniforms
      const called = this.calls.has(e.id) ? 1 : 0
      p.call += ((this.guiding ? 0.35 + 0.35 * Math.sin(t * 9 + i) : Math.max(called, this.hovered === i ? 1 : 0)) - p.call) * 0.12
      u.uTime.value = t
      u.uThink.value = me ? this.think * (1 - this.dissolve * 0.6) : 0
      u.uSpeak.value = me ? this.speak : 0
      u.uAmp.value = this.amp
      u.uDissolve.value = me ? this.dissolve : 0
      u.uCall.value = p.call
      const ru = (p.rise.material as THREE.ShaderMaterial).uniforms
      ru.uTime.value = t; ru.uOn.value = p.call * (1 - this.dissolve)
      // the sign of knowledge, gathered inside under the pointer
      // slowly: about four seconds to form, and a little quicker to fade
      const want = this.hoveredBody === i && !this.inside ? 1 : 0
      p.show += Math.sign(want - p.show) * Math.min(Math.abs(want - p.show), dt * (want ? 0.25 : 0.5))
      const su = (p.sign.material as THREE.ShaderMaterial).uniforms
      su.uShow.value = p.show * (1 - this.dissolve); su.uTime.value = t
      p.sign.visible = su.uShow.value > 0.002
      p.sign.position.copy(p.pts.position).setY(p.pts.scale.y * p.middle)
      p.sign.quaternion.copy(this.camera.quaternion)
      p.sign.rotateY(Math.sin(t * 0.6 + i) * 0.75); p.sign.rotateX(Math.sin(t * 0.43 + i) * 0.18)
      p.sign.scale.setScalar(p.pts.scale.y * 0.85)
      // its name, under it
      const at = this.tmp.copy(p.pts.position).setY(-0.6).project(this.camera)
      p.label.style.left = `${(at.x + 1) / 2 * box.width}px`
      p.label.style.top = `${(1 - at.y) / 2 * box.height}px`
      p.label.style.opacity = at.z < 1 && !this.inside ? '1' : '0'
      p.label.classList.toggle('on', me)
      ;(p.label.lastChild as HTMLElement).style.display = me ? '' : 'none'
    })
    // the camera: a view of the desert, or gliding into the sand
    const front = this.pieces[this.current]?.pts.position ?? this.tmp.set(0, 0, 0)
    const fx = front.x, fz = front.z, d = this.dissolve
    // a little above the desert, looking down across it - so depth, and size, read
    this.look.lerp(new THREE.Vector3(fx, 0.55 + d * 0.6, fz - 1.2 - d * 2), 0.05)
    // a narrow window steps back, so a tall monument still fits
    const back = Math.max(1, 1.25 / this.camera.aspect) ** 0.8
    this.camPos.lerp(new THREE.Vector3(fx + Math.sin(t * 0.1) * 0.5 * (1 - d), (2.6 + (back - 1) * 1.2) - d * 1.5,
                                       fz + 7.4 * back - d * 6.4 * back), 0.035)
    this.camera.position.copy(this.camPos)
    this.camera.lookAt(this.look)
    this.composer.render()
  }

  dispose() {
    cancelAnimationFrame(this.frameId)
    removeEventListener('resize', this.resize)
    removeEventListener('pointermove', this.onMove)
    for (const p of this.pieces) p.label.remove()
    this.renderer.dispose()
    this.renderer.domElement.remove()
  }
}
