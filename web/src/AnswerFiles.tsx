/** The PDFs an answer touches, offered to read more (DESIGN.md 48).
 *
 *  A PDF on a board is read once in the background, so a question finds it by
 *  what it covers. Under the answer, the PDFs it cites - and those among the
 *  very best matches even if it did not cite them - are offered: "Read more in
 *  your files". Open hands the file to your own PDF reader, as a click on its
 *  card does; "Show on its board" goes to the card.
 */
import { useState } from 'react'

import { api } from './api'

export interface Filed { card: string; board: string; sha: string; name: string }

/** the matches looked at for files not cited, and how many files are offered */
const TOP = 6
const MOST = 3

/** cited files first, in the order cited, then files among the top matches */
export function filesToOffer(cited: string[], ranked: string[], fileOf: (card: string) => Filed | null): Filed[] {
  const out: Filed[] = []
  for (const id of [...cited, ...ranked.slice(0, TOP)]) {
    const f = fileOf(id)
    if (f && !out.some((o) => o.sha === f.sha)) out.push(f)
  }
  return out.slice(0, MOST)
}

export function AnswerFiles({ files, onShow, className = '' }: {
  files: Filed[]; onShow?: (f: Filed) => void; className?: string
}) {
  const [said, setSaid] = useState('')
  if (!files.length) return null
  const open = (f: Filed) => {
    setSaid('')
    api.openAsset(f.board, f.sha).then(() => setSaid(`Opened ${f.name} in your PDF reader.`),
      (e: Error) => setSaid(e.message || 'Could not open it.'))
  }
  return (
    <div className={`answer-files ${className}`}>
      <div className="answer-files-lead">Read more in your files</div>
      {files.map((f) => (
        <div key={f.card} className="answer-file">
          <span className="answer-file-badge">PDF</span>
          <span className="answer-file-name" title={f.name}>{f.name}</span>
          <button className="answer-file-btn" onClick={() => open(f)}>Open</button>
          {onShow ? <button className="answer-file-btn" onClick={() => onShow(f)}>Show on its board</button> : null}
        </div>
      ))}
      {said ? <div className="answer-files-said">{said}</div> : null}
    </div>
  )
}
