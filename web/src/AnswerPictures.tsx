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

/** `likely`: not cited, shown as one of the best matches - and hidden if its file is missing */
export interface Pictured { card: string; board: string; sha: string; title: string; likely?: boolean }

/** the best matches looked at for a picture the answer used but did not cite */
const TOP = 3

/** Cited pictures first, in the order cited, then pictures among the question's
 *  very best matches: the model reads those pictures themselves, and an answer
 *  taken from one does not always cite it (DESIGN.md 51). */
export function picturesToShow(cited: string[], ranked: string[],
                               pictureOf: (card: string) => Pictured | null): Pictured[] {
  const out: Pictured[] = []
  const sure = new Set(cited)
  for (const id of [...cited, ...ranked.slice(0, TOP)]) {
    const p = pictureOf(id)
    if (p && !out.some((o) => o.sha === p.sha)) out.push(sure.has(id) ? p : { ...p, likely: true })
  }
  return out
}

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
  const shown = pictures.filter((p, i) => pictures.findIndex((q) => q.sha === p.sha) === i)
    .filter((p) => !(p.likely && missing.has(p.sha))).slice(0, MOST)
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
