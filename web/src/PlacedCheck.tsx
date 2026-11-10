/** "Right place?" beside a card Add to board just placed (DESIGN.md 53).
 *
 *  The card goes on the board at once, linked to the card the model chose,
 *  and this asks the person whether that is where it belongs. ✓ keeps it as
 *  it is. ✗ takes the suggested link away - the card stays - and starts a
 *  connection from it, so the next click on a card links it there instead
 *  (Esc leaves it on its own). Drawn in board coordinates, so it stays beside
 *  the card as the board is panned or zoomed.
 */
import { ViewportPortal } from '@xyflow/react'
import { useEffect, useRef } from 'react'

import { useAsk } from './askStore'
import { Icon } from './icons'
import { useStore } from './store'
import { useUI } from './ui'

export function PlacedCheck() {
  const placed = useUI((s) => s.placed)
  const card = useStore((s) => (placed ? s.nodes[placed.cardId] : undefined))
  const target = useStore((s) => (placed?.targetId ? s.nodes[placed.targetId] : undefined))
  const edgeThere = useStore((s) => (placed?.edgeId ? !!s.edges[placed.edgeId] : false))
  const path = useStore((s) => s.path)
  const setUI = useUI((s) => s.set)
  // the board has to be seen to answer: its own chat steps aside, unless it
  // sits along the left edge - as when arriving from the Experts (DESIGN.md 55)
  // once the board's chat has loaded its saved state, and once per card - a
  // chat you open again yourself stays open
  const askBoard = useAsk((s) => s.boardId)
  const boardId = useStore((s) => s.boardId)
  const steppedAside = useRef<string | null>(null)
  useEffect(() => {
    if (!placed || !card || !boardId || askBoard !== boardId || steppedAside.current === placed.cardId) return
    steppedAside.current = placed.cardId
    const ask = useAsk.getState()
    if (ask.open && (ask.expanded || ask.side !== 'left')) ask.setOpen(false)
  }, [placed?.cardId, !!card, askBoard, boardId])  // eslint-disable-line react-hooks/exhaustive-deps
  if (!placed || !card) return null
  // only on the level the card is on
  if ((card.parent ?? null) !== (path[path.length - 1] ?? null)) return null

  const keep = () => {
    setUI('placed', null)
    useStore.getState().say('Kept where it is')
  }
  const move = () => {
    const store = useStore.getState()
    if (placed.edgeId && store.edges[placed.edgeId]) store.run([{ kind: 'edge.delete', ids: [placed.edgeId] }])
    setUI('placed', null)
    setUI('linking', { sourceId: placed.cardId })
    store.say('Click the card it belongs with - or Esc to leave it on its own')
  }

  const linked = !!(target && edgeThere)
  return (
    <ViewportPortal>
      <div className="placed-check nodrag nopan"
        style={{ transform: `translate(${card.pos[0]}px, ${card.pos[1] - 54}px)` }}
        onMouseDown={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
        <div className="placed-words">
          <span className="placed-q">Right place?</span>
          <span className="placed-where" title={placed.why || undefined}>
            {linked ? <>{placed.relation} <b>{target!.title || 'Untitled'}</b></> : 'on its own, not linked'}
          </span>
        </div>
        <button className="placed-yes" title="Yes - keep it here" onClick={keep}>
          <Icon name="check" size={14} />
        </button>
        <button className="placed-no" title="No - connect it to another card yourself" onClick={move}>
          <Icon name="x" size={14} />
        </button>
      </div>
    </ViewportPortal>
  )
}
