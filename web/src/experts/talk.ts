/** Talking to an expert hands-free (DESIGN.md 44, 45).
 *
 *  Turn it on once and just speak. The page only carries sound: the
 *  microphone, through the browser's echo cancellation, brought to 16 kHz and
 *  streamed to /api/voice/stream. The server decides the turns, as the
 *  speech-to-speech pipeline behind CLARA does - Silero VAD for who is
 *  speaking, Smart Turn for when you have finished, a reopen window for a
 *  pause it misjudged - writes your words as you speak, and says what
 *  happened. The expert's voice plays through the same audio context as the
 *  microphone, so the echo canceller knows exactly what to take out.
 */
import { api } from '../api'
import { audio } from './voice'

export type TalkState = 'off' | 'waiting' | 'hearing' | 'writing'

export interface TalkEvents {
  state(s: TalkState): void
  /** how loud you are right now, 0..1 */
  level(v: number): void
  /** the words so far, while you speak */
  partial(text: string): void
  /** a finished turn: ask it */
  turn(text: string): void
  /** you began speaking - over the expert, if it was answering */
  interrupt(): void
  /** it cannot listen: say why */
  failed(message: string): void
}

/** the microphone, brought to 16 kHz 16-bit in blocks of 512 samples (32 ms) */
const MIC = `class Mic extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / 16000; this.acc = []; this.pos = 0 }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) this.acc.push(ch[i])
    while (this.acc.length - this.pos > this.ratio * 512 + 2) {
      const out = new Int16Array(512); let sum = 0
      for (let i = 0; i < 512; i++) {
        const at = this.pos + i * this.ratio, i0 = Math.floor(at), fr = at - i0
        const next = this.acc[i0 + 1] !== undefined ? this.acc[i0 + 1] : this.acc[i0]
        const v = Math.max(-1, Math.min(1, this.acc[i0] * (1 - fr) + next * fr))
        out[i] = v < 0 ? v * 0x8000 : v * 0x7fff; sum += v * v
      }
      this.pos += this.ratio * 512
      this.port.postMessage({ pcm: out.buffer, rms: Math.sqrt(sum / 512) }, [out.buffer])
      const done = Math.floor(this.pos)
      if (done > 0) { this.acc.splice(0, done); this.pos -= done }
    }
    return true
  }
}
registerProcessor('mb-mic', Mic)`

let workletReady: Promise<void> | null = null

export class Talk {
  private stream: MediaStream | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private node: AudioWorkletNode | null = null
  private sink: GainNode | null = null
  private ws: WebSocket | null = null
  private state: TalkState = 'off'

  constructor(private ev: TalkEvents) {}

  get on() { return this.state !== 'off' }

  async start() {
    if (this.on) return
    const ctx = audio()
    if (ctx.state === 'suspended') await ctx.resume()
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } })
    if (!workletReady) {
      const url = URL.createObjectURL(new Blob([MIC], { type: 'text/javascript' }))
      workletReady = ctx.audioWorklet.addModule(url).finally(() => URL.revokeObjectURL(url))
    }
    await workletReady
    const token = api.token()
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/voice/stream`
      + (token ? `?t=${encodeURIComponent(token)}` : ''))
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    await new Promise<void>((ok, fail) => {
      ws.onopen = () => ok()
      ws.onerror = () => fail(new Error('could not reach the listener'))
    })
    ws.onmessage = (m) => this.on && this.message(JSON.parse(m.data as string))
    ws.onclose = () => { if (this.on) { this.stop(); this.ev.failed('Listening stopped.') } }

    this.source = ctx.createMediaStreamSource(this.stream)
    this.node = new AudioWorkletNode(ctx, 'mb-mic')
    this.node.port.onmessage = (e) => {
      this.ev.level(Math.min(1, (e.data.rms as number) * 9))
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(e.data.pcm as ArrayBuffer)
    }
    this.source.connect(this.node)
    this.sink = ctx.createGain()
    this.sink.gain.value = 0                       // keeps the worklet running, silently
    this.node.connect(this.sink).connect(ctx.destination)
    this.set('waiting')
  }

  stop() {
    this.set('off')
    this.ws?.close(); this.ws = null
    this.source?.disconnect(); this.node?.disconnect(); this.sink?.disconnect()
    this.source = null; this.node = null; this.sink = null
    this.stream?.getTracks().forEach((t) => t.stop()); this.stream = null
    this.ev.level(0)
    this.ev.partial('')
  }

  private set(s: TalkState) { if (s !== this.state) { this.state = s; this.ev.state(s) } }

  private message(m: { type: string; text?: string; message?: string }) {
    switch (m.type) {
      case 'speech':                               // you began: the expert stops for you
        this.set('hearing'); this.ev.interrupt(); break
      case 'reopen':                               // you went on: the same turn
        this.set('hearing'); break
      case 'pause': break
      case 'writing':
        this.set('writing'); break
      case 'partial':
        if (m.text) this.ev.partial(m.text); break
      case 'turn':
        this.ev.partial(''); this.set('waiting')
        if (m.text) this.ev.turn(m.text)
        break
      case 'nothing':
        this.ev.partial(''); this.set('waiting'); break
      case 'error':
        this.stop(); this.ev.failed(m.message || 'Listening is not available.'); break
    }
  }
}
