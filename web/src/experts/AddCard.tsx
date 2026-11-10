/** Add to board, from an expert's answer (DESIGN.md 44, 53, 55).
 *
 *  As on a board's own conversation: the model writes what this answer adds
 *  that the conversation's cards do not already say, picks the card it belongs
 *  with among every card the conversation brought up - on any of the expert's
 *  boards - and how they relate: a kind, and a label of its own where a kind is
 *  too coarse. The card goes on that card's board at once, through the op-log,
 *  marked as made by AI; then you are taken to it, and the canvas asks "Right
 *  place?" beside it (PlacedCheck): keep it, or link it to another card
 *  yourself. The conversation is kept for when you come back.
 *
 *  With no card to link to, it goes on the board the answer cited most - or
 *  the expert's first - to the right of what is there.
 */
import { useEffect, useState } from 'react'

import { api } from '../api'
import { newEdge, newNode, ulid, type MEdge, type Op } from '../types'
import { useUI } from '../ui'

const KIND_WORDS: Record<string, string> = {
  relates: 'relates to', supports: 'supports', 'example-of': 'is an example of',
  'part-of': 'is part of', causes: 'causes', contradicts: 'contradicts',
}

type Where = Record<string, { board: string; title: string }>
interface ConvoTurn { role: 'user' | 'expert'; text: string; where?: Where; cited?: string[]; status?: string; error?: string }

const CITE = /\[\[([0-9A-Za-z]{26})\]\]/g

export function AddCard({ expertId, boards, turns, at, onDone, onOpen }: {
  expertId: string
  boards: { id: string; title: string }[]
  /** the whole conversation, and the index of the answer being added */
  turns: ConvoTurn[]
  at: number
  onDone: () => void
  onOpen: (board: string, card: string) => void
}) {
  const [error, setError] = useState('')

  useEffect(() => {
    const turn = turns[at]
    const question = turns[at - 1]?.text ?? ''
    const cited = [...new Set([...turn.text.matchAll(CITE)].map((m) => m[1]))]
    // every card this conversation brought up, the latest first: what answers
    // cited, then what their searches found
    const seen = turns.slice(0, at + 1).reverse()
      .flatMap((t) => [...[...t.text.matchAll(CITE)].map((m) => m[1]), ...Object.keys(t.where ?? {})])
    const candidates = [...new Set(seen)].slice(0, 60)
    const history = turns.slice(0, Math.max(at - 1, 0))
      .filter((t) => !t.error && t.text.trim() && (t.role === 'user' || t.status === 'done'))
      .slice(-6).map((t) => ({ role: t.role === 'user' ? 'user' : 'assistant', text: t.text }))
    // where it goes when it links to nothing: the board the answer cited most
    const where: Where = Object.assign({}, ...turns.map((t) => t.where ?? {}))
    const count = (b: string) => cited.filter((id) => where[id]?.board === b).length
    const fallbackBoard = [...boards].sort((a, b) => count(b.id) - count(a.id))[0]?.id ?? ''

    api.expertDraft(expertId, { question, answer: turn.text, cited, candidates, history })
      .then(async (draft) => {
        const board = draft.board || fallbackBoard
        if (!board) throw new Error('this expert has no board to add to')
        const g = await api.graph(board)
        const target = draft.link_to ? g.nodes.find((n) => n.id === draft.link_to) : undefined
        // beside the card it belongs with, in its group; else to the right of the top level
        const tops = g.nodes.filter((n) => !n.parent)
        const right = tops.reduce((x, n) => Math.max(x, n.pos[0] + (n.size[0] ?? 240)), 0)
        const pos: [number, number] = target
          ? [target.pos[0] + (target.size[0] ?? 240) + 90, target.pos[1] + 30]
          : [tops.length ? right + 120 : 0, tops.length ? Math.min(...tops.map((n) => n.pos[1])) : 0]
        const card = newNode({ pos, parent: target ? target.parent : null,
                               title: draft.title.trim() || question.slice(0, 70),
                               content: { text: draft.text.trim() }, provenance: 'ai' })
        const ops: Op[] = [{ kind: 'node.create', node: card }]
        let edgeId: string | null = null
        if (target) {
          const edge = newEdge(card.id, target.id, { kind: draft.kind as MEdge['kind'], label: draft.label,
                                                    provenance: 'ai' })
          edgeId = edge.id
          ops.push({ kind: 'edge.create', edge })
        }
        const r = await api.ops(board, ops.map((op) => ({ opId: ulid(), op, origin: 'user' as const })))
        if (r.rejected.length) throw new Error(r.rejected[0].error)
        // the canvas asks "Right place?" once it opens on the card
        useUI.getState().set('placed', {
          cardId: card.id, edgeId, targetId: target?.id ?? null, why: draft.why,
          relation: draft.label || KIND_WORDS[draft.kind] || 'relates to' })
        onDone()
        onOpen(board, card.id)
      })
      .catch((e) => setError(e.message || 'could not add the card'))
    // drafted once, when asked for
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (error) return <div className="xp-error">{error}</div>
  return <div className="xp-note">Finding what is new here, and where it belongs on its boards…</div>
}
