/** Speaking an expert's answer, sentence by sentence (DESIGN.md 41).
 *
 *  Each sentence is made on this computer (POST /api/voice/speak, Kokoro) and
 *  played through Web Audio; the next is fetched while one plays, so the
 *  voice runs on without gaps. It starts while the answer is still being
 *  written (DESIGN.md 46). Its speed is yours to set (DESIGN.md 44), and it
 *  says when it starts and stops, so you can be offered Stop - or speak over it.
 */
import { api } from '../api'

/** what is read aloud: words, without citations, markup or the follow-up lines */
export function speakable(text: string): string {
  return text.replace(/\[\[[^\]\n]{1,40}\]\]/g, '').replace(/^[ \t*_>#-]*next:.*$/gim, '')
    .replace(/Beyond your board:/g, '').replace(/[*_#>`]+/g, '').replace(/[ \t]+([.,;:!?])/g, '$1')
    .replace(/\s+/g, ' ').trim()
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

/** the answer up to its follow-up questions, which are shown, not spoken */
function beforeFollowUps(text: string): string {
  const m = text.match(/(^|\n)[ \t*_>#-]*next:/i)
  return m ? text.slice(0, m.index) : text
}

/** the part of an answer still being written that will not change: up to its
 *  last finished line or sentence */
function settled(text: string): string {
  const t = beforeFollowUps(text)
  let at = t.lastIndexOf('\n')
  const re = /[.!?](?=\s)/g
  for (let m = re.exec(t); m; m = re.exec(t)) at = Math.max(at, m.index + 1)
  return at > 0 ? t.slice(0, at) : ''
}

export const SPEEDS = [0.8, 0.9, 1, 1.1, 1.25]

let shared: AudioContext | null = null
/** one audio context for the page: the expert's voice plays where the
 *  microphone listens, so echo cancellation can take the voice out (DESIGN.md 45) */
export function audio(): AudioContext {
  if (!shared) shared = new AudioContext()
  return shared
}

/** The expert's voice. It speaks an answer while the answer is still being
 *  written (DESIGN.md 46): each sentence as soon as it is finished, the next
 *  one made while this one plays - no waiting for the whole answer first. */
export class Voice {
  private source: AudioBufferSourceNode | null = null
  private turn = 0
  private _playing = false
  private parts: string[] = []          // sentences waiting to be spoken
  private spoken = ''                   // what has been queued so far, as spoken text
  private ended = true                  // nothing more is coming
  private wake: (() => void) | null = null
  private name = 'af_heart'
  /** how fast it speaks: 0.8 slower ... 1.25 faster; the next sentence uses it */
  speed = 1
  /** told when it starts and stops speaking */
  onPlaying: (playing: boolean) => void = () => {}

  get playing() { return this._playing }
  private set(playing: boolean) {
    if (playing !== this._playing) { this._playing = playing; this.onPlaying(playing) }
  }

  /** start speaking an answer that is still being written */
  begin(voice: string) {
    this.stop()
    this.name = voice
    this.ended = false
    void this.run(++this.turn)
  }

  /** the answer so far: its finished sentences are queued */
  feed(text: string) { if (!this.ended) this.queue(settled(text)) }

  /** the whole answer, and anything to say after it: nothing more will come */
  end(text: string, after = '') {
    if (this.ended) return
    this.queue(beforeFollowUps(text) + (after ? `\n\n${after}` : ''))
    this.ended = true
    this.poke()
  }

  /** say it - stopping anything still being said */
  say(text: string, voice: string) { this.begin(voice); this.end(text) }

  stop() {
    this.turn++
    this.parts = []; this.spoken = ''; this.ended = true
    this.poke()
    try { this.source?.stop() } catch { /* already ended */ }
    this.source = null
    this.set(false)
  }

  private poke() { const w = this.wake; this.wake = null; w?.() }

  private queue(text: string) {
    const now = speakable(text)
    // what is new since last time - normally the text only grows at its end
    const fresh = now.startsWith(this.spoken) ? now.slice(this.spoken.length) : now.slice(Math.min(this.spoken.length, now.length))
    if (!fresh.trim()) return
    this.spoken = now
    this.parts.push(...sentences(fresh))
    this.poke()
  }

  private async run(turn: number) {
    const ctx = audio()
    if (ctx.state === 'suspended') await ctx.resume()
    const make = (s: string) => api.speak(s, this.name, this.speed).then((b) => ctx.decodeAudioData(b)).catch(() => null)
    let next: Promise<AudioBuffer | null> | null = null
    while (turn === this.turn) {
      if (!next) {
        const part = this.parts.shift()
        if (part) next = make(part)
        else if (this.ended) break
        else { await new Promise<void>((r) => { this.wake = r }); continue }
      }
      const buffer = await next
      const following = this.parts.shift()
      next = following ? make(following) : null              // the next one, while this plays
      if (!buffer || turn !== this.turn) continue
      this.set(true)
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
