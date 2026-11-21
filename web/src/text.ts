/** Plain text that knows about lists.
 *
 *  Node bodies stay plain text - no markup language to learn, and the same
 *  string is what an LLM will read in v2. Lists are the one structure worth
 *  having, so they live as ordinary leading markers ("- " / "1. ") that the
 *  editor continues for you and the canvas renders as a real list.
 */

export type ListKind = 'none' | 'bullet' | 'number'

const BULLET = /^(\s*)[-*•]\s+(.*)$/
const NUMBER = /^(\s*)(\d+)[.)]\s+(.*)$/

export const URL_ONLY = /^https?:\/\/\S+$/i

const YOUTUBE = /(?:youtube\.com\/(?:watch\?(?:[^#]*&)?v=|embed\/|shorts\/|live\/|v\/)|youtu\.be\/)([A-Za-z0-9_-]{6,})/
const VIMEO = /vimeo\.com\/(?:video\/)?(\d+)/

/** The same detection the backend does, mirrored here so a video link still
 *  gets its player even if the server could not tell us about it. */
export function videoEmbed(url: string):
  { provider: string; video_id: string; embed: string } | null {
  const y = url.match(YOUTUBE)
  if (y) {
    return { provider: 'youtube', video_id: y[1],
      embed: `https://www.youtube-nocookie.com/embed/${y[1]}?autoplay=1&rel=0` }
  }
  const v = url.match(VIMEO)
  if (v) {
    return { provider: 'vimeo', video_id: v[1],
      embed: `https://player.vimeo.com/video/${v[1]}?autoplay=1` }
  }
  return null
}

export function providerName(url: string): string | null {
  const v = videoEmbed(url)
  if (!v) return null
  return v.provider === 'youtube' ? 'YouTube' : 'Vimeo'
}

/** Never show a raw address as a node's name, and never settle for the bare
 *  host when we know what kind of thing it is. */
export function niceTitle(given: string | undefined, url: string, host: string): string {
  const clean = (given || '').trim()
  const useless = !clean || URL_ONLY.test(clean) || clean === host
  if (!useless) return clean
  const name = providerName(url)
  if (name) return `${name} video`
  return host || url
}

export function lineKind(line: string): ListKind {
  if (BULLET.test(line)) return 'bullet'
  if (NUMBER.test(line)) return 'number'
  return 'none'
}

/** What the body as a whole currently is, so a toggle can turn it off again. */
export function detectList(text: string): ListKind {
  const lines = text.split('\n').filter((l) => l.trim())
  if (!lines.length) return 'none'
  const kinds = new Set(lines.map(lineKind))
  return kinds.size === 1 ? [...kinds][0] : 'none'
}

function strip(line: string): string {
  const b = line.match(BULLET)
  if (b) return b[2]
  const n = line.match(NUMBER)
  if (n) return n[3]
  return line
}

export function toList(text: string, kind: ListKind): string {
  const lines = text.split('\n')
  let counter = 0
  return lines
    .map((line) => {
      const body = strip(line)
      if (!body.trim()) return ''
      if (kind === 'bullet') return `- ${body}`
      if (kind === 'number') { counter += 1; return `${counter}. ${body}` }
      return body
    })
    .join('\n')
}

/** Pressing Enter inside a list continues it; pressing it on an empty item
 *  ends the list, the way every editor behaves. */
export function continueList(value: string, caret: number):
  { value: string; caret: number } | null {
  const before = value.slice(0, caret)
  const after = value.slice(caret)
  const lineStart = before.lastIndexOf('\n') + 1
  const line = before.slice(lineStart)

  const b = line.match(BULLET)
  if (b) {
    if (!b[2].trim()) {
      const cleared = value.slice(0, lineStart) + after
      return { value: cleared, caret: lineStart }
    }
    const insert = `\n${b[1]}- `
    return { value: before + insert + after, caret: caret + insert.length }
  }

  const n = line.match(NUMBER)
  if (n) {
    if (!n[3].trim()) {
      const cleared = value.slice(0, lineStart) + after
      return { value: cleared, caret: lineStart }
    }
    const insert = `\n${n[1]}${Number(n[2]) + 1}. `
    return { value: before + insert + after, caret: caret + insert.length }
  }
  return null
}

export interface Block {
  kind: 'p' | 'ul' | 'ol'
  items: string[]
}

/** Group a body into paragraphs and lists for display. */
export function blocks(text: string): Block[] {
  const out: Block[] = []
  for (const line of text.split('\n')) {
    const kind = lineKind(line)
    const wanted: Block['kind'] = kind === 'bullet' ? 'ul' : kind === 'number' ? 'ol' : 'p'
    const content = kind === 'none' ? line : strip(line)
    const last = out[out.length - 1]
    if (last && last.kind === wanted && wanted !== 'p') last.items.push(content)
    else if (last && last.kind === 'p' && wanted === 'p') last.items.push(content)
    else out.push({ kind: wanted, items: [content] })
  }
  return out
}

/** A card's title drawn as words (DESIGN.md 84): no heading or list marker,
 *  no bold, italics or code marks - and, when such marks came off, no
 *  trailing colon, which belonged to a "**Label:**". Only how it is drawn:
 *  the title itself is kept, and editing it shows it as written. Mirrors
 *  ask.plain_title on the server. */
const TITLE_LEAD = /^\s*(?:#{1,6}\s+|(?:[-*+•]|\d{1,3}[.)])\s+)/
const TITLE_MARKS: [RegExp, string][] = [
  [/\*\*(.+?)\*\*/g, '$1'], [/__(.+?)__/g, '$1'], [/`([^`]+)`/g, '$1'],
  [/(^|[\s(])\*([^*\s](?:[^*]*[^*\s])?)\*(?=$|[\s).,:;!?])/g, '$1$2'],
  [/(^|[\s(])_([^_\s](?:[^_]*[^_\s])?)_(?=$|[\s).,:;!?])/g, '$1$2'],
]

export function plainTitle(title: string): string {
  const raw = title.replace(/\s+/g, ' ').trim()
  if (!/[*_`#]|^\s*([-+•]|\d{1,3}[.)])\s/.test(raw)) return raw
  let out = raw.replace(TITLE_LEAD, '')
  for (const [re, keep] of TITLE_MARKS) out = out.replace(re, keep)
  out = out.replace(/^[*_ ]+|[*_ ]+$/g, '').trim()
  if (out !== raw) out = out.replace(/\s*:$/, '')
  return out || raw
}

// --------------------------------------------------- cards named with @ --
// A card names another as [[id]] (DESIGN.md 93): the same mark an answer cites
// with, so the card shows it as a chip with the other card's title, a click
// away. While the card is written the mark reads @[Title]; the labels map
// each @[...] back to its card when the card is saved.

const CARD_MARK = /\[\[([0-9A-Za-z]{26})\]\]/g

/** The card's text as it is written: [[id]] as @[Title], and what each label
 *  stands for. Two cards of one title get "Title (2)" and so on. */
export function toWriting(text: string, titleOf: (id: string) => string | undefined):
  { text: string; labels: Map<string, string> } {
  const labels = new Map<string, string>()          // label -> id
  const byId = new Map<string, string>()
  const out = text.replace(CARD_MARK, (whole, id: string) => {
    const known = byId.get(id)
    if (known) return `@[${known}]`
    const title = titleOf(id)
    if (title === undefined) return whole          // not on this board: left as it is
    const label = freeLabel(title, labels)
    labels.set(label, id)
    byId.set(id, label)
    return `@[${label}]`
  })
  return { text: out, labels }
}

/** A label no other card in this text uses: brackets are taken out, so the
 *  label always ends where its mark does. */
export function freeLabel(title: string, labels: Map<string, string>, id?: string): string {
  const base = (plainTitle(title) || 'Untitled').replace(/[[\]]/g, '').trim() || 'Untitled'
  for (let n = 1; ; n++) {
    const label = n === 1 ? base : `${base} (${n})`
    const holder = labels.get(label)
    if (holder === undefined || holder === id) return label
  }
}

/** The card's text as it is kept: every @[label] the labels know becomes
 *  [[id]]; one typed by hand, or edited, stays as written. */
export function fromWriting(text: string, labels: Map<string, string>): string {
  return text.replace(/@\[([^\]\n]+)\]/g, (whole, label: string) => {
    const id = labels.get(label)
    return id ? `[[${id}]]` : whole
  })
}

/** The cards a kept text names. */
export function namedCards(text: string): string[] {
  return [...new Set(Array.from(text.matchAll(CARD_MARK), (m) => m[1]))]
}

/** The words typed after an @ up to the caret, while a card is being named:
 *  the @ starts the text or follows a space or bracket, the words stay on one
 *  line and short. null when the caret is not in such words. */
export function atQuery(value: string, caret: number): { start: number; query: string } | null {
  const before = value.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at < 0) return null
  if (at > 0 && !/[\s([{"'“]/.test(before[at - 1])) return null   // an address: me@example.org
  const query = before.slice(at + 1)
  if (query.length > 40 || /[\n[\]]/.test(query) || /^\s/.test(query)) return null
  return { start: at, query }
}
