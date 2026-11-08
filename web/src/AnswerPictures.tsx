/** The pictures an answer cites, shown with it (DESIGN.md 48).
 *
 *  An answer that cites a picture card used to show only the card's title, as
 *  a chip; seeing the picture meant going to its board - for an expert, leaving
 *  the conversation. Now each cited picture is a thumbnail under the answer, in
 *  the order it was cited. A click opens it full size over the page - the same
 *  viewer as the canvas, zoom and all - with "Show on its board" for when you
 *  want its place in the map. Nothing is fetched until it is shown, and a
 *  thumbnail is made once on the server and kept.
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'

import { api } from './api'
import { Open } from './Viewer'

export interface Pictured { card: string; board: string; sha: string; title: string }

/** at most this many under one answer - beyond that it is a gallery, not an answer */
const MOST = 6

export function AnswerPictures({ pictures, onShow, className = '' }: {
  pictures: Pictured[]
  /** go to the card on its board */
  onShow?: (p: Pictured) => void
  className?: string
}) {
  const [open, setOpen] = useState<{ p: Pictured; at: number } | null>(null)
  /** pictures whose file is not on this computer - shown as such, not as a broken image */
  const [missing, setMissing] = useState<Set<string>>(new Set())
  const shown = pictures.filter((p, i) => pictures.findIndex((q) => q.sha === p.sha) === i).slice(0, MOST)
  if (!shown.length) return null
  return (
    <>
      <div className={`answer-pictures ${className}`}>
        {shown.map((p) => missing.has(p.sha) ? (
          <button key={p.card} className="answer-picture is-missing" disabled={!onShow}
            title="This picture's file is not on this computer" onClick={() => onShow?.(p)}>
            <span className="answer-picture-gone">picture file missing</span>
            <span className="answer-picture-title">{p.title}</span>
          </button>
        ) : (
          <button key={p.card} className="answer-picture" title={`${p.title} - open it`}
            onClick={() => setOpen({ p, at: Date.now() })}>
            <img src={api.thumbUrl(p.board, p.sha)} alt={p.title} loading="lazy" draggable={false}
              // no thumbnail (an unusual format): the picture itself, shrunk by the
              // page; and if that is not here either, say so
              onError={(e) => {
                const full = api.assetUrl(p.board, p.sha)
                if (e.currentTarget.src !== new URL(full, location.href).href) e.currentTarget.src = full
                else setMissing((m) => new Set(m).add(p.sha))
              }} />
            <span className="answer-picture-title">{p.title}</span>
          </button>
        ))}
      </div>
      {open ? createPortal(
        <Open key={open.p.card} src={api.assetUrl(open.p.board, open.p.sha)} title={open.p.title}
          alt={open.p.title} openedAt={open.at} onClose={() => setOpen(null)}
          action={onShow ? { label: 'Show on its board', run: () => onShow(open.p) } : undefined} />,
        document.body) : null}
    </>
  )
}
