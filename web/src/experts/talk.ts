/** Talking to an expert hands-free (DESIGN.md 44).
 *
 *  Turn it on once and just speak. The microphone stays open; a light voice
 *  detector, run on each 20 ms of sound, notices when you start. While you
 *  speak, what you have said so far is written out every second or so
 *  (whisper.cpp). When you pause, Smart Turn - on this computer - listens to
 *  how your voice ended and says whether you have finished your thought or
 *  are only taking a breath; if finished, your words are asked. If you start
 *  speaking while the expert is answering, it stops at once and listens:
 *  you can interrupt it, as in a conversation.
 *
 *  The browser's echo cancellation keeps the expert's own voice out of the
 *  microphone, and while it speaks the detector asks for a clearer, longer
 *  sound before it treats it as you. Nothing is recorded outside a turn, and
 *  nothing is kept.
 */
import { api } from '../api'
import { wav } from './voice'

export type TalkState = 'off' | 'waiting' | 'hearing' | 'writing'

export interface TalkEvents {
  state(s: TalkState): void
  /** how loud you are right now, 0..1 */
  level(v: number): void
  /** the words so far, while you speak */
  partial(text: string): void
  /** a finished turn: ask it */
  turn(text: string): void
  /** you began speaking while the expert was answering */
  interrupt(): void
}

const RATE = 16000
const FRAME = 320                                   // 20 ms
const PRE_ROLL = 20                                 // frames kept from before you started: 400 ms

/** the microphone, its samples sent on in blocks */
const TAP = `class Tap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(2048); this.n = 0 }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i]
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0 }
    }
    return true
  }
}
registerProcessor('mb-tap', Tap)`

export class Talk {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: AudioWorkletNode | null = null
  private state: TalkState = 'off'
  // bringing the microphone's rate down to 16 kHz
  private step = 3
  private acc = 0
  private accN = 0
  private pos = 0
  private frame = new Float32Array(FRAME)
  private fill = 0
  // the voice detector
  private floor = 0.004
  private run = 0
  private pre: Float32Array[] = []
  // the turn being spoken
  private chunks: Float32Array[] = []
  private voiced = 0
  private quiet = 0
  private checked = 0
  private partialAt = 0
  private partialBusy = false
  private turnId = 0

  constructor(private ev: TalkEvents, private expertBusy: () => boolean, private smart: boolean) {}

  get on() { return this.state !== 'off' }

