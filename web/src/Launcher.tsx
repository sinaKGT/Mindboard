/** The first page. Each tile draws the board's actual top-level graph, so a
 *  shelf of boards reads as a shelf of distinct things.
 *
 *  DESIGN.md 57: boards can be gathered into groups - "AI work", "Teaching" -
 *  each shown as a stack of its boards with how many it holds; a board goes
 *  into one by dragging its tile onto the group, or from the tile's folder
 *  button. The page has a look of its own, a theme and a background pattern.
 *  Groups and look are kept by Mindboard (home.py) and travel in backups.
 */
import { useCallback, useEffect, useState } from 'react'

import { api, type BoardGroup, type Home } from './api'
import { backUp } from './backup'
import { Constellation } from './Constellation'
import { Icon } from './icons'
import { GraphMini, ago } from './Mini'
import { useStore } from './store'
import { NODE_COLOURS, THEMES, THEME_LOOK, applyTheme } from './theme'
import type { BoardMeta } from './types'

/** the colours a tile or group can take: the card colours, and a teal */
const COLOURS = [...NODE_COLOURS, '#4fd1c5']
const PATTERNS: { key: string; name: string }[] = [
  { key: 'dots', name: 'Dots' }, { key: 'grid', name: 'Grid' }, { key: 'lines', name: 'Lines' },
  { key: 'dunes', name: 'Dunes' }, { key: 'stars', name: 'Stars' }, { key: 'plain', name: 'Plain' },
]

const TYPE_ORDER = ['text', 'image', 'video', 'link', 'file', 'meta'] as const
const TYPE_LABEL: Record<string, string> = {
  text: 'text', image: 'image', video: 'video', link: 'link', file: 'file', meta: 'group',
}

function Composition({ counts }: { counts: Record<string, number> }) {
  const shown = TYPE_ORDER.filter((t) => counts[t])
  if (!shown.length) return <span className="type-dots empty">empty</span>
  return (
    <span className="type-dots">
      {shown.map((t) => (
        <span key={t} className="type-dot" title={`${counts[t]} ${TYPE_LABEL[t]}`}>
          <i style={{ background: `var(--mb-type-${t})` }} />
          {counts[t]}
        </span>
      ))}
    </span>
  )
}

/** A colour, for a tile or a group: a small panel in the page's own style */
function ColourPicker({ value, title, onPick, onClose }: {
  value: string; title: string; onPick: (c: string | null) => void; onClose: () => void
}) {
  return (
    <div className="pop colour-pop" onClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.stopPropagation()}>
      <div className="pop-head">{title}</div>
      <div className="colour-grid">
        {COLOURS.map((c) => (
          <button key={c} className={`colour-dot${value === c ? ' on' : ''}`} style={{ ['--c' as string]: c }}
            title={c} onClick={() => { onPick(c); onClose() }}>
            {value === c ? <Icon name="check" size={11} /> : null}
          </button>
        ))}
      </div>
      <div className="pop-row">
        <label className="pop-btn">
          <span className="colour-any" />Any colour
          <input type="color" value={value || '#6ea8fe'} onChange={(e) => onPick(e.target.value)} />
        </label>
        <button className="pop-btn" onClick={() => { onPick(null); onClose() }}>
          <Icon name="x" size={11} /> None
        </button>
      </div>
    </div>
  )
}

/** A question asked in the page's own style, never the browser's */
function Confirm({ title, body, action, onYes, onNo }: {
  title: string; body: string; action: string; onYes: () => void; onNo: () => void
}) {
  return (
    <div className="scrim" onMouseDown={onNo}>
      <div className="modal confirm-modal" onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === 'Escape') onNo() }}>
        <h3>{title}</h3>
        <p>{body}</p>
        <div className="modal-actions">
          <button className="btn" onClick={onNo}>Cancel</button>
          <button className="btn danger-btn" autoFocus onClick={onYes}>{action}</button>
        </div>
      </div>
    </div>
  )
}

