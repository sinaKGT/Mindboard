/** Speaking an expert's answer, sentence by sentence (DESIGN.md 41).
 *
 *  Each sentence is made on this computer (POST /api/voice/speak, Kokoro) and
 *  played through Web Audio; the next is fetched while one plays, so the
 *  voice runs on without gaps. An analyser reports how loud it is right now,
 *  and the sand moves with it.
 */
import { api } from '../api'

/** what is read aloud: words, without citations, markup or the NEXT line */
export function speakable(text: string): string {
  return text.replace(/\[\[[0-9A-Za-z]{26}\]\]/g, '').replace(/^[ \t]*NEXT:.*$/gm, '')
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

export class Voice {
  private ctx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private source: AudioBufferSourceNode | null = null
  private turn = 0
  private data = new Uint8Array(1024)
  playing = false

  private audio() {
    if (!this.ctx) {
      this.ctx = new AudioContext()
      this.analyser = this.ctx.createAnalyser()
      this.analyser.fftSize = 1024
      this.analyser.connect(this.ctx.destination)
    }
    return this.ctx
  }

  /** say it - stopping anything still being said */
  async say(text: string, voice: string) {
    this.stop()
    const turn = ++this.turn, ctx = this.audio()
    if (ctx.state === 'suspended') await ctx.resume()
    const parts = sentences(text)
    const fetchOne = (s: string) => api.speak(s, voice).then((b) => ctx.decodeAudioData(b)).catch(() => null)
    let next = parts.length ? fetchOne(parts[0]) : null
    for (let i = 0; i < parts.length && turn === this.turn; i++) {
      const buffer = await next
      next = i + 1 < parts.length ? fetchOne(parts[i + 1]) : null     // the next one, while this plays
      if (!buffer || turn !== this.turn) continue
      await new Promise<void>((done) => {
        const src = ctx.createBufferSource()
        src.buffer = buffer
        src.connect(this.analyser!)
        src.onended = () => done()
        this.source = src
        this.playing = true
        src.start()
      })
    }
    if (turn === this.turn) { this.playing = false; this.source = null }
  }

  stop() {
    this.turn++
    try { this.source?.stop() } catch { /* already ended */ }
    this.source = null
    this.playing = false
  }

  /** how loud it is right now, 0..1 */
  level(): number {
    if (!this.analyser || !this.playing) return 0
    this.analyser.getByteTimeDomainData(this.data)
    let sum = 0
    for (const v of this.data) { const x = (v - 128) / 128; sum += x * x }
    return Math.min(1, Math.sqrt(sum / this.data.length) * 4.5)
  }
}
