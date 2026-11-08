/** Add to board, from an expert's answer (DESIGN.md 44).
 *
 *  As on a board's own conversation (DESIGN 34): the model drafts a card from
 *  the answer - the knowledge, not the chat - and proposes the card it belongs
 *  with. An expert draws on several boards, so you also choose which one it
 *  goes on: the board the answer cited most, unless you pick another. Nothing
 *  changes until Add; then the card and its link go in through the op-log,
 *  marked as made by AI, beside the card it belongs with, and the background
 *  indexer makes it searchable.
 */
import { useEffect, useState } from 'react'

import { api } from '../api'
import { newEdge, newNode, ulid, type MEdge, type Op } from '../types'

const KIND_WORDS: Record<string, string> = {
  relates: 'relates to', supports: 'supports', 'example-of': 'is an example of',
  'part-of': 'is part of', causes: 'causes', contradicts: 'contradicts',
}

interface Draft { title: string; text: string; link_to: string; kind: string }

export function AddCard({ boards, question, answer, where, onDone, onOpen }: {
  boards: { id: string; title: string }[]
  question: string
  answer: string
  where: Record<string, { board: string; title: string }>
  onDone: () => void
  onOpen: (board: string, card: string) => void
}) {
  // the cards the answer cited, by board
  const cited = [...new Set([...answer.matchAll(/\[\[([0-9A-Za-z]{26})\]\]/g)].map((m) => m[1]))].filter((id) => where[id])
  const count = (b: string) => cited.filter((id) => where[id].board === b).length
  const [board, setBoard] = useState(() => [...boards].sort((a, b) => count(b.id) - count(a.id))[0]?.id ?? '')
  const onBoard = cited.filter((id) => where[id].board === board)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [error, setError] = useState('')
  const [added, setAdded] = useState<{ board: string; card: string } | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!board) return
    api.askDraft(board, { question, answer, cited: onBoard })
      .then(setDraft).catch((e) => setError(e.message || 'could not draft the card'))
    // drafted once, when opened; choosing another board only changes where it goes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const pickBoard = (b: string) => {
    setBoard(b)
    const first = cited.find((id) => where[id].board === b) ?? ''
    if (draft) setDraft({ ...draft, link_to: first })
  }

  const add = async () => {
    if (!draft || !draft.title.trim() || saving) return
    setSaving(true); setError('')
    try {
      const g = await api.graph(board)
      const target = draft.link_to ? g.nodes.find((n) => n.id === draft.link_to) : undefined
      // beside the card it belongs with, in its group; else to the right of the top level
      const top = g.nodes.filter((n) => !n.parent)
      const right = top.reduce((x, n) => Math.max(x, n.pos[0] + (n.size[0] ?? 240)), 0)
      const pos: [number, number] = target
        ? [target.pos[0] + (target.size[0] ?? 240) + 90, target.pos[1] + 30]
        : [top.length ? right + 120 : 0, top.length ? Math.min(...top.map((n) => n.pos[1])) : 0]
      const card = newNode({ pos, parent: target ? target.parent : null, title: draft.title.trim(),
                             content: { text: draft.text.trim() }, provenance: 'ai' })
      const ops: Op[] = [{ kind: 'node.create', node: card }]
      if (target) ops.push({ kind: 'edge.create',
        edge: newEdge(card.id, target.id, { kind: draft.kind as MEdge['kind'], provenance: 'ai' }) })
      const r = await api.ops(board, ops.map((op) => ({ opId: ulid(), op, origin: 'user' as const })))
      if (r.rejected.length) throw new Error(r.rejected[0].error)
      setAdded({ board, card: card.id })
    } catch (e: any) {
      setError(e.message || 'could not add the card')
    } finally { setSaving(false) }
  }

  if (added) {
    const name = boards.find((b) => b.id === added.board)?.title ?? 'its board'
    return (
      <div className="xp-added">Added to {name}.
        <button onClick={() => onOpen(added.board, added.card)}>Open it</button>
        <button onClick={onDone}>Close</button>
      </div>
    )
  }
  if (error && !draft) return <div className="xp-error">{error}</div>
  if (!draft) return <div className="xp-note">Drafting a card from this answer…</div>
  return (
    <div className="xp-draft">
      <input value={draft.title} placeholder="Title" onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
      <textarea rows={3} value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
      <div className="xp-draft-where">
        <span>On</span>
        <select value={board} onChange={(e) => pickBoard(e.target.value)}>
          {boards.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
        </select>
        {onBoard.length ? (
          <>
            <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
              {Object.entries(KIND_WORDS).map(([k, w]) => <option key={k} value={k}>{w}</option>)}
            </select>
            <select value={draft.link_to} onChange={(e) => setDraft({ ...draft, link_to: e.target.value })}>
              {onBoard.map((id) => <option key={id} value={id}>{where[id].title || 'Untitled'}</option>)}
              <option value="">no card - on its own</option>
            </select>
          </>
        ) : <span>on its own</span>}
      </div>
      {error ? <div className="xp-error">{error}</div> : null}
      <div className="xp-draft-actions">
        <button onClick={onDone}>Cancel</button>
        <button className="xp-primary" onClick={() => void add()} disabled={!draft.title.trim() || saving}>
          {saving ? 'Adding…' : 'Add'}</button>
      </div>
    </div>
  )
}
