import { useEffect, useState } from 'react'

import { api, type CustomTheme } from './api'
import { Icon } from './icons'
import { useStore } from './store'
import { CUSTOMISABLE, THEMES, THEME_LOOK, contrast, readVar } from './theme'
import { useUI } from './ui'

export function ThemeSheet() {
  const board = useStore((s) => s.board)
  const run = useStore((s) => s.run)
  const say = useStore((s) => s.say)
  const setUI = useUI((s) => s.set)
  const settings = board?.settings ?? {}
  const palette = settings.palette ?? {}

  // your own themes (DESIGN.md 52): kept by Mindboard, offered on every board
  const [mine, setMine] = useState<CustomTheme[]>([])
  const using = mine.find((t) => t.id === settings.custom)
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => { api.themes().then((r) => setMine(r.themes)).catch(() => {}) }, [])
  useEffect(() => { if (using) setName(using.name) }, [using?.id])  // eslint-disable-line react-hooks/exhaustive-deps

  const write = (patch: Record<string, any>) =>
    run([{ kind: 'board.update', patch: { settings: { ...settings, ...patch } } }])

  const value = (key: string) => palette[key] || readVar(key)
  const ratio = contrast(value('--mb-text'), value('--mb-bg'))

  /** a built-in theme: its own colours, so the colours of one of yours go */
  const pickBuiltIn = (t: string) => write({ theme: t, custom: null, palette: settings.custom ? {} : palette })
  /** one of yours: its base theme and every colour it saved, copied onto the board */
  const pickMine = (t: CustomTheme) => write({ theme: t.base, custom: t.id, palette: t.palette })

  /** Save the colours on screen as a theme of your own. Saving under a name you
   *  already have updates that theme. */
  const save = async () => {
    if (!name.trim() || saving) return
    setSaving(true)
    try {
      const colours = Object.fromEntries(CUSTOMISABLE.map(({ key }) => [key, value(key)]))
      const saved = await api.saveTheme({ name: name.trim(), base: settings.theme || 'midnight', palette: colours })
      setMine((m) => (m.some((t) => t.id === saved.id) ? m.map((t) => (t.id === saved.id ? saved : t)) : [...m, saved]))
      write({ custom: saved.id, palette: saved.palette })
      say(`Saved your theme “${saved.name}”`)
    } catch (e: any) {
      say(e.message || 'Could not save the theme')
    } finally { setSaving(false) }
  }

  const remove = async (t: CustomTheme) => {
    if (!confirm(`Delete your theme “${t.name}”? Boards using it keep their colours.`)) return
    try {
      await api.deleteTheme(t.id)
      setMine((m) => m.filter((x) => x.id !== t.id))
      if (settings.custom === t.id) write({ custom: null })
    } catch (e: any) { say(e.message || 'Could not delete the theme') }
  }

  return (
    <div className="sheet theme-sheet" onMouseDown={(e) => e.stopPropagation()}>
      <h4>Theme</h4>
      <div className="theme-row">
        {THEMES.map((t) => {
          const look = THEME_LOOK[t]
          return (
            <button key={t} className="theme-chip" aria-pressed={!settings.custom && (settings.theme || 'midnight') === t}
              title={look.feel} onClick={() => pickBuiltIn(t)}>
              {/* a tiny preview: its canvas, a card on it, its accent */}
              <span className="theme-swatch" style={{ background: look.bg }}>
                <span style={{ background: look.card }} />
                <span style={{ background: look.accent }} />
              </span>
              <span className="theme-name">{t}</span>
              <span className="theme-feel">{look.feel}</span>
            </button>
          )
        })}
      </div>

      {mine.length ? (
        <>
          <div className="theme-section">Your themes</div>
          <div className="theme-row">
            {mine.map((t) => {
              const look = THEME_LOOK[t.base as keyof typeof THEME_LOOK] ?? THEME_LOOK.midnight
              return (
                <div key={t.id} className="theme-mine">
                  <button className="theme-chip" aria-pressed={settings.custom === t.id}
                    title={`${t.name} - your own theme`} onClick={() => pickMine(t)}>
                    <span className="theme-swatch" style={{ background: t.palette['--mb-bg'] || look.bg }}>
                      <span style={{ background: t.palette['--mb-surface'] || look.card }} />
                      <span style={{ background: t.palette['--mb-accent'] || look.accent }} />
                    </span>
                    <span className="theme-name theme-own">{t.name}</span>
                    <span className="theme-feel">your own</span>
                  </button>
                  <button className="theme-del" title={`Delete “${t.name}”`} onClick={() => remove(t)}>
                    <Icon name="x" size={10} />
                  </button>
                </div>
              )
            })}
          </div>
        </>
      ) : null}

      <div className="theme-section">Colours</div>
      {CUSTOMISABLE.map(({ key, label }) => (
        <div className="row" key={key}>
          <span>{label}</span>
          <input type="color" value={value(key)}
            onChange={(e) => write({ palette: { ...palette, [key]: e.target.value } })} />
        </div>
      ))}
      {ratio < 4.5 && (
        <div className="row" style={{ color: 'var(--mb-danger)', fontSize: 11.5 }}>
          text contrast {ratio.toFixed(1)}:1 - below the 4.5:1 readable threshold
        </div>
      )}

      <div className="theme-save">
        <input value={name} maxLength={40} placeholder="Name your theme"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void save() }} />
        <button className="btn primary" disabled={!name.trim() || saving} onClick={() => void save()}
          title="Save these colours as a theme of your own, for every board">
          {mine.some((t) => t.name.toLowerCase() === name.trim().toLowerCase()) ? 'Update' : 'Save'}
        </button>
      </div>

      <div className="modal-actions" style={{ marginTop: 12 }}>
        <button className="btn" onClick={() => write({ palette: {}, custom: null })}>Reset colours</button>
        <button className="btn" onClick={() => setUI('themeOpen', false)}>Done</button>
      </div>
    </div>
  )
}
