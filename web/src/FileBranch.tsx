/** Conversations with one file, in the chat (DESIGN.md 64).
 *
 *  Each file asked about is a branch beside the board's conversation: a tab
 *  above the conversation, its own questions and answers. While the file is
 *  prepared - read, its figures described, its passages indexed - the strip
 *  says how far it has got; then every question searches that file only.
 *  Under each answer, the pages it was given, a click away.
 */
import { useState } from 'react'

import { addFile } from './actions'
import { api } from './api'
import { useAsk, type FileBranch, type Turn } from './askStore'
import { Icon } from './icons'
import { useStore } from './store'

const STAGES: Record<string, string> = {
  reading: 'Reading the file',
  pictures: 'Looking at its figures',
  meaning: 'Learning what each passage means',
  ready: 'Ready',
}

export const extOf = (name: string) => (name.match(/\.([a-z0-9]+)$/i)?.[1] ?? 'file').toUpperCase()

/** the board's conversation and each file's, as tabs */
export function FileTabs() {
  const branches = useAsk((s) => s.branches)
  const active = useAsk((s) => s.active)
  const busy = useAsk((s) => s.busy)
  const ask = useAsk.getState()
  if (!branches.length) return null
  return (
    <div className="file-tabs" role="tablist" aria-label="Conversations">
      <button role="tab" aria-selected={active === null} className={`file-tab ${active === null ? 'on' : ''}`}
        disabled={busy && active !== null} onClick={() => ask.show(null)} title="The board's conversation">
        <Icon name="grid" size={11} /> Board
      </button>
      {branches.map((b) => (
        <span key={b.id} className={`file-tab ${active === b.id ? 'on' : ''} ${b.status}`}>
          <button role="tab" aria-selected={active === b.id} disabled={busy && active !== b.id}
            onClick={() => ask.show(b.id)} title={`Ask ${b.name}`}>
            <span className="file-tab-badge">{extOf(b.name)}</span>
            <span className="file-tab-name">{b.name}</span>
            {b.status === 'preparing' ? <span className="file-tab-dot" aria-label="preparing" /> : null}
          </button>
          <button className="file-tab-x" title="Close file"
            aria-label={`Close ${b.name}`} onClick={() => ask.closeBranch(b.id)}>
            <Icon name="x" size={9} />
          </button>
        </span>
      ))}
    </div>
  )
}

/** what the shown file is doing: preparing, ready, or ended */
export function FileStrip({ branch }: { branch: FileBranch }) {
  const boardId = useStore((s) => s.boardId)
  const [adding, setAdding] = useState(false)
  const ask = useAsk.getState()
  const pct = branch.total ? Math.round(((branch.done ?? 0) / branch.total) * 100) : 0

  const toBoard = async () => {
    if (!boardId) return
    setAdding(true)
    try {
      // the file as it was added in the chat, onto the board as a file card
      const res = await fetch(api.fileUrl(branch.id), { credentials: 'same-origin' })
      if (!res.ok) throw new Error('the file is not here any more')
      const file = new File([await res.blob()], branch.name, { type: res.headers.get('content-type') || '' })
      const card = await addFile(file, [0, 0], null)
      if (card) useAsk.setState((st) => ({
        branches: st.branches.map((b) => (b.id === branch.id ? { ...b, card, board: boardId } : b)) }))
    } catch (err: any) {
      useStore.getState().say(err.message || 'could not add the file', true)
    } finally { setAdding(false) }
  }

  if (branch.status === 'preparing') {
    const stage = STAGES[branch.stage ?? 'reading'] ?? 'Preparing'
    // no step forward for two minutes: say so, rather than a bar that never moves
    const slow = branch.moved && Date.now() - branch.moved > 120_000
    return (
      <div className="file-strip preparing">
        <div className="file-strip-line">
          <b>Preparing {branch.name}</b> — {stage.toLowerCase()}
          {branch.total ? ` · ${branch.done ?? 0} of ${branch.total}` : '…'}
        </div>
        <div className="file-progress"><i style={{ width: `${branch.total ? Math.max(pct, 4) : 12}%` }}
          className={branch.total ? '' : 'indeterminate'} /></div>
        {slow ? (
          <div className="file-strip-note slow">
            This is taking long — the model may be busy with another question, or loading. It will
            go on by itself; or <button className="linkish" onClick={() => ask.closeBranch(branch.id)}>stop and close it</button>.
          </div>
        ) : (
          <div className="file-strip-note">Only its best-matching passages will be sent with each question — never the whole file.</div>
        )}
      </div>
    )
  }
  if (branch.status === 'failed' || branch.status === 'gone') {
    return (
      <div className="file-strip failed">
        <div className="file-strip-line">
          {branch.status === 'gone'
            ? <>This conversation's preparation has ended — Mindboard was restarted, or it sat unused.
                {branch.board ? '' : ' Add the file again to keep asking it.'}</>
            : <><b>{branch.name}</b> could not be prepared: {branch.error}</>}
        </div>
        <div className="file-strip-actions">
          {branch.status === 'gone' && branch.board && branch.sha ? (
            <button className="file-btn" onClick={() => {
              ask.closeBranch(branch.id)
              void ask.openFile({ board: branch.board!, sha: branch.sha!, card: branch.card, name: branch.name })
            }}>Prepare again</button>
          ) : null}
          <button className="file-btn" onClick={() => ask.closeBranch(branch.id)}>Close</button>
        </div>
      </div>
    )
  }
  return (
    <div className="file-strip ready">
      <div className="file-strip-line">
        Asking <b>{branch.name}</b> — only this file is searched.
        <span className="file-strip-size">
          {branch.pages ? ` ${branch.pages} pages ·` : ''} {branch.passages ?? 0} passages
          {branch.sheets?.length ? ` · ${branch.sheets.map((x) => `${x.name} (${x.rows.toLocaleString()} rows)`).join(', ')} — totals and counts are calculated over every row` : ''}
        </span>
      </div>
      {branch.note ? <div className="file-strip-note">{branch.note}</div> : null}
      <div className="file-strip-actions">
        <a className="file-btn" href={api.fileUrl(branch.id)} target="_blank" rel="noreferrer">Open</a>
        {!branch.card && boardId ? (
          <button className="file-btn" disabled={adding} onClick={() => void toBoard()}>
            {adding ? 'Adding…' : 'Add file to board'}
          </button>
        ) : null}
      </div>
    </div>
  )
}

