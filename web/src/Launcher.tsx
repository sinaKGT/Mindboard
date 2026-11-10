/** The only screen outside the canvas. Each card draws the board's actual
 *  top-level graph, so a shelf of boards reads as a shelf of distinct things. */
import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from './api'
import { backUp } from './backup'
import { Constellation } from './Constellation'
import { Icon } from './icons'
import { GraphMini, ago } from './Mini'
import { useStore } from './store'
import { NODE_COLOURS } from './theme'
import type { BoardMeta } from './types'

/** the colours a tile can take: the card colours, and the Experts' gold */
const TILE_COLOURS = [...NODE_COLOURS, '#e8b864']
type Summary = { summary: string; pending?: boolean; empty?: boolean; installed?: boolean; asked: number }

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

export function Launcher({ onImport }: { onImport: () => void }) {
  const [boards, setBoards] = useState<BoardMeta[] | null>(null)
  const [modal, setModal] = useState<{ mode: 'new' | 'rename'; board?: BoardMeta } | null>(null)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [, retick] = useState(0)
  const open = useStore((s) => s.open)
  const say = useStore((s) => s.say)
  // the tile whose colour is being picked
  const [colouring, setColouring] = useState<string | null>(null)
  // tile summaries (DESIGN.md 54), asked for when the pointer rests on a tile
  const [summaries, setSummaries] = useState<Record<string, Summary>>({})
  const resting = useRef<number>(0)

  const refresh = useCallback(async () => {
    try { setBoards(await api.listBoards()) } catch (e: any) { say(e.message, true); setBoards([]) }
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

  const submit = async () => {
    if (!modal) return
    const name = title.trim() || 'Untitled board'
    try {
      if (modal.mode === 'new') {
        const b = await api.createBoard(name, description.trim())
        setModal(null)
        await open(b.id)
      } else if (modal.board) {
        await api.patchBoard(modal.board.id, { title: name, description: description.trim() })
        setModal(null)
        void refresh()
      }
    } catch (e: any) { say(e.message, true) }
  }

  const remove = async (b: BoardMeta) => {
    if (!confirm(`Delete "${b.title}" and its ${b.node_count} nodes? This cannot be undone.`)) return
    try { await api.deleteBoard(b.id); void refresh() } catch (e: any) { say(e.message, true) }
  }

  /** A tile's colour, kept in the board's settings - merged in, as settings
   *  are saved whole and also hold its theme */
  const paint = async (b: BoardMeta, colour: string | null) => {
    const { tile: _old, ...others } = b.settings ?? {}
    try {
      await api.patchBoard(b.id, { settings: colour ? { ...others, tile: colour } : others })
      void refresh()
    } catch (e: any) { say(e.message, true) }
  }

  /** The summary, asked for once the pointer has rested a moment: kept for
   *  the session, asked again after a while if it was not written yet */
  const rest = (b: BoardMeta) => {
    window.clearTimeout(resting.current)
    const have = summaries[b.id]
    if (have && (!have.pending || Date.now() - have.asked < 30000)) return
    resting.current = window.setTimeout(() => {
      api.boardSummary(b.id)
        .then((s) => setSummaries((m) => ({ ...m, [b.id]: { ...s, asked: Date.now() } })))
        .catch(() => {})
    }, 350)
  }

  const spotlight = (e: React.MouseEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`)
    e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`)
  }

  return (
    <div className="launcher" onMouseDown={(e) => {
      if (colouring && !(e.target as HTMLElement).closest('.tile-colours, .tile-colour-btn')) setColouring(null)
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
        </div>

        {boards === null ? null : boards.length === 0 ? (
          <div className="empty-state">
            No boards yet. Make one, or import a <code>.mindboard</code> bundle.
          </div>
        ) : (
          <div className="board-grid">
            {boards.map((b, i) => {
              const tile = b.settings?.tile
              const s = summaries[b.id]
              return (
              <button key={b.id} className={`board-card${tile ? ' coloured' : ''}${colouring === b.id ? ' picking' : ''}`}
                style={{ ['--i' as string]: i, ...(tile ? { ['--tile' as string]: tile } : {}) }}
                onMouseMove={spotlight} onMouseEnter={() => rest(b)}
                onMouseLeave={() => window.clearTimeout(resting.current)}
                onClick={() => { if (!colouring) void open(b.id) }}>
                <span className="card-glow" aria-hidden="true" />
                <span className="card-canvas">
                  {b.preview?.nodes?.length
                    ? <GraphMini nodes={b.preview.nodes} edges={b.preview.edges} />
                    : <span className="card-canvas-empty">empty board</span>}
                  {s && !s.empty ? (
                    <span className={`card-summary${s.summary ? '' : ' waiting'}`}>
                      {s.summary || (s.installed === false
                        ? 'Set up the AI to have this board summarised.'
                        : 'Not summarised yet - it is written while the AI is running.')}
                    </span>
                  ) : null}
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
                    onClick={() => setColouring(colouring === b.id ? null : b.id)}>
                    <span className="tile-swatch" style={{ background: tile || 'transparent' }} />
                  </span>
                  <a className="icon-btn" href={api.exportUrl(b.id)} title="Export"
                    onClick={(e) => e.stopPropagation()}>
                    <Icon name="download" size={13} />
                  </a>
                  <span className="icon-btn" title="Rename" role="button"
                    onClick={() => { setTitle(b.title); setDescription(b.description); setModal({ mode: 'rename', board: b }) }}>
                    <Icon name="type" size={13} />
                  </span>
                  <span className="icon-btn danger" title="Delete" role="button"
                    onClick={() => void remove(b)}>
                    <Icon name="trash" size={13} />
                  </span>
                </span>
                {colouring === b.id ? (
                  <span className="tile-colours" onClick={(e) => e.stopPropagation()}>
                    {TILE_COLOURS.map((c) => (
                      <span key={c} role="button" className={`tile-pick${tile === c ? ' on' : ''}`}
                        style={{ background: c }} title={c}
                        onClick={() => { void paint(b, c); setColouring(null) }} />
                    ))}
                    <label className="tile-pick custom" title="Any colour">
                      <input type="color" value={tile || '#6ea8fe'}
                        onChange={(e) => void paint(b, e.target.value)} />
                    </label>
                    <span role="button" className="tile-pick none" title="No colour"
                      onClick={() => { void paint(b, null); setColouring(null) }}>
                      <Icon name="x" size={10} />
                    </span>
                  </span>
                ) : null}
              </button>
              )
            })}
          </div>
        )}
      </div>

      {modal && (
        <div className="scrim" onMouseDown={() => setModal(null)}>
          <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
            <h3>{modal.mode === 'new' ? 'New board' : 'Rename board'}</h3>
            <div className="field">
              <label>Name</label>
              <input autoFocus value={title} placeholder="Nottingham DT ideas"
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void submit() }} />
            </div>
            <div className="field">
              <label>Description</label>
              <textarea rows={2} value={description} placeholder="What this board is for"
                onChange={(e) => setDescription(e.target.value)} />
            </div>
            <div className="modal-actions">
              <button className="btn" onClick={() => setModal(null)}>Cancel</button>
              <button className="btn primary" onClick={() => void submit()}>
                {modal.mode === 'new' ? 'Create' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
