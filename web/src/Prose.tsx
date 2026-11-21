/** An answer, laid out (DESIGN.md 45).
 *
 *  The model writes light markdown - headings, bullet and numbered lists
 *  (often nested), bold, italics, the odd table - and [[card]] citations.
 *  Shown as it came, that was a wall of `###` and `*`. Here it is read into
 *  blocks first and then drawn: headings as headings, lists with their real
 *  depth, citations as chips through `cite`. Nothing in an answer is ever
 *  treated as HTML; everything is text in React elements - formulas apart,
 *  drawn by KaTeX from its own reading of them (Math.tsx, DESIGN.md 61).
 */
import { Fragment, type ReactNode } from 'react'
import { MATH_SOURCE, Tex, texOf } from './Math'

export type Block =
  | { k: 'h'; level: number; text: string }
  | { k: 'p'; text: string }
  | { k: 'li'; depth: number; mark: string | null; text: string }
  | { k: 'quote'; text: string }
  | { k: 'code'; text: string }
  | { k: 'hr' }
  | { k: 'table'; rows: string[][] }

const TABLE_ROW = /^\s*\|.*\|\s*$/

/** A table row's cells. A "|" inside a formula ($P(Y | C)$), inside `code`,
 *  or written "\|" is part of the cell, not a border: split on every "|",
 *  a row of formulas fell apart into stray columns of raw LaTeX (DESIGN.md 90). */
const KEEP_WHOLE = new RegExp(`${MATH_SOURCE}|` + '`[^`\\n]+`', 'g')
export function splitCells(row: string): string[] {
  const kept: string[] = []
  const masked = row
    .replace(KEEP_WHOLE, (m) => { kept.push(m); return `\u0000${kept.length - 1}\u0000` })
    .replace(/\\\|/g, () => { kept.push('|'); return `\u0000${kept.length - 1}\u0000` })
  return masked.split('|').map((c) => c.replace(/\u0000(\d+)\u0000/g, (_, n) => kept[Number(n)]).trim())
}

