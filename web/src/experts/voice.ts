/** Speaking an expert's answer, sentence by sentence (DESIGN.md 41).
 *
 *  Each sentence is made on this computer (POST /api/voice/speak, Kokoro) and
 *  played through Web Audio; the next is fetched while one plays, so the
 *  voice runs on without gaps. Its speed is yours to set (DESIGN.md 44), and
 *  it says when it starts and stops, so you can be offered Stop - or speak
 *  over it.
 */
import { api } from '../api'

/** what is read aloud: words, without citations, markup or the NEXT line */
export function speakable(text: string): string {
  return text.replace(/\[\[[^\]\n]{1,40}\]\]/g, '').replace(/^[ \t]*NEXT:.*$/gm, '')
    .replace(/Beyond your board:/g, '').replace(/[*_#>`]+/g, '').replace(/\s+/g, ' ').trim()
}

/** sentences, joined until each is a comfortable mouthful */
function sentences(text: string): string[] {
  const out: string[] = []
  for (const s of speakable(text).split(/(?<=[.!?])\s+/)) {
    if (out.length && (out[out.length - 1].length < 60 || s.length < 20) && out[out.length - 1].length + s.length < 240)
      out[out.length - 1] += ' ' + s
    else if (s.trim()) out.push(s.trim())
  }
  return out
}

export const SPEEDS = [0.8, 0.9, 1, 1.1, 1.25]

export class Voice {
  private ctx: AudioContext | null = null
  private source: AudioBufferSourceNode | null = null
  private turn = 0
  private _playing = false
  /** how fast it speaks: 0.8 slower ... 1.25 faster; the next sentence uses it */
  speed = 1
  /** told when it starts and stops speaking */
  onPlaying: (playing: boolean) => void = () => {}

  get playing() { return this._playing }
  private set(playing: boolean) {
    if (playing !== this._playing) { this._playing = playing; this.onPlaying(playing) }
  }

  private audio() {
    if (!this.ctx) this.ctx = new AudioContext()
    return this.ctx
  }

  /** say it - stopping anything still being said */
  async say(text: string, voice: string) {
    this.stop()
    const turn = ++this.turn, ctx = this.audio()
    if (ctx.state === 'suspended') await ctx.resume()
    const parts = sentences(text)
    const fetchOne = (s: string) => api.speak(s, voice, this.speed).then((b) => ctx.decodeAudioData(b)).catch(() => null)
    if (parts.length) this.set(true)
    let next = parts.length ? fetchOne(parts[0]) : null
    for (let i = 0; i < parts.length && turn === this.turn; i++) {
      const buffer = await next
      next = i + 1 < parts.length ? fetchOne(parts[i + 1]) : null     // the next one, while this plays
      if (!buffer || turn !== this.turn) continue
      await new Promise<void>((done) => {
        const src = ctx.createBufferSource()
        src.buffer = buffer
        src.connect(ctx.destination)
        src.onended = () => done()
        this.source = src
        src.start()
      })
    }
    if (turn === this.turn) { this.source = null; this.set(false) }
  }

  stop() {
    this.turn++
    try { this.source?.stop() } catch { /* already ended */ }
    this.source = null
    this.set(false)
  }
}

/** 16 kHz mono samples as a WAV file - what whisper.cpp and Smart Turn read */
export function wav(pcm: Float32Array): Blob {
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2))
  const text = (o: number, t: string) => { for (let i = 0; i < t.length; i++) out.setUint8(o + i, t.charCodeAt(i)) }
  text(0, 'RIFF'); out.setUint32(4, 36 + pcm.length * 2, true); text(8, 'WAVE'); text(12, 'fmt ')
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true)
  out.setUint32(24, 16000, true); out.setUint32(28, 32000, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true)
  text(36, 'data'); out.setUint32(40, pcm.length * 2, true)
  for (let i = 0; i < pcm.length; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true)
  return new Blob([out], { type: 'audio/wav' })
}