/** a page cited "(p. 12)": opens the file at that page */
export function pageLink(branch: FileBranch | undefined) {
  if (!branch || branch.kind !== 'pdf' || branch.status !== 'ready') return undefined
  return (page: number, label: string) => (
    <a className="page-cite" href={api.fileUrl(branch.id, page)} target="_blank" rel="noreferrer"
      title={`Open page ${page}`}>{label}</a>
  )
}

/** what the model chose to calculate over whole sheets, and what could not be (DESIGN.md 65) */
export function Calculated({ turn }: { turn: Turn }) {
  const t = turn.tools
  if (!t || (!t.calls.length && !t.trouble)) return null
  return (
    <div className="calculated">
      <div className="calculated-lead">
        <Icon name="numbers" size={11} /> Calculated over the whole sheet{t.why ? ` — ${t.why}` : ''}
      </div>
      {t.calls.map((c, i) => (
        <div key={i} className={`calculated-row ${c.error ? 'failed' : ''}`}>
          {c.error ? `Could not calculate ${c.label || c.tool}: ${c.error}` : c.said.split('\n').slice(0, 8).join('\n')}
        </div>
      ))}
      {t.trouble && t.trouble !== 'stopped' ? (
        <div className="calculated-row failed">No calculation was made: {t.trouble}. The answer comes from the matching rows only.</div>
      ) : null}
    </div>
  )
}

/** the passages an answer was given: pages as small pictures, or where in the file */
export function PagesUsed({ turn, branch }: { turn: Turn; branch: FileBranch | undefined }) {
  if (!branch || !turn.passages?.length || turn.status !== 'done') return null
  const pages = [...new Set(turn.passages.map((p) => p.page).filter((p): p is number => p !== null))]
  const places = [...new Set(turn.passages.filter((p) => p.page === null).map((p) => p.where))]
  const live = branch.status === 'ready'
  return (
    <div className="pages-used">
      <div className="pages-used-lead">
        From {branch.name}: {turn.passages.length} passage{turn.passages.length === 1 ? '' : 's'}
        {turn.total ? ` of ${turn.total}` : ''}{turn.how ? `, found by ${turn.how}` : ''}
      </div>
      {pages.length && branch.kind === 'pdf' && live ? (
        <div className="pages-used-row">
          {pages.slice(0, 8).map((p) => {
            const pictured = turn.passages!.some((x) => x.page === p && x.pictured)
            return (
              <a key={p} className={`page-thumb ${pictured ? 'pictured' : ''}`} href={api.fileUrl(branch.id, p)}
                target="_blank" rel="noreferrer"
                title={`Page ${p}`}>
                <img src={api.filePageUrl(branch.id, p)} alt={`Page ${p}`} loading="lazy" />
                <span>p. {p}</span>
              </a>
            )
          })}
        </div>
      ) : pages.length ? (
        <div className="pages-used-row">{pages.map((p) => <span key={p} className="place-chip">p. {p}</span>)}</div>
      ) : null}
      {places.length ? (
        <div className="pages-used-row">{places.slice(0, 10).map((w) => <span key={w} className="place-chip">{w}</span>)}</div>
      ) : null}
    </div>
  )
}