/** the answer as blocks */
export function blocks(src: string, breaks = false): Block[] {
  const out: Block[] = []
  const lines = src.replace(/\r/g, '').replace(/\t/g, '    ').split('\n')
  let para: string[] = []
  let indents: number[] = []                     // the open list's indentation, outermost first
  // a card keeps its line breaks (breaks); an answer's lines run on as a paragraph
  const flush = () => { if (para.length) { out.push({ k: 'p', text: para.join(breaks ? '\n' : ' ') }); para = [] } }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    let m: RegExpMatchArray | null
    if (/^\s*```/.test(line)) {
      flush(); indents = []
      const code: string[] = []
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i])
      out.push({ k: 'code', text: code.join('\n') })
      continue
    }
    if (!line.trim()) { flush(); continue }
    if ((m = line.match(/^\s{0,3}(#{1,6})\s+(.*?)[\s#]*$/))) {
      flush(); indents = []; out.push({ k: 'h', level: m[1].length, text: m[2] }); continue
    }
    if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); indents = []; out.push({ k: 'hr' }); continue }
    if ((m = line.match(/^(\s*)(?:([-*+•])|(\d{1,3})[.)])\s+(.*)$/))) {
      flush()
      // depth from indentation relative to the list's own items, however many spaces it uses
      const indent = m[1].length
      while (indents.length && indent < indents[indents.length - 1]) indents.pop()
      if (!indents.length || indent > indents[indents.length - 1]) indents.push(indent)
      out.push({ k: 'li', depth: Math.min(indents.length - 1, 4), mark: m[3] ? `${m[3]}.` : null, text: m[4] })
      continue
    }
    if ((m = line.match(/^\s*>\s?(.*)$/))) { flush(); indents = []; out.push({ k: 'quote', text: m[1] }); continue }
    if (TABLE_ROW.test(line)) {
      flush(); indents = []
      const rows: string[][] = []
      for (; i < lines.length && TABLE_ROW.test(lines[i]); i++) {
        const cells = splitCells(lines[i].trim().slice(1, -1))
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells)
      }
      i--
      out.push({ k: 'table', rows })
      continue
    }
    const last = out[out.length - 1]
    if (!para.length && last?.k === 'li' && /^\s{2,}\S/.test(line)) { last.text += ' ' + line.trim(); continue }
    if (!/^\s/.test(line)) indents = []
    para.push(line.trim())
  }
  flush()
  return out
}

// maths first (DESIGN.md 61): a formula's _ and * are not italics
const INLINE = new RegExp(`(${MATH_SOURCE}|` + String.raw`⟦[^⟦⟧\n]{1,400}⟧|\((?:p|pp)\.\s?\d{1,4}(?:\s?[-–]\s?\d{1,4})?(?:\s?[,;]\s?(?:(?:p|pp)\.\s?)?\d{1,4}(?:\s?[-–]\s?\d{1,4})?){0,11}\)|\[[^\]\n]{1,200}\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'"]|~~[^~\n]+~~|\[\[[^\]\n]{1,40}\]\]|\*\*[^*\n]+\*\*|__[^_\n]+__|` + '`[^`\\n]+`' + String.raw`|\*[^*\s][^*\n]*\*|(?<![\w])_[^_\s][^_\n]*_(?![\w]))`, 'g')

/** one line's light formatting; citations through `cite` */
type PageCite = (page: number, label: string) => ReactNode

function inline(text: string, cite: (id: string) => ReactNode, key = '', page?: PageCite): ReactNode[] {
  // "... [[id]] ." - no gap left between a citation and the stop after it
  const clean = text.replace(/\]\]\s+(?=[.,;:!?)])/g, ']]')
  return clean.split(INLINE).map((piece, j) => {
    const k = `${key}${j}`
    if (!piece) return null
    if (j % 2 && texOf(piece)) return <Tex key={k} piece={piece} />
    // a number Double check looked up (DESIGN.md 82), as "⟦status|number|was|note⟧"
    const mk = j % 2 ? piece.match(/^⟦(ok|fixed|unchecked)\|([^|]*)\|([^|]*)\|([^|]*)⟧$/) : null
    if (mk) {
      const [, status, num, was, note] = mk
      const title = status === 'ok' ? `Checked: ${note}` : status === 'fixed'
        ? `Corrected from ${was}: ${note}` : `Not checked: ${note}`
      return (
        <span key={k} className={`num-mark ${status}`} title={title}>
          {status === 'fixed' && was ? <s>{was}</s> : null}{num}
        </span>
      )
    }
    // a file's page, cited "(p. 12)" (DESIGN.md 64): a link to it, where there is one
    // - and each page of a list, "(p. 4, p. 13)" or "(pp. 4, 12)", a link of
    // its own: a list was plain text, as if it cited nothing (DESIGN.md 89)
    const pg = j % 2 ? piece.match(/^\((?:p|pp)\.\s?(\d{1,4})/) : null
    if (pg) {
      if (!page) return <Fragment key={k}>{piece}</Fragment>
      const parts = piece.slice(1, -1).split(/\s?[,;]\s?/)
      if (parts.length === 1) return <Fragment key={k}>{page(Number(pg[1]), piece)}</Fragment>
      return (
        <Fragment key={k}>({parts.map((p, n) => {
          const num = p.match(/\d{1,4}/)
          const label = /^(?:p|pp)\./.test(p) ? p : `p. ${p}`
          return <Fragment key={n}>{n ? ', ' : ''}{num ? page(Number(num[0]), label) : p}</Fragment>
        })})</Fragment>
      )
    }
    if (piece.startsWith('[[')) {
      const id = piece.match(/^\[\[([0-9A-Za-z]{26})\]\]$/)
      return id ? <Fragment key={k}>{cite(id[1])}</Fragment> : null   // a citation the model got wrong: left out
    }
    // links (DESIGN.md 69): [words](https://...) and bare addresses; only http(s)
    const md = j % 2 ? piece.match(/^\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)$/) : null
    if (md) return <a key={k} {...LINK} href={md[2]} title={md[2]}>{inline(md[1], cite, k, page)}</a>
    if (j % 2 && /^https?:\/\//.test(piece)) return <a key={k} {...LINK} href={piece}>{piece}</a>
    if (/^~~.+~~$/.test(piece)) return <del key={k}>{inline(piece.slice(2, -2), cite, k, page)}</del>
    if (/^(\*\*|__).+\1$/.test(piece)) return <strong key={k}>{inline(piece.slice(2, -2), cite, k, page)}</strong>
    if (/^`.+`$/.test(piece)) return <code key={k}>{piece.slice(1, -1)}</code>
    if (/^([*_]).+\1$/.test(piece)) return <em key={k}>{inline(piece.slice(1, -1), cite, k, page)}</em>
    return <Fragment key={k}>{piece}</Fragment>
  })
}

const BULLETS = ['•', '◦', '▪', '◦', '▪']

/** a link opens in a new tab, and a press on it is not a press on the card */
const stop = (e: React.SyntheticEvent) => e.stopPropagation()
const LINK = { className: 'prose-link nodrag', target: '_blank', rel: 'noreferrer noopener',
               onPointerDown: stop, onMouseDown: stop, onClick: stop, onDoubleClick: stop }

/** "[ ] task" and "[x] done" at the start of a list item */
const TASK = /^\[([ xX])\]\s+/

export function Prose({ text, cite, className = '', page, breaks = false }: {
  text: string
  cite: (id: string) => ReactNode
  className?: string
  /** keep single line breaks - a card's text, written by hand */
  breaks?: boolean
  /** a file's page cited "(p. 12)" - drawn by this, when given (DESIGN.md 64) */
  page?: PageCite
}) {
  return (
    <div className={`prose ${className}`}>
      {blocks(text, breaks).map((b, i) => {
        switch (b.k) {
          case 'h': return <div key={i} className={`prose-h prose-h${Math.min(b.level, 4)}`}>{inline(b.text, cite, '', page)}</div>
          case 'p': return <p key={i} className="prose-p">{inline(b.text, cite, '', page)}</p>
          case 'li': {
            const task = b.text.match(TASK)
            const done = !!task && task[1] !== ' '
            return (
              <div key={i} className={`prose-li${task ? ' task' : ''}${done ? ' done' : ''}`}
                style={{ paddingLeft: `${b.depth * 1.35}em` }}>
                <span className={`prose-mark ${b.mark ? 'num' : ''}`}>
                  {task ? (done ? '☑' : '☐') : b.mark ?? BULLETS[b.depth]}</span>
                <span className="prose-li-text">{inline(task ? b.text.slice(task[0].length) : b.text, cite, '', page)}</span>
              </div>
            )
          }
          case 'quote': return <div key={i} className="prose-quote">{inline(b.text, cite, '', page)}</div>
          case 'code': return <pre key={i} className="prose-code">{b.text}</pre>
          case 'hr': return <hr key={i} className="prose-hr" />
          case 'table': return (
            <div key={i} className="prose-table-wrap">
              <table className="prose-table"><tbody>
                {b.rows.map((r, ri) => (
                  <tr key={ri}>{r.map((c, ci) => ri === 0
                    ? <th key={ci}>{inline(c, cite, '', page)}</th> : <td key={ci}>{inline(c, cite, '', page)}</td>)}</tr>
                ))}
              </tbody></table>
            </div>
          )
        }
      })}
    </div>
  )
}
