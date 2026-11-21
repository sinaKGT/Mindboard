/** Maths, drawn (DESIGN.md 61).
 *
 *  A picture of a formula is described in LaTeX - `$\arg\max_x P(x)$` - and
 *  that text reaches cards and answers. Shown as it came it is code, and its
 *  underscores were read as italics. Here a formula is found first, before
 *  any other formatting, and drawn by KaTeX (bundled: no internet needed).
 *
 *  What counts as maths: $$...$$ and \[...\] (on a line of their own), $...$
 *  and \(...\) (in a sentence). A $...$ has no space just inside either
 *  dollar and is not just a number, so "$5 and $10" and "$5-$10" stay prices.
 *  A formula KaTeX cannot read is left as the text it was, never an error.
 *
 *  KaTeX's output is HTML, made by KaTeX from its own reading of the formula
 *  (trust off: no links, no styles, no raw HTML from the text), so the rule
 *  that nothing in an answer is treated as HTML still holds for the answer.
 */
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { Fragment, type ReactNode } from 'react'

/** the four ways of writing a formula; no capturing groups, so it can join a split */
export const MATH_SOURCE = String.raw`\$\$[^$]+?\$\$|\\\[[\s\S]+?\\\]|\\\([^\n]+?\\\)|(?<![\\$\w])\$(?![\s$])[^$\n]+?(?<![\s\\])\$(?![\w$])`
const MATH = new RegExp(`(${MATH_SOURCE})`, 'g')

/** the formula inside a piece, and whether it stands on its own line; null if it is not maths */
export function texOf(piece: string): { tex: string; display: boolean } | null {
  let tex: string, display: boolean
  if (piece.startsWith('$$') && piece.endsWith('$$') && piece.length > 4) { tex = piece.slice(2, -2); display = true }
  else if (piece.startsWith('\\[') && piece.endsWith('\\]')) { tex = piece.slice(2, -2); display = true }
  else if (piece.startsWith('\\(') && piece.endsWith('\\)')) { tex = piece.slice(2, -2); display = false }
  else if (piece.length > 2 && piece.startsWith('$') && piece.endsWith('$')) { tex = piece.slice(1, -1); display = false }
  else return null
  tex = tex.trim()
  if (!tex || /^[\d.,\s%+-]+$/.test(tex)) return null      // a price or a number, not a formula
  return { tex, display }
}

const drawn = new Map<string, string | null>()

/** KaTeX's drawing of a formula, remembered; null if it cannot be read */
function draw(tex: string, display: boolean): string | null {
  const key = `${display ? 'D' : 'I'}${tex}`
  if (drawn.has(key)) return drawn.get(key)!
  let html: string | null
  try {
    html = katex.renderToString(tex, { displayMode: display, throwOnError: true, trust: false,
                                       strict: 'ignore', output: 'html', maxExpand: 500 })
  } catch {
    html = null
  }
  if (drawn.size > 500) drawn.clear()
  drawn.set(key, html)
  return html
}

/** one formula, or the text it was if it is not one KaTeX can read */
export function Tex({ piece }: { piece: string }) {
  const got = texOf(piece)
  const html = got && draw(got.tex, got.display)
  if (!got || !html) return <>{piece}</>
  return <span className={got.display ? 'math math-display' : 'math'} title={got.tex}
    dangerouslySetInnerHTML={{ __html: html }} />
}

/** plain text with its formulas drawn - a card's text */
export function withMath(text: string, rest: (plain: string, key: string) => ReactNode = (t) => t): ReactNode[] {
  if (!text.includes('$') && !text.includes('\\')) return [rest(text, '0')]
  return text.split(MATH).map((piece, j) => {
    if (!piece) return null
    if (j % 2) return <Tex key={j} piece={piece} />
    return <Fragment key={j}>{rest(piece, String(j))}</Fragment>
  })
}