export function Launcher({ onImport }: { onImport: () => void }) {
  const [boards, setBoards] = useState<BoardMeta[] | null>(null)
  const [home, setHome] = useState<Home>({ look: { theme: 'midnight', pattern: 'dots' }, groups: [] })
  const [modal, setModal] = useState<{ mode: 'new' | 'rename' | 'new-group' | 'rename-group';
                                      board?: BoardMeta; group?: BoardGroup; placing?: string } | null>(null)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [asking, setAsking] = useState<{ title: string; body: string; action: string; yes: () => void } | null>(null)
  const [, retick] = useState(0)
  const open = useStore((s) => s.open)
  const say = useStore((s) => s.say)
  // which small panel is open: a tile's colour or folder, a group's colour, the look
  const [panel, setPanel] = useState<string | null>(null)
  // the group being looked inside, or the whole shelf
  const [inside, setInside] = useState<string | null>(null)
  const [dropOn, setDropOn] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [b, h] = await Promise.all([api.listBoards(), api.home()])
      setBoards(b); setHome(h)
    } catch (e: any) { say(e.message, true); setBoards([]) }
  }, [say])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const onChanged = () => void refresh()
    window.addEventListener('mb:boards-changed', onChanged)
    return () => window.removeEventListener('mb:boards-changed', onChanged)
  }, [refresh])
  // keeps "3m ago" honest without a reload
  useEffect(() => {
    const t = setInterval(() => retick((n) => n + 1), 30000)
    return () => clearInterval(t)
  }, [])
  // the page's own theme (a board applies its own when it opens)
  useEffect(() => { applyTheme({ theme: home.look.theme }) }, [home.look.theme])

  const group = inside ? home.groups.find((g) => g.id === inside) : undefined
  useEffect(() => { if (inside && !group && boards) setInside(null) }, [inside, group, boards])
  const groupOf = (id: string) => home.groups.find((g) => g.boards.includes(id))
  const byId = new Map((boards ?? []).map((b) => [b.id, b]))
  const shown = (boards ?? []).filter((b) => (group ? group.boards.includes(b.id) : !groupOf(b.id)))

  const submit = async () => {
    if (!modal) return
    try {
      if (modal.mode === 'new') {
        const b = await api.createBoard(title.trim() || 'Untitled board', description.trim())
        if (inside) await api.placeBoard(b.id, inside)
        setModal(null)
        await open(b.id)
      } else if (modal.mode === 'rename' && modal.board) {
        await api.patchBoard(modal.board.id, { title: title.trim() || 'Untitled board', description: description.trim() })
        setModal(null); void refresh()
      } else if (modal.mode === 'new-group') {
        const g = await api.addGroup(title.trim() || 'New group')
        if (modal.placing) await api.placeBoard(modal.placing, g.id)
        setModal(null); void refresh()
      } else if (modal.mode === 'rename-group' && modal.group) {
        await api.updateGroup(modal.group.id, { name: title.trim() || modal.group.name })
        setModal(null); void refresh()
      }
    } catch (e: any) { say(e.message, true) }
  }

  const remove = (b: BoardMeta) => setAsking({
    title: `Delete “${b.title}”?`,
    body: `Its ${b.node_count} cards go with it. This cannot be undone.`,
    action: 'Delete board',
    yes: async () => { try { await api.deleteBoard(b.id); void refresh() } catch (e: any) { say(e.message, true) } },
  })
  const removeGroup = (g: BoardGroup) => setAsking({
    title: `Delete the group “${g.name}”?`,
    body: g.boards.length ? `Its ${g.boards.length} board${g.boards.length === 1 ? '' : 's'} stay, outside any group.`
      : 'It is empty.',
    action: 'Delete group',
    yes: async () => {
      try { await api.deleteGroup(g.id); if (inside === g.id) setInside(null); void refresh() } catch (e: any) { say(e.message, true) }
    },
  })

  /** A tile's colour, kept in the board's settings - merged in, as settings
   *  are saved whole and also hold its theme */
  const paint = async (b: BoardMeta, colour: string | null) => {
    const { tile: _old, ...others } = b.settings ?? {}
    try {
      await api.patchBoard(b.id, { settings: colour ? { ...others, tile: colour } : others })
      void refresh()
    } catch (e: any) { say(e.message, true) }
  }
  const put = async (board: string, gid: string | null) => {
    try { setHome(await api.placeBoard(board, gid)); setPanel(null) } catch (e: any) { say(e.message, true) }
  }
  const look = async (patch: { theme?: string; pattern?: string }) => {
    setHome((h) => ({ ...h, look: { ...h.look, ...patch } }))
    try { await api.setLook(patch) } catch (e: any) { say(e.message, true) }
  }

  const spotlight = (e: React.MouseEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`)
    e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`)
  }

  const tileFor = (b: BoardMeta, i: number) => {
    const tile = b.settings?.tile
    return (
      <button key={b.id} className={`board-card${tile ? ' coloured' : ''}${panel?.endsWith(b.id) ? ' picking' : ''}`}
        style={{ ['--i' as string]: i, ...(tile ? { ['--tile' as string]: tile } : {}) }}
        draggable onDragStart={(e) => { e.dataTransfer.setData('text/mindboard-board', b.id); e.dataTransfer.effectAllowed = 'move' }}
        onMouseMove={spotlight}
        onClick={() => { if (!panel) void open(b.id) }}>
        <span className="card-glow" aria-hidden="true" />
        <span className="card-canvas">
          {b.preview?.nodes?.length
            ? <GraphMini nodes={b.preview.nodes} edges={b.preview.edges} />
            : <span className="card-canvas-empty">empty board</span>}
        </span>
        <span className="card-body">
          <h3>{b.title}</h3>
          <p>{b.description || <span className="faint">No description</span>}</p>
        </span>
        <span className="card-foot">
          <Composition counts={b.counts_by_type ?? {}} />
          <span className="when">{ago(b.updated_at)}</span>
        </span>
        <span className="card-tools" onClick={(e) => e.stopPropagation()}>
          <span className="icon-btn tile-colour-btn" title="Colour" role="button"
            onClick={() => setPanel(panel === `c:${b.id}` ? null : `c:${b.id}`)}>
            <span className="tile-swatch" style={{ background: tile || 'transparent' }} />
          </span>
          <span className="icon-btn" title="Put in a group" role="button"
            onClick={() => setPanel(panel === `g:${b.id}` ? null : `g:${b.id}`)}>
            <Icon name="folder" size={13} />
          </span>
          <a className="icon-btn" href={api.exportUrl(b.id)} title="Export" onClick={(e) => e.stopPropagation()}>
            <Icon name="download" size={13} />
          </a>
          <span className="icon-btn" title="Rename" role="button"
            onClick={() => { setTitle(b.title); setDescription(b.description); setModal({ mode: 'rename', board: b }) }}>
            <Icon name="type" size={13} />
          </span>
          <span className="icon-btn danger" title="Delete" role="button" onClick={() => remove(b)}>
            <Icon name="trash" size={13} />
          </span>
        </span>
        {panel === `c:${b.id}` ? (
          <ColourPicker value={tile ?? ''} title="Tile colour" onPick={(c) => void paint(b, c)} onClose={() => setPanel(null)} />
        ) : null}
        {panel === `g:${b.id}` ? (
          <div className="pop group-pop" onClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.stopPropagation()}>
            <div className="pop-head">Put in a group</div>
            {home.groups.map((g) => (
              <button key={g.id} className={`pop-item${g.boards.includes(b.id) ? ' on' : ''}`} onClick={() => void put(b.id, g.id)}>
                <span className="pop-dot" style={{ background: g.colour || 'var(--mb-accent)' }} />{g.name}
                {g.boards.includes(b.id) ? <Icon name="check" size={12} /> : null}
              </button>
            ))}
            <button className="pop-item" onClick={() => { setPanel(null); setTitle(''); setModal({ mode: 'new-group', placing: b.id }) }}>
              <Icon name="plus" size={12} />New group…
            </button>
            {groupOf(b.id) ? (
              <button className="pop-item quiet" onClick={() => void put(b.id, null)}>
                <Icon name="x" size={12} />Not in a group
              </button>
            ) : null}
          </div>
        ) : null}
      </button>
    )
  }

  const groupCard = (g: BoardGroup, i: number) => {
    const members = g.boards.map((id) => byId.get(id)).filter(Boolean) as BoardMeta[]
    const fan = members.slice(0, 3)
    return (
      <div key={g.id} role="button" tabIndex={0}
        className={`group-card${dropOn === g.id ? ' drop' : ''}${panel === `gc:${g.id}` ? ' picking' : ''}`}
        style={{ ['--i' as string]: i, ['--tile' as string]: g.colour || 'var(--mb-accent)' }}
        onClick={() => { if (!panel) setInside(g.id) }}
        onKeyDown={(e) => { if (e.key === 'Enter') setInside(g.id) }}
        onDragOver={(e) => { if (e.dataTransfer.types.includes('text/mindboard-board')) { e.preventDefault(); setDropOn(g.id) } }}
        onDragLeave={() => setDropOn((d) => (d === g.id ? null : d))}
        onDrop={(e) => { e.preventDefault(); setDropOn(null); const id = e.dataTransfer.getData('text/mindboard-board'); if (id) void put(id, g.id) }}>
        <div className="group-stack">
          {fan.length ? fan.map((m, k) => (
            <span key={m.id} className="group-sheet" style={{ ['--k' as string]: k, ...(m.settings?.tile ? { ['--sheet' as string]: m.settings.tile } : {}) }}>
              {m.preview?.nodes?.length ? <GraphMini nodes={m.preview.nodes} edges={m.preview.edges} /> : null}
            </span>
          )) : <span className="group-empty">Drag boards here</span>}
        </div>
        <div className="group-body">
          <h3><Icon name="folder" size={14} /> {g.name}</h3>
          <span className="group-count">{members.length} board{members.length === 1 ? '' : 's'}</span>
        </div>
        <span className="card-tools" onClick={(e) => e.stopPropagation()}>
          <span className="icon-btn tile-colour-btn" title="Colour" role="button"
            onClick={() => setPanel(panel === `gc:${g.id}` ? null : `gc:${g.id}`)}>
            <span className="tile-swatch" style={{ background: g.colour || 'transparent' }} />
          </span>
          <span className="icon-btn" title="Rename" role="button"
            onClick={() => { setTitle(g.name); setModal({ mode: 'rename-group', group: g }) }}>
            <Icon name="type" size={13} />
          </span>
          <span className="icon-btn danger" title="Delete group" role="button" onClick={() => removeGroup(g)}>
            <Icon name="trash" size={13} />
          </span>
        </span>
        {panel === `gc:${g.id}` ? (
          <ColourPicker value={g.colour} title="Group colour" onClose={() => setPanel(null)}
            onPick={(c) => { void api.updateGroup(g.id, { colour: c ?? '' }).then(() => refresh()) }} />
        ) : null}
      </div>
    )
  }

  return (
    <div className={`launcher pattern-${home.look.pattern}`} onMouseDown={(e) => {
      if (panel && !(e.target as HTMLElement).closest('.pop, .card-tools, .look-btn')) setPanel(null)
    }}>
      <div className="launcher-inner">
        <Constellation />
        <div className="launcher-head">
          <span className="dot" />
          <h1>Mindboard</h1>
        </div>
        <p className="launcher-sub">
          Knowledge is not what you collect, but what you connect.
        </p>

        <div className="launcher-actions">
          <button className="btn primary"
            onClick={() => { setTitle(''); setDescription(''); setModal({ mode: 'new' }) }}>
            <Icon name="plus" size={14} /> New board
          </button>
          <button className="btn" onClick={() => { setTitle(''); setModal({ mode: 'new-group' }) }}
            title="A group gathers boards - AI work, teaching, a project">
            <Icon name="folder" size={14} /> New group
          </button>
          {!!boards?.length && (
            <button className="btn experts-btn" onClick={() => { location.hash = '#/experts' }}
              title="Talk to experts made of your boards">
              <Icon name="ask" size={14} /> Experts
            </button>
          )}
          <button className="btn" onClick={onImport}
            title="Open a .mindboard bundle, or a backup zip to restore every board">
            <Icon name="upload" size={14} /> Import
          </button>
          {!!boards?.length && (
            <button className="btn ghost" onClick={() => void backUp()}
              title="Download every board, media included, as one zip">
              <Icon name="download" size={14} /> Back up all
            </button>
          )}
          <span className="look-wrap">
            <button className="btn ghost look-btn" title="This page's theme and background"
              onClick={() => setPanel(panel === 'look' ? null : 'look')}>
              <Icon name="palette" size={14} /> Look
            </button>
            {panel === 'look' ? (
              <div className="pop look-pop" onMouseDown={(e) => e.stopPropagation()}>
                <div className="pop-head">Theme</div>
                <div className="look-themes">
                  {THEMES.map((t) => {
                    const l = THEME_LOOK[t]
                    return (
                      <button key={t} className={`look-theme${home.look.theme === t ? ' on' : ''}`} title={l.feel}
                        onClick={() => void look({ theme: t })}>
                        <span className="theme-swatch" style={{ background: l.bg }}>
                          <span style={{ background: l.card }} /><span style={{ background: l.accent }} />
                        </span>
                        {t}
                      </button>
                    )
                  })}
                </div>
                <div className="pop-head">Background</div>
                <div className="look-patterns">
                  {PATTERNS.map((p) => (
                    <button key={p.key} className={`look-pattern${home.look.pattern === p.key ? ' on' : ''}`}
                      onClick={() => void look({ pattern: p.key })}>
                      <span className={`pattern-swatch pattern-${p.key}`} />{p.name}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </span>
        </div>

        {group ? (
          <div className="shelf-head" style={{ ['--tile' as string]: group.colour || 'var(--mb-accent)' }}>
            <button className="crumb-back" onClick={() => setInside(null)}>
              <Icon name="up" size={13} /> All boards
            </button>
            <span className="crumb-sep">/</span>
            <span className="crumb-here" style={{ ['--tile' as string]: group.colour || 'var(--mb-accent)' }}>
              <Icon name="folder" size={14} /> {group.name}
            </span>
            <span className="group-count">{shown.length} board{shown.length === 1 ? '' : 's'}</span>
          </div>
        ) : null}

        {boards === null ? null : !group && boards.length === 0 && !home.groups.length ? (
          <div className="empty-state">
            No boards yet. Make one, or import a <code>.mindboard</code> bundle.
          </div>
        ) : (
          <>
            {!group && home.groups.length ? (
              <>
                <div className="shelf-title">Groups</div>
                <div className="board-grid group-grid">{home.groups.map(groupCard)}</div>
                {shown.length ? <div className="shelf-title">Boards</div> : null}
              </>
            ) : null}
            {group && !shown.length ? (
              <div className="empty-state">
                This group is empty. Drag a board onto it on the first page, or use a tile’s folder button.
              </div>
            ) : null}
            <div className="board-grid">{shown.map(tileFor)}</div>
          </>
        )}
      </div>

      {modal && (
        <div className="scrim" onMouseDown={() => setModal(null)}>
          <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
            <h3>{{ new: 'New board', rename: 'Rename board', 'new-group': 'New group', 'rename-group': 'Rename group' }[modal.mode]}</h3>
            <div className="field">
              <label>Name</label>
              <input autoFocus value={title}
                placeholder={modal.mode.endsWith('group') ? 'AI work' : 'Nottingham DT ideas'}
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void submit() }} />
            </div>
            {modal.mode === 'new' || modal.mode === 'rename' ? (
              <div className="field">
                <label>Description</label>
                <textarea rows={2} value={description} placeholder="What this board is for"
                  onChange={(e) => setDescription(e.target.value)} />
              </div>
            ) : null}
            <div className="modal-actions">
              <button className="btn" onClick={() => setModal(null)}>Cancel</button>
              <button className="btn primary" onClick={() => void submit()}>
                {modal.mode.startsWith('new') ? 'Create' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
      {asking ? (
        <Confirm title={asking.title} body={asking.body} action={asking.action}
          onNo={() => setAsking(null)} onYes={() => { asking.yes(); setAsking(null) }} />
      ) : null}
    </div>
  )
}
