/** Ctrl+K. This is what replaces a menu bar: everything the app can do lives
 *  here, and the canvas keeps its chrome-free surface. */
import { useEffect, useMemo, useRef, useState } from 'react'

import { autoLayout, jumpToNode, wrapSelection } from './actions'
import { api } from './api'
import { Icon } from './icons'
import { currentParent, flushNow, useStore } from './store'
import { useUI } from './ui'

interface Cmd { id: string; label: string; hint?: string; group: string; run: () => void }

export function Palette({ onImport }: { onImport: () => void }) {
  const open = useUI((s) => s.paletteOpen)
  const setUI = useUI((s) => s.set)
  const board = useStore((s) => s.board)
  const path = useStore((s) => s.path)
  const [q, setQ] = useState('')
  const [cursor, setCursor] = useState(0)
  const [hits, setHits] = useState<{ id: string; title: string; snippet: string; type: string }[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { if (open) { setQ(''); setCursor(0); setHits([]); setTimeout(() => inputRef.current?.focus(), 10) } }, [open])

  useEffect(() => {
    if (!open || !board || q.trim().length < 2) { setHits([]); return }
    let live = true
    const t = setTimeout(async () => {
      try {
        const r = await api.search(board.id, q.trim())
        if (live) setHits(r.results.slice(0, 6))
      } catch { /* search is a convenience, never an error the user must see */ }
    }, 130)
    return () => { live = false; clearTimeout(t) }
  }, [q, open, board])

  const commands: Cmd[] = useMemo(() => {
    const s = useStore.getState()
    const sel = useUI.getState().selected
    const list: Cmd[] = []
    if (board) {
      list.push(
        { id: 'wrap', label: 'Group selection into a meta-node', hint: 'Ctrl+G', group: 'Canvas',
          run: () => wrapSelection(useUI.getState().selected) },
        { id: 'layout', label: 'Tidy this level', hint: 'Ctrl+L', group: 'Canvas',
          run: () => autoLayout(currentParent(s.path)) },
        { id: 'minimap', label: 'Toggle minimap', group: 'Canvas',
          run: () => setUI('minimap', !useUI.getState().minimap) },
        { id: 'theme', label: 'Theme and colours', group: 'Canvas',
          run: () => setUI('themeOpen', true) },
        { id: 'undo', label: 'Undo', hint: 'Ctrl+Z', group: 'Canvas', run: () => s.undo() },
        { id: 'redo', label: 'Redo', hint: 'Ctrl+Shift+Z', group: 'Canvas', run: () => s.redo() },
        { id: 'export', label: `Export "${board.title}" as .mindboard`, group: 'Board',
          run: () => { flushNow(); setTimeout(() => { location.href = api.exportUrl(board.id) }, 350) } },
        { id: 'switch', label: 'All boards', group: 'Board', run: () => s.close() },
      )
      if (sel.length) {
        list.unshift({ id: 'del', label: `Delete ${sel.length} selected`, group: 'Canvas',
          run: () => s.run([{ kind: 'node.delete', ids: useUI.getState().selected }]) })
      }
      if (path.length) {
        list.unshift({ id: 'up', label: 'Go up a level', hint: 'Esc', group: 'Canvas',
          run: () => s.goTo(s.path.length - 1) })
      }
    }
    list.push({ id: 'import', label: 'Import a .mindboard bundle', group: 'Board', run: onImport })
    return list
  }, [board, path, setUI, onImport])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return commands
    return commands.filter((c) => c.label.toLowerCase().includes(needle))
  }, [q, commands])

  const rows = useMemo(() => [
    ...filtered.map((c) => ({ kind: 'cmd' as const, cmd: c })),
    ...hits.map((h) => ({ kind: 'node' as const, hit: h })),
  ], [filtered, hits])

  if (!open) return null

  const choose = (i: number) => {
    const row = rows[i]
    if (!row) return
    setUI('paletteOpen', false)
    if (row.kind === 'cmd') row.cmd.run()
    else jumpToNode(row.hit.id)
  }

  return (
    <div className="scrim" onMouseDown={() => setUI('paletteOpen', false)}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={inputRef} className="q" value={q} placeholder="Search nodes, or type a command"
          onChange={(e) => { setQ(e.target.value); setCursor(0) }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, rows.length - 1)) }
            if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)) }
            if (e.key === 'Enter') { e.preventDefault(); choose(cursor) }
            if (e.key === 'Escape') setUI('paletteOpen', false)
          }} />
        <ul>
          {rows.length === 0 && <li style={{ color: 'var(--mb-text-faint)' }}>nothing matches</li>}
          {rows.map((row, i) => (
            <li key={row.kind === 'cmd' ? row.cmd.id : row.hit.id}
              aria-selected={i === cursor}
              onMouseEnter={() => setCursor(i)}
              onMouseDown={(e) => { e.preventDefault(); choose(i) }}>
              <Icon name={row.kind === 'cmd' ? 'chevron' : 'search'} size={13} />
              {row.kind === 'cmd' ? (
                <>
                  <span className="lead">{row.cmd.label}</span>
                  {row.cmd.hint && <kbd>{row.cmd.hint}</kbd>}
                  <span className="sub">{row.cmd.group}</span>
                </>
              ) : (
                <>
                  <span className="lead">{row.hit.title || 'Untitled'}</span>
                  <span className="sub">{row.hit.snippet || row.hit.type}</span>
                </>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