  async start() {
    if (this.on) return
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } })
    this.ctx = new AudioContext()
    const url = URL.createObjectURL(new Blob([TAP], { type: 'text/javascript' }))
    try { await this.ctx.audioWorklet.addModule(url) } finally { URL.revokeObjectURL(url) }
    this.step = this.ctx.sampleRate / RATE
    this.node = new AudioWorkletNode(this.ctx, 'mb-tap')
    this.node.port.onmessage = (e) => this.take(e.data as Float32Array)
    this.ctx.createMediaStreamSource(this.stream).connect(this.node)
    this.reset()
    this.set('waiting')
  }

  stop() {
    this.turnId++
    this.node?.disconnect(); this.node = null
    this.stream?.getTracks().forEach((t) => t.stop()); this.stream = null
    void this.ctx?.close(); this.ctx = null
    this.reset()
    this.set('off')
    this.ev.level(0)
  }

  private set(s: TalkState) { if (s !== this.state) { this.state = s; this.ev.state(s) } }

  private reset() {
    this.chunks = []; this.voiced = 0; this.quiet = 0; this.checked = 0; this.partialAt = 0; this.run = 0
  }

  /** samples at the microphone's rate, averaged down to 16 kHz, cut into 20 ms frames */
  private take(block: Float32Array) {
    for (let i = 0; i < block.length; i++) {
      this.acc += block[i]; this.accN++; this.pos++
      if (this.pos >= this.step) {
        this.pos -= this.step
        this.frame[this.fill++] = this.acc / this.accN
        this.acc = 0; this.accN = 0
        if (this.fill === FRAME) { this.onFrame(this.frame.slice(0)); this.fill = 0 }
      }
    }
  }

  private onFrame(f: Float32Array) {
    let sum = 0
    for (let i = 0; i < f.length; i++) sum += f[i] * f[i]
    const rms = Math.sqrt(sum / f.length)
    this.ev.level(Math.min(1, rms * 9))
    // the room's own sound, followed slowly; speech stands above it
    const over = this.expertBusy()
    const threshold = over ? Math.max(0.035, this.floor * 5) : Math.max(0.012, this.floor * 3)
    const speech = rms > threshold
    if (!speech) this.floor = rms < this.floor ? this.floor * 0.9 + rms * 0.1 : this.floor * 0.998 + rms * 0.002

    if (this.state === 'waiting') {
      this.pre.push(f); if (this.pre.length > PRE_ROLL) this.pre.shift()
      this.run = speech ? this.run + 1 : 0
      // a clear start: 80 ms of speech - or 240 ms over the expert's own voice
      if (this.run >= (over ? 12 : 4)) {
        this.reset()
        this.chunks = this.pre.slice(); this.pre = []
        this.voiced = this.chunks.length
        this.set('hearing')
        if (over) this.ev.interrupt()
      }
      return
    }
    if (this.state !== 'hearing') return
    this.chunks.push(f)
    if (speech) { this.voiced++; this.quiet = 0; this.checked = 0 } else this.quiet++
    const ms = this.quiet * 20, length = this.chunks.length * 20
    // the words so far, about every 1.2 s of speech
    if (length - this.partialAt >= 1200 && !this.partialBusy && this.quiet < 10) void this.partial()
    if (this.voiced < 12 && ms >= 700) { this.back(); return }       // a cough, a click: not a turn
    if (length >= 30000) { void this.finish(); return }             // 30 s at most
    if (!this.smart) { if (ms >= 1200) void this.finish(); return }
    // a pause: has the thought ended? Smart Turn decides; asked again if the pause goes on
    if ((ms >= 400 && this.checked === 0) || (ms >= 1200 && this.checked === 1)) {
      this.checked++
      void this.check()
    } else if (ms >= 2500) void this.finish()
  }

  private samples(keepQuietMs = Infinity) {
    let n = this.chunks.length
    if (keepQuietMs !== Infinity) n = Math.max(1, n - Math.max(0, this.quiet - keepQuietMs / 20))
    const out = new Float32Array(n * FRAME)
    for (let i = 0; i < n; i++) out.set(this.chunks[i], i * FRAME)
    return out
  }

  private async check() {
    const id = this.turnId, at = this.chunks.length
    try {
      const all = this.samples()
      const r = await api.turn(wav(all.subarray(Math.max(0, all.length - 8 * RATE))))
      // still the same pause, nobody spoke since
      if (id === this.turnId && this.state === 'hearing' && r.complete && this.quiet >= this.chunks.length - at) void this.finish()
    } catch { /* if it cannot tell, the long pause still ends the turn */ }
  }

  private async partial() {
    this.partialBusy = true
    this.partialAt = this.chunks.length * 20
    const id = this.turnId
    try {
      const text = (await api.listen(wav(this.samples()))).trim()
      if (id === this.turnId && this.state === 'hearing' && text) this.ev.partial(text)
    } catch { /* the next try */ } finally { this.partialBusy = false }
  }

  private back() {
    this.turnId++
    this.reset()
    this.ev.partial('')
    this.set('waiting')
  }

  private async finish() {
    if (this.state !== 'hearing') return
    const id = ++this.turnId
    const audio = this.samples(300)
    this.reset()
    this.set('writing')
    let text = ''
    try { text = (await api.listen(wav(audio))).trim() } catch { /* nothing made out */ }
    if (id !== this.turnId) return                                  // stopped meanwhile
    this.ev.partial('')
    if ((this.state as TalkState) === 'writing') this.set('waiting')     // not stopped meanwhile
    if (text && !/^[.\s]*$/.test(text)) this.ev.turn(text)
  }
}
