import { useStore } from './store'
import { CUSTOMISABLE, THEMES, THEME_LOOK, contrast, readVar } from './theme'
import { useUI } from './ui'

export function ThemeSheet() {
  const board = useStore((s) => s.board)
  const run = useStore((s) => s.run)
  const setUI = useUI((s) => s.set)
  const settings = board?.settings ?? {}
  const palette = settings.palette ?? {}

  const write = (patch: Record<string, any>) =>
    run([{ kind: 'board.update', patch: { settings: { ...settings, ...patch } } }])

  const value = (key: string) => palette[key] || readVar(key)
  const ratio = contrast(value('--mb-text'), value('--mb-bg'))

  return (
    <div className="sheet" onMouseDown={(e) => e.stopPropagation()}>
      <h4>Theme</h4>
      <div className="theme-row">
        {THEMES.map((t) => {
          const look = THEME_LOOK[t]
          return (
            <button key={t} className="theme-chip" aria-pressed={(settings.theme || 'midnight') === t}
              title={look.feel} onClick={() => write({ theme: t })}>
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
      <div className="modal-actions" style={{ marginTop: 12 }}>
        <button className="btn" onClick={() => write({ palette: {} })}>Reset colours</button>
        <button className="btn" onClick={() => setUI('themeOpen', false)}>Done</button>
      </div>
    </div>
  )
}
