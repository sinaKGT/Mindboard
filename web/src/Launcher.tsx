/** The only screen outside the canvas. It exists because import lives here and
 *  because a board's identity - its name and description - is set here. */
import { useCallback, useEffect, useState } from 'react'

import { api } from './api'
import { Icon } from './icons'
import { useStore } from './store'
import type { BoardMeta } from './types'

const fmt = (iso: string) => {
  try {
    return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
  } catch { return iso.slice(0, 10) }
}

export function Launcher({ onImport }: { onImport: () => void }) {
  const [boards, setBoards] = useState<BoardMeta[] | null>(null)
  const [modal, setModal] = useState<{ mode: 'new' | 'rename'; board?: BoardMeta } | null>(null)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const open = useStore((s) => s.open)
  const say = useStore((s) => s.say)

  const refresh = useCallback(async () => {
    try { setBoards(await api.listBoards()) } catch (e: any) { say(e.message, true); setBoards([]) }
  }, [say])

  useEffect(() => { void refresh() }, [refresh])

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

  return (
    <div className="launcher">
      <div className="launcher-inner">
        <div className="launcher-head">
          <span className="dot" />
          <h1>Mindboard</h1>
        </div>
        <p className="launcher-sub">
          Everything stays on this machine.
          {boards?.length ? ` ${boards.length} board${boards.length === 1 ? '' : 's'}.` : ''}
        </p>

        <div className="launcher-actions">
          <button className="btn primary"
            onClick={() => { setTitle(''); setDescription(''); setModal({ mode: 'new' }) }}>
            <Icon name="plus" size={14} /> New board
          </button>
          <button className="btn" onClick={onImport}>
            <Icon name="upload" size={14} /> Import
          </button>
        </div>

        {boards === null ? null : boards.length === 0 ? (
          <div className="empty-state">
            No boards yet. Make one, or import a <code>.mindboard</code> bundle.
          </div>
        ) : (
          <div className="board-grid">
            {boards.map((b) => (
              <button key={b.id} className="board-card" onClick={() => void open(b.id)}>
                <h3>{b.title}</h3>
                <p>{b.description || <span style={{ color: 'var(--mb-text-faint)' }}>No description</span>}</p>
                <footer>
                  <span>{fmt(b.created_at)}</span>
                  <span>{b.node_count} {b.node_count === 1 ? 'node' : 'nodes'}</span>
                </footer>
                <span className="card-tools" onClick={(e) => e.stopPropagation()}>
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
              </button>
            ))}
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
