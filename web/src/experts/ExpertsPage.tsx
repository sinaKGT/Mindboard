/** Experts (DESIGN.md 40): talk to experts made of your boards.
 *
 *  A desert at night where each expert stands as a monument of glowing sand,
 *  sized by how much it knows. Ask the guide which expert can help, or pick
 *  one and go in: its monument comes apart and you are inside its sand, where
 *  you talk to it. Nothing is remembered once you leave - each visit starts
 *  fresh, as asked - except that opening a cited card and coming back finds
 *  the conversation where it was (DESIGN.md 44).
 */
import { Fragment, useEffect, useRef, useState } from 'react'

import { api, type Expert, type ExpertDraft, type ShapeState } from '../api'
import type { BoardMeta } from '../types'
import { ExpertScene } from './scene'
import './experts.css'
import { SHAPES, SHAPE_WORDS } from './shapes'
import { AddCard } from './AddCard'
import { Talk, type TalkState } from './talk'
import { SPEEDS, Voice } from './voice'

const BEYOND = 'Beyond your board:'
const NEXT_LINE = /(^|\n)[ \t*_>#-]*(NEXT:[\s\S]*|N(E(X(T)?)?)?)$/
const COLOURS = ['#f0c070', '#7fd3c4', '#e9967a', '#d8c8a8', '#b4a0e8', '#8fc8f0', '#e6d36a', '#e88fb4']

interface Turn { role: 'user' | 'expert'; text: string; where?: Record<string, { board: string; title: string }>;
  next?: string; status?: 'thinking' | 'writing' | 'done'; error?: string; stopped?: boolean }

/** where a conversation waits while you look at a card it cited */
const RETURN = 'mb_xp_return'

/** go to a card on its board, wherever it is */
function openCard(board: string, card: string) {
  sessionStorage.setItem('mb_focus', card)
  location.hash = `#/b/${board}`
}

/** the icons in the bar, drawn in the page's gold rather than as coloured emoji */
const MicIcon = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
    <rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" /></svg>)
const SpeakerIcon = ({ on }: { on: boolean }) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
    {on ? <path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" /> : <path d="M16 9.5l5 5M21 9.5l-5 5" />}</svg>)

/** an answer: light formatting, citations as chips that open their card */
function Said({ turn, onCard }: { turn: Turn; onCard: (board: string, card: string) => void }) {
  const text = turn.status === 'done' ? turn.text : turn.text.replace(NEXT_LINE, '')
  const at = text.indexOf(BEYOND)
  const parts = at >= 0 ? [text.slice(0, at), text.slice(at + BEYOND.length)] : [text]
  // a citation the model got wrong - not a card it was given - is left out, not shown raw
  const line = (s: string) => s.split(/(\[\[[^\]\n]{1,40}\]\]|\*\*[^*]+\*\*)/g).map((piece, j) => {
    const cite = piece.match(/^\[\[([0-9A-Za-z]{26})\]\]$/)
    if (!cite && /^\[\[.*\]\]$/.test(piece)) return null
    if (cite) {
      const w = turn.where?.[cite[1]]
      return w ? <button key={j} className="xp-cite" title="Open this card on its board"
        onClick={() => onCard(w.board, cite[1])}>{w.title}</button> : null
    }
    const bold = piece.match(/^\*\*([^*]+)\*\*$/)
    return bold ? <strong key={j}>{bold[1]}</strong> : <Fragment key={j}>{piece}</Fragment>
  })
  return (
    <div className="xp-said">
      {parts[0].trim() ? <div>{parts[0].trim().split('\n').map((l, i) => <div key={i}>{line(l)}</div>)}</div> : null}
      {parts[1]?.trim() ? (
        <div className="xp-beyond"><div className="xp-beyond-head">From its own knowledge</div>
          {parts[1].trim().split('\n').map((l, i) => <div key={i}>{line(l)}</div>)}</div>
      ) : null}
    </div>
  )
}

function Editor({ expert, shapes, onDone }: {
  expert: Expert | null; shapes: ShapeState[]; onDone: (changed: boolean) => void
}) {
  const [boards, setBoards] = useState<BoardMeta[]>([])
  const [d, setD] = useState<ExpertDraft>({
    name: expert?.name ?? '', brief: expert?.brief ?? '', boards: expert?.boards.map((b) => b.id) ?? [],
    colour: expert?.colour ?? '', shape: expert?.shape ?? '' })
  const [error, setError] = useState('')
  useEffect(() => { void api.listBoards().then(setBoards) }, [])
  const save = async () => {
    try {
      if (expert) await api.updateExpert(expert.id, d)
      else await api.createExpert(d)
      onDone(true)
    } catch (e: any) { setError(e.message) }
  }
  const remove = async () => {
    if (!expert || !confirm(`Remove ${expert.name}? Its boards are not touched.`)) return
    await api.deleteExpert(expert.id)
    onDone(true)
  }
  return (
    <div className="xp-modal" onClick={() => onDone(false)}>
      <div className="xp-editor" onClick={(e) => e.stopPropagation()}>
        <h3>{expert ? 'Edit expert' : 'New expert'}</h3>
        <input placeholder="Name - Retrieval Engineer" value={d.name} autoFocus
          onChange={(e) => setD({ ...d, name: e.target.value })} />
        <textarea rows={3} placeholder="What it knows and how it talks - a sentence or two"
          value={d.brief} onChange={(e) => setD({ ...d, brief: e.target.value })} />
        <div className="xp-field">Its boards - all it knows</div>
        <div className="xp-boards">
          {boards.map((b) => (
            <label key={b.id}><input type="checkbox" checked={d.boards.includes(b.id)}
              onChange={(e) => setD({ ...d, boards: e.target.checked ? [...d.boards, b.id]
                : d.boards.filter((x) => x !== b.id) })} /> {b.title}
              <span className="xp-count">{b.node_count} cards</span></label>
          ))}
        </div>
        <div className="xp-field">Stands as</div>
        {/* earned, not chosen freely: each monument opens as your library grows */}
        <div className="xp-shapes">
          {SHAPES.map((s) => {
            const st = shapes.find((x) => x.shape === s)
            const open = !st || st.unlocked || expert?.shape === s
            return (
              <button key={s} className={`${d.shape === s ? 'on' : ''} ${open ? '' : 'locked'}`}
                disabled={!open} onClick={() => setD({ ...d, shape: s })}
                title={open ? SHAPE_WORDS[s] : `Locked - needs ${st!.need} (you have ${st!.have} of ${st!.of})`}>
                {open ? '' : '🔒 '}{SHAPE_WORDS[s]}
                {!open ? <span className="xp-progress">{st!.have}/{st!.of}</span> : null}
              </button>
            )
          })}
        </div>
        <div className="xp-colours">
          {COLOURS.map((c) => <button key={c} style={{ background: c }} className={d.colour === c ? 'on' : ''}
            onClick={() => setD({ ...d, colour: c })} aria-label={`Colour ${c}`} />)}
        </div>
        {error ? <div className="xp-error">{error}</div> : null}
        <div className="xp-actions">
          {expert ? <button className="xp-danger" onClick={() => void remove()}>Remove</button> : <span />}
          <span>
            <button onClick={() => onDone(false)}>Cancel</button>
            <button className="xp-primary" onClick={() => void save()}>Save</button>
          </span>
        </div>
      </div>
    </div>
  )
}

export function ExpertsPage() {
  const host = useRef<HTMLDivElement>(null)
  const scene = useRef<ExpertScene | null>(null)
  const [experts, setExpertsState] = useState<Expert[]>([])
  const expertsRef = useRef<Expert[]>([])
  const setExperts = (l: Expert[]) => { expertsRef.current = l; setExpertsState(l) }
  const [current, setCurrent] = useState(0)
  const [inside, setInside] = useState(false)
  const [turns, setTurns] = useState<Turn[]>([])
  const [guideSaid, setGuideSaid] = useState<{ why: string; picked: { id: string; name: string }[] } | null>(null)
  const [busy, setBusyState] = useState(false)
  const busyRef = useRef(false)
  const setBusy = (b: boolean) => { busyRef.current = b; setBusyState(b) }
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState<Expert | null | undefined>(undefined)
  const [loaded, setLoaded] = useState(false)
  const [shapes, setShapes] = useState<ShapeState[]>([])
  const [unlocked, setUnlocked] = useState<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  // the voice: on unless turned off, if it is set up (mindboard ai voice)
  const voice = useRef(new Voice())
  const [voiceReady, setVoiceReady] = useState(false)
  const [cast, setCast] = useState<string[]>([])
  const [voiceOn, setVoiceOn] = useState(localStorage.getItem('mb_xp_voice') !== '0')
  const [speaking, setSpeaking] = useState(false)
  const [speed, setSpeed] = useState(() => Number(localStorage.getItem('mb_xp_speed')) || 1)
  // talking hands-free: just speak; Smart Turn tells when you have finished (DESIGN.md 44)
  const talk = useRef<Talk | null>(null)
  const smart = useRef(false)
  const [canListen, setCanListen] = useState(false)
  const [talkState, setTalkState] = useState<TalkState>('off')
  const [heard, setHeard] = useState('')
  const [adding, setAdding] = useState<number | null>(null)   // the answer being made a card
  const bar = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const stick = useRef(true)               // follow the answer down - unless you scrolled up to read
  const turnsRef = useRef<Turn[]>([])
  turnsRef.current = turns

  const load = async () => {
    const got = await api.experts(), list = got.experts
    setExperts(list); setShapes(got.shapes)
    // a monument opened since last time: say so
    const open = got.shapes.filter((x) => x.unlocked).map((x) => x.shape)
    const seen: string[] = JSON.parse(localStorage.getItem('mb_xp_unlocked') ?? '["ankh"]')
    const fresh = open.filter((x) => !seen.includes(x))
    if (fresh.length) setUnlocked(fresh.map((x) => SHAPE_WORDS[x as keyof typeof SHAPE_WORDS]).join(' and '))
    localStorage.setItem('mb_xp_unlocked', JSON.stringify(open))
    scene.current?.setExperts(list.map((e) => ({ id: e.id, name: e.name, colour: e.colour, shape: e.shape, cards: e.cards })))
    setLoaded(true)
    // back from a card the conversation cited: the conversation, where it was
    const back = JSON.parse(sessionStorage.getItem(RETURN) ?? 'null') as { expert: string; turns: Turn[]; at: number } | null
    sessionStorage.removeItem(RETURN)
    const i = back && Date.now() - back.at < 2 * 3600e3 ? list.findIndex((x) => x.id === back.expert) : -1
    if (i >= 0) { enter(i); setTurns(back!.turns) }
  }
  /** open a cited card, keeping the conversation to come back to */
  const goToCard = (board: string, card: string) => {
    const e = expertsRef.current[current]
    if (e) sessionStorage.setItem(RETURN, JSON.stringify({ expert: e.id, at: Date.now(),
      turns: turnsRef.current.map((t) => (t.status && t.status !== 'done' ? { ...t, status: 'done', stopped: true } : t)) }))
    interrupt()
    openCard(board, card)
  }
  useEffect(() => {
    if (!host.current) return
    scene.current = new ExpertScene(host.current, (i) => enter(i), (i) => setEditing(expertsRef.current[i]))
    void load()
    void api.voice().then((v) => { setVoiceReady(v.installed); setCast(v.cast); setCanListen(v.listen); smart.current = v.turn }).catch(() => {})
    voice.current.onPlaying = setSpeaking
    voice.current.speed = speed
    // the sand moves with the voice: its loudness, read every frame
    let raf = 0
    const listen = () => {
      raf = requestAnimationFrame(listen)
      // the voice speaks; the sand stays as it is
      const s = scene.current
      if (s && s.speaking) s.speaking = false
    }
    listen()
    return () => { cancelAnimationFrame(raf); voice.current.stop(); talk.current?.stop(); scene.current?.dispose(); controller.current?.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { if (scene.current) scene.current.current = current }, [current])
  useEffect(() => { if (stick.current) scroller.current?.scrollTo({ top: scroller.current.scrollHeight }) }, [turns])
  // a new monument is announced for a while, then steps aside
  useEffect(() => { if (!unlocked) return; const t = setTimeout(() => setUnlocked(null), 9000); return () => clearTimeout(t) }, [unlocked])

  const choose = (i: number) => { setCurrent((i + experts.length) % Math.max(experts.length, 1)); setGuideSaid(null) }
  function enter(i: number) {
    setCurrent(i)
    if (scene.current) { scene.current.current = i; scene.current.inside = true; scene.current.calls.clear() }
    setInside(true); setGuideSaid(null); setTurns([]); setAdding(null); stick.current = true
  }
  /** stop the expert: its answer, and its voice - Stop, Esc, or speaking over it */
  function interrupt() {
    if (busyRef.current) { controller.current?.abort(); void api.expertStop().catch(() => {}) }
    voice.current.stop()
  }
  const leave = () => {
    controller.current?.abort()
    voice.current.stop()
    if (scene.current) { scene.current.inside = false; scene.current.thinking = false; scene.current.speaking = false }
    setInside(false); setTurns([])
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === 'INPUT' || (e.target as HTMLElement)?.tagName === 'TEXTAREA') {
        if (e.key === 'Escape' && inside) { if (busyRef.current || voice.current.playing) interrupt(); else leave() }
        return
      }
      if (editing !== undefined) return
      if (e.key === 'Escape' && inside) { if (busyRef.current || voice.current.playing) interrupt(); else leave() }
      if (!inside && e.key === 'ArrowLeft') choose(current - 1)
      if (!inside && e.key === 'ArrowRight') choose(current + 1)
      if (!inside && e.key === 'Enter' && experts.length) enter(current)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  })

  /** the guide: who can help with this? */
  const askGuide = async (question: string) => {
    const s = scene.current!
    setBusy(true); setGuideSaid(null); s.guiding = true; s.calls.clear()
    try {
      const r = await api.guide(question)
      s.calls = new Set(r.picked.map((p) => p.id))
      const first = experts.findIndex((e) => e.id === r.picked[0]?.id)
      if (first >= 0) setCurrent(first)
      setGuideSaid({ why: r.why, picked: r.picked })
    } catch (e: any) {
      setGuideSaid({ why: e.message || 'The guide could not answer', picked: [] })
    } finally { s.guiding = false; setBusy(false) }
  }

  /** talking to the expert inside */
  const askExpert = async (question: string) => {
    const e = experts[current], s = scene.current!
    const history = turns.filter((t) => t.status === 'done' || t.role === 'user').slice(-10)
      .map((t) => ({ role: t.role === 'user' ? 'user' : 'assistant', text: t.text }))
    stick.current = true
    setTurns((ts) => [...ts, { role: 'user', text: question }, { role: 'expert', text: '', status: 'thinking' }])
    const patch = (p: Partial<Turn>) => setTurns((ts) => { const c = ts.slice(); c[c.length - 1] = { ...c[c.length - 1], ...p }; return c })
    const ctl = new AbortController(); controller.current = ctl
    setBusy(true); s.thinking = true
    voice.current.stop()
    let text = ''
    try {
      await api.askExpert(e.id, { question, history }, (name, data) => {
        if (name === 'context') patch({ where: data.where })
        else if (name === 'text') {
          text += data.text
          s.thinking = false
          patch({ text, status: 'writing' })
        } else if (name === 'done') {
          patch({ text: data.answer, next: data.next || undefined, status: 'done' })
          // and it says it, in its own voice
          if (voiceReady && voiceOn && data.answer) {
            const i = expertsRef.current.findIndex((x) => x.id === e.id)
            const spoken = data.next ? `${data.answer}\n\nWould you like to know: ${data.next}` : data.answer
            void voice.current.say(spoken, e.voice || cast[Math.max(i, 0) % Math.max(cast.length, 1)] || 'af_heart')
          }
        }
        else if (name === 'error') patch({ error: data.message, status: 'done' })
      }, ctl.signal)
    } catch (err: any) {
      if (!ctl.signal.aborted) patch({ error: err.message || 'could not ask', status: 'done' })
      else patch({ text, status: 'done', stopped: true })            // interrupted: what was said stays
    } finally { s.thinking = false; s.speaking = false; setBusy(false) }
  }

  const send = (text: string) => {
    if (!text.trim()) return
    if (busyRef.current) {
      // spoken over an answer: it is being stopped - ask as soon as it has
      if (talk.current?.on) { interrupt(); setTimeout(() => sendRef.current(text), 250) }
      return
    }
    setDraft('')
    if (inside) void askExpert(text.trim())
    else void askGuide(text.trim())
  }
  const e = experts[current]

  const sendRef = useRef(send)
  sendRef.current = send
  const interruptRef = useRef(interrupt)
  interruptRef.current = interrupt

  /** the microphone: on, and you just talk; off again when you are done */
  const toggleTalk = async () => {
    if (talk.current?.on) { talk.current.stop(); setHeard(''); return }
    talk.current = new Talk({
      state: setTalkState,
      level: (v) => bar.current?.style.setProperty('--lv', v.toFixed(3)),
      partial: setHeard,
      turn: (text) => { setHeard(''); sendRef.current(text) },
      interrupt: () => interruptRef.current(),
    }, () => busyRef.current || voice.current.playing, smart.current)
    try { await talk.current.start() }
    catch { talk.current.stop(); setGuideSaid({ why: 'The microphone is not available - check the browser allows it.', picked: [] }) }
  }
  const cycleSpeed = () => {
    const next = SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length] ?? 1
    setSpeed(next); voice.current.speed = next; localStorage.setItem('mb_xp_speed', String(next))
  }
  const talking = talkState === 'hearing' || talkState === 'writing'
  const hint = talkState === 'writing' ? 'Writing down what you said…'
    : talkState === 'hearing' ? 'Hearing you…'
    : talkState === 'waiting' ? (busy || speaking ? 'Speak to interrupt…'
      : inside && e ? `Listening - just speak to ${e.name}…` : 'Listening - just ask…')
    : inside && e ? `Ask ${e.name}…` : 'Not sure who to ask? Ask here…'
  const unlockCard = unlocked ? (
    <div className="xp-unlock" onClick={() => setUnlocked(null)}>
      <div className="xp-unlock-head">A new monument</div>
      <div className="xp-unlock-name">{unlocked}</div>
      <div>Your library has grown enough. Your next expert can stand as it.</div>
    </div>
  ) : null

  return (
    <div className={`xp ${inside ? 'inside' : ''}`} ref={host}>
      <div className="xp-top">
        <button className="xp-crumb" onClick={() => { location.hash = '' }}>Mindboard</button>
        <span>/</span><b>Experts</b>
      </div>
      {!inside ? <button className="xp-new" onClick={() => setEditing(null)}>+ New expert</button> : null}
      {loaded && !experts.length ? (
        <div className="xp-empty">
          {unlockCard}
          <div className="xp-empty-title">Your experts live here</div>
          <div>Each one knows the boards you give it, and answers from them.</div>
          <button className="xp-primary" onClick={() => setEditing(null)}>Make your first expert</button>
        </div>
      ) : null}
      {!inside && experts.length > 1 ? (
        <>
          <button className="xp-nav" style={{ left: 18 }} onClick={() => choose(current - 1)} aria-label="Previous expert">&#8249;</button>
          <button className="xp-nav" style={{ right: 18 }} onClick={() => choose(current + 1)} aria-label="Next expert">&#8250;</button>
        </>
      ) : null}

      {inside && e ? (
        <div className="xp-page">
          <button className="xp-back" onClick={leave}>&#8249; All experts</button>
          <div className="xp-who">{e.name}</div>
          <div className="xp-cartouches">{e.boards.map((b) => <span key={b.id} className="xp-cartouche">{b.title}</span>)}</div>
          <div className="xp-turns" ref={scroller}
            onScroll={(ev) => { const el = ev.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60 }}>
            {turns.map((t, i) => t.role === 'user'
              ? <div key={i} className="xp-q">{t.text}</div>
              : (
                <div key={i} className="xp-a">
                  {t.status === 'thinking' ? <div className="xp-note">Thinking…</div> : <Said turn={t} onCard={goToCard} />}
                  {t.stopped ? <div className="xp-note">Stopped</div> : null}
                  {t.error ? <div className="xp-error">{t.error}</div> : null}
                  {adding === i ? (
                    <AddCard boards={e.boards} question={turns[i - 1]?.text ?? ''} answer={t.text} where={t.where ?? {}}
                      onDone={() => setAdding(null)} onOpen={goToCard} />
                  ) : t.status === 'done' && t.text.trim() && !t.error && !(busy && i === turns.length - 1) ? (
                    <button className="xp-add" onClick={() => setAdding(i)}>+ Add to board</button>
                  ) : null}
                  {t.next && i === turns.length - 1 && !busy ? (
                    <div className="xp-next"><span>Would you like to know</span>
                      <button onClick={() => send(t.next!)}>{t.next}</button></div>
                  ) : null}
                </div>
              ))}
          </div>
        </div>
      ) : null}

      {guideSaid && !inside ? (
        <div className="xp-guide">
          <div>{guideSaid.why}</div>
          <div>{guideSaid.picked.map((p) => {
            const i = experts.findIndex((x) => x.id === p.id)
            return i >= 0 ? <button key={p.id} className="xp-go" onClick={() => enter(i)}>Ask {p.name}</button> : null
          })}</div>
        </div>
      ) : null}

      {experts.length ? (
        <div className={`xp-bar ${busy ? 'busy' : ''} talk-${talkState}`} ref={bar}>
          {talkState !== 'off' ? <span className="xp-wave" aria-hidden="true"><i /><i /><i /><i /><i /></span> : null}
          <input value={talking ? heard : draft} readOnly={talking} className={talking ? 'heard' : ''}
            onChange={(ev) => setDraft(ev.target.value)}
            onKeyDown={(ev) => { if (ev.key === 'Enter') send(draft) }}
            placeholder={hint} />
          {canListen ? (
            <button className={`xp-mic ${talkState !== 'off' ? 'on' : ''}`}
              title={talkState !== 'off' ? 'Listening - press to stop' : 'Talk - just speak, hands-free'}
              aria-label={talkState !== 'off' ? 'Stop listening' : 'Talk'}
              onClick={() => void toggleTalk()}><MicIcon /></button>
          ) : null}
          {inside && voiceReady && voiceOn ? (
            <button className="xp-speed" title="How fast the expert speaks" onClick={cycleSpeed}>{speed}×</button>
          ) : null}
          {inside && voiceReady ? (
            <button className={`xp-voice ${voiceOn ? 'on' : ''}`} title={voiceOn ? 'Voice on' : 'Voice off'}
              aria-label={voiceOn ? 'Turn the voice off' : 'Turn the voice on'}
              onClick={() => { const on = !voiceOn; setVoiceOn(on); localStorage.setItem('mb_xp_voice', on ? '1' : '0')
                if (!on) voice.current.stop() }}><SpeakerIcon on={voiceOn} /></button>
          ) : null}
          {busy || speaking ? <button className="xp-send" onClick={interrupt} title="Stop (Esc)" aria-label="Stop">&#9632;</button>
            : <button className="xp-send" onClick={() => send(draft)} aria-label="Ask">&#8593;</button>}
        </div>
      ) : null}

      {/* never over a conversation; above the empty desert's words, not on them */}
      {experts.length > 0 && !inside ? <div className="xp-unlock-top">{unlockCard}</div> : null}
      {editing !== undefined ? <Editor expert={editing} shapes={shapes} onDone={(changed) => { setEditing(undefined); if (changed) void load() }} /> : null}
    </div>
  )
}
