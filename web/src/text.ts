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
