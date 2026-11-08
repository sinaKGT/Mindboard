/** An answer, laid out (DESIGN.md 45).
 *
 *  The model writes light markdown - headings, bullet and numbered lists
 *  (often nested), bold, italics, the odd table - and [[card]] citations.
 *  Shown as it came, that was a wall of `###` and `*`. Here it is read into
 *  blocks first and then drawn: headings as headings, lists with their real
 *  depth, citations as chips through `cite`. Nothing in an answer is ever
 *  treated as HTML; everything is text in React elements.
 */
import { Fragment, type ReactNode } from 'react'

export type Block =
  | { k: 'h'; level: number; text: string }
  | { k: 'p'; text: string }
  | { k: 'li'; depth: number; mark: string | null; text: string }
  | { k: 'quote'; text: string }
  | { k: 'code'; text: string }
  | { k: 'hr' }
  | { k: 'table'; rows: string[][] }

const TABLE_ROW = /^\s*\|.*\|\s*$/

/** the answer as blocks */
export function blocks(src: string): Block[] {
  const out: Block[] = []
  const lines = src.replace(/\r/g, '').replace(/\t/g, '    ').split('\n')
  let para: string[] = []
  let indents: number[] = []                     // the open list's indentation, outermost first
  const flush = () => { if (para.length) { out.push({ k: 'p', text: para.join(' ') }); para = [] } }
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
        const cells = lines[i].trim().slice(1, -1).split('|').map((c) => c.trim())
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

const INLINE = /(\[\[[^\]\n]{1,40}\]\]|\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\*[^*\s][^*\n]*\*|(?<![\w])_[^_\s][^_\n]*_(?![\w]))/g

/** one line's light formatting; citations through `cite` */
function inline(text: string, cite: (id: string) => ReactNode, key = ''): ReactNode[] {
  // "... [[id]] ." - no gap left between a citation and the stop after it
  const clean = text.replace(/\]\]\s+(?=[.,;:!?)])/g, ']]')
  return clean.split(INLINE).map((piece, j) => {
    const k = `${key}${j}`
    if (!piece) return null
    if (piece.startsWith('[[')) {
      const id = piece.match(/^\[\[([0-9A-Za-z]{26})\]\]$/)
      return id ? <Fragment key={k}>{cite(id[1])}</Fragment> : null   // a citation the model got wrong: left out
    }
    if (/^(\*\*|__).+\1$/.test(piece)) return <strong key={k}>{inline(piece.slice(2, -2), cite, k)}</strong>
    if (/^`.+`$/.test(piece)) return <code key={k}>{piece.slice(1, -1)}</code>
    if (/^([*_]).+\1$/.test(piece)) return <em key={k}>{inline(piece.slice(1, -1), cite, k)}</em>
    return <Fragment key={k}>{piece}</Fragment>
  })
}

const BULLETS = ['•', '◦', '▪', '◦', '▪']

export function Prose({ text, cite, className = '' }: {
  text: string
  cite: (id: string) => ReactNode
  className?: string
}) {
  return (
    <div className={`prose ${className}`}>
      {blocks(text).map((b, i) => {
        switch (b.k) {
          case 'h': return <div key={i} className={`prose-h prose-h${Math.min(b.level, 4)}`}>{inline(b.text, cite)}</div>
          case 'p': return <p key={i} className="prose-p">{inline(b.text, cite)}</p>
          case 'li': return (
            <div key={i} className="prose-li" style={{ paddingLeft: `${b.depth * 1.35}em` }}>
              <span className={`prose-mark ${b.mark ? 'num' : ''}`}>{b.mark ?? BULLETS[b.depth]}</span>
              <span className="prose-li-text">{inline(b.text, cite)}</span>
            </div>
          )
          case 'quote': return <div key={i} className="prose-quote">{inline(b.text, cite)}</div>
          case 'code': return <pre key={i} className="prose-code">{b.text}</pre>
          case 'hr': return <hr key={i} className="prose-hr" />
          case 'table': return (
            <div key={i} className="prose-table-wrap">
              <table className="prose-table"><tbody>
                {b.rows.map((r, ri) => (
                  <tr key={ri}>{r.map((c, ci) => ri === 0
                    ? <th key={ci}>{inline(c, cite)}</th> : <td key={ci}>{inline(c, cite)}</td>)}</tr>
                ))}
              </tbody></table>
            </div>
          )
        }
      })}
    </div>
  )
}
