/** Everything the interface can do to a board, expressed as ops. */
import { api } from './api'
import { useStore } from './store'
import { type ListKind, niceTitle, toList, videoEmbed } from './text'
import type { EdgeKind, MEdge, MNode, NodeType, Op } from './types'
import { newEdge, newNode } from './types'
import { useUI } from './ui'

const S = () => useStore.getState()

/** Mark ids as just-created so the canvas can animate them in, then forget. */
export function spark(...ids: string[]) {
  const ui = useUI.getState()
  ui.set('sparkIds', [...ui.sparkIds, ...ids])
  setTimeout(() => {
    const now = useUI.getState()
    now.set('sparkIds', now.sparkIds.filter((id) => !ids.includes(id)))
  }, 900)
}

/** Estimates for nodes that size themselves to their content. */
const GUESS_W = 300
const GUESS_H = 190

/** Cascade a new node clear of anything already sitting there, so pasting twice
 *  in a row does not bury one card under another. Only moves on a real overlap,
 *  so a node you placed deliberately stays where you put it. */
export function freeSpot(pos: [number, number], parent: string | null,
                         size: [number, number] = [320, 200]): [number, number] {
  const siblings = Object.values(S().nodes).filter((n) => (n.parent ?? null) === parent)
  let [x, y] = pos
  for (let i = 0; i < 30; i++) {
    const overlaps = siblings.some((n) => {
      const w = n.size[0] ?? GUESS_W
      const h = n.size[1] ?? GUESS_H
      return x < n.pos[0] + w && x + size[0] > n.pos[0]
        && y < n.pos[1] + h && y + size[1] > n.pos[1]
    })
    if (!overlaps) break
    x += 48
    y += 36
  }
  return [Math.round(x), Math.round(y)]
}

export function addTextNode(pos: [number, number], parent: string | null): MNode {
  const node = newNode({ pos: freeSpot(pos, parent), parent, type: 'text', title: '',
                         content: { text: '' } })
  S().run([{ kind: 'node.create', node }])
  spark(node.id)
  useUI.getState().set('editingId', node.id)
  return node
}

/** Drag from a node and drop on empty canvas: the fast idea-flow path. */
export function addConnected(from: MNode, at?: [number, number]): MNode {
  const pos: [number, number] = at ?? [from.pos[0] + 330, from.pos[1] + 18]
  const node = newNode({ pos, parent: from.parent, type: 'text', content: { text: '' } })
  const edge = newEdge(from.id, node.id)
  S().run([{ kind: 'node.create', node }, { kind: 'edge.create', edge }])
  spark(node.id, edge.id)
  useUI.getState().set('editingId', node.id)
  return node
}

export const updateNode = (id: string, patch: Partial<MNode>) =>
  S().run([{ kind: 'node.update', id, patch }])

export const updateEdge = (id: string, patch: Partial<MEdge>) =>
  S().run([{ kind: 'edge.update', id, patch }])

export const deleteNodes = (ids: string[]) =>
  ids.length && S().run([{ kind: 'node.delete', ids }])

export const deleteEdges = (ids: string[]) =>
  ids.length && S().run([{ kind: 'edge.delete', ids }])

export function connect(source: string, target: string, kind: EdgeKind = 'relates') {
  if (source === target) return
  const exists = Object.values(S().edges).some((e) => e.source === source && e.target === target)
  if (exists) return
  const edge = newEdge(source, target, { kind })
  S().run([{ kind: 'edge.create', edge }])
  spark(edge.id)
}

/** Turn a node that is really just a URL into the link (or media) it names. */
export async function linkifyNode(nodeId: string, url: string): Promise<void> {
  const s = S()
  const boardId = s.boardId
  const node = s.nodes[nodeId]
  if (!boardId || !node) return
  try {
    const got = await api.fromUrl(boardId, url)
    if (got.kind === 'link') {
      const video = got.video ?? videoEmbed(url)
      S().run([{
        kind: 'node.update', id: nodeId,
        patch: {
          type: 'link' as NodeType,
          title: niceTitle(got.title, url, got.host),
          size: [video || got.thumb ? 360 : 300, null],
          content: {
            url, host: got.host, excerpt: got.excerpt,
            ...(got.thumb ? { thumb: got.thumb } : {}),
            ...(video
              ? { provider: video.provider, video_id: video.video_id, embed: video.embed }
              : {}),
          },
        },
      }])
    } else {
      const type: NodeType = got.mime.startsWith('video/') ? 'video'
        : got.mime.startsWith('image/') ? 'image' : 'file'
      S().run([{
        kind: 'node.update', id: nodeId,
        patch: {
          type,
          title: got.orig_name || url.split('/').pop() || 'media',
          size: [320, null],
          content: { sha256: got.sha256, alt: got.orig_name, source_url: url },
        },
      }])
    }
    spark(nodeId)
  } catch (err: any) {
    s.say(`could not fetch that link: ${err.message}`, true)
  }
}

export function setBodyList(node: MNode, kind: ListKind) {
  const text = String(node.content.text ?? node.content.summary ?? '')
  const next = toList(text, kind)
  if (next === text) return
  const { summary: _dropped, ...rest } = node.content as Record<string, unknown>
  updateNode(node.id, { content: { ...rest, text: next } })
}

export function setStyle(node: MNode, patch: Partial<MNode['style']>) {
  updateNode(node.id, { style: { ...node.style, ...patch } })
}

// ------------------------------------------------------------ meta-nodes -- //

export function wrapSelection(ids: string[]) {
  const s = S()
  const picked = ids.map((id) => s.nodes[id]).filter(Boolean)
  if (!picked.length) return
  const parent = picked[0].parent ?? null
  const xs = picked.map((n) => n.pos[0])
  const ys = picked.map((n) => n.pos[1])
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  const meta = newNode({
    pos: [(minX + Math.max(...xs)) / 2, (minY + Math.max(...ys)) / 2],
    parent, type: 'meta', title: 'Group', content: { summary: '' },
  })
  s.run([{
    kind: 'meta.wrap',
    meta,
    children: picked.map((n) => ({ id: n.id, x: n.pos[0] - minX, y: n.pos[1] - minY })),
  }])
  spark(meta.id)
  useUI.getState().set('editingId', meta.id)
}

export function unwrapMeta(metaId: string) {
  const s = S()
  const meta = s.nodes[metaId]
  if (!meta || meta.type !== 'meta') return
  const kids = Object.values(s.nodes).filter((n) => n.parent === metaId)
  s.run([{
    kind: 'meta.unwrap',
    metaId,
    children: kids.map((k) => ({
      id: k.id, parent: meta.parent, x: meta.pos[0] + k.pos[0], y: meta.pos[1] + k.pos[1],
    })),
  }])
}

// ----------------------------------------------------------------- media -- //

async function probeImage(file: File) {
  try {
    const bmp = await createImageBitmap(file)
    const out = { width: bmp.width, height: bmp.height }
    bmp.close()
    return out
  } catch { return {} }
}

function probeVideo(file: File): Promise<{ width?: number; height?: number; duration_ms?: number }> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file)
    const v = document.createElement('video')
    v.preload = 'metadata'
    v.onloadedmetadata = () => {
      resolve({
        width: v.videoWidth, height: v.videoHeight,
        duration_ms: Math.round((v.duration || 0) * 1000),
      })
      URL.revokeObjectURL(url)
    }
    v.onerror = () => { resolve({}); URL.revokeObjectURL(url) }
    v.src = url
  })
}

/** A poster frame captured in the browser, so video cards look right without
 *  shipping ffmpeg with the app. */
async function capturePoster(boardId: string, nodeId: string, file: File) {
  const url = URL.createObjectURL(file)
  try {
    const v = document.createElement('video')
    v.preload = 'metadata'
    v.muted = true
    v.src = url
    await new Promise<void>((res, rej) => {
      v.onloadeddata = () => res()
      v.onerror = () => rej(new Error('no metadata'))
    })
    v.currentTime = Math.min(0.5, (v.duration || 1) / 3)
    await new Promise<void>((res) => { v.onseeked = () => res() })
    const c = document.createElement('canvas')
    c.width = v.videoWidth
    c.height = v.videoHeight
    c.getContext('2d')!.drawImage(v, 0, 0)
    const blob: Blob | null = await new Promise((res) => c.toBlob(res, 'image/webp', 0.8))
    if (!blob) return
    const info = await api.upload(boardId, new File([blob], 'poster.webp', { type: 'image/webp' }),
      { node_id: nodeId, role: 'poster' })
    const node = S().nodes[nodeId]
    if (node) updateNode(nodeId, { content: { ...node.content, poster: info.sha256 } })
  } catch { /* a board without a poster frame is fine */ } finally {
    URL.revokeObjectURL(url)
  }
}

export async function addFile(file: File, pos: [number, number], parent: string | null,
                              focus = false): Promise<string | null> {
  const s = S()
  const boardId = s.boardId
  if (!boardId) return null
  try {
    const isVideo = file.type.startsWith('video/')
    const probed = isVideo ? await probeVideo(file) : await probeImage(file)
    const info = await api.upload(boardId, file, probed as Record<string, number>)
    const type: NodeType = info.mime.startsWith('video/') ? 'video'
      : info.mime.startsWith('image/') ? 'image' : 'file'
    const node = newNode({
      pos: freeSpot(pos, parent), parent, type,
      title: file.name.replace(/\.[^.]+$/, ''),
      size: [type === 'file' ? null : 320, null],
      content: type === 'file'
        ? { sha256: info.sha256, filename: file.name }
        : { sha256: info.sha256, alt: file.name, ...probed },
    })
    S().run([{ kind: 'node.create', node }])
    spark(node.id)
    // dropping one file usually means you want to say something about it
    if (focus) useUI.getState().set('editingId', node.id)
    if (type === 'video') void capturePoster(boardId, node.id, file)
    return node.id
  } catch (err: any) {
    s.say(`could not add ${file.name}: ${err.message}`, true)
    return null
  }
}

/** A pasted URL: media is fetched and stored locally, a page becomes a link node. */
export async function addUrl(url: string, pos: [number, number],
                             parent: string | null): Promise<string | null> {
  const s = S()
  const boardId = s.boardId
  if (!boardId) return null
  try {
    const got = await api.fromUrl(boardId, url)
    if (got.kind === 'link') {
      const video = got.video ?? videoEmbed(url)
      const node = newNode({
        pos: freeSpot(pos, parent), parent, type: 'link',
        title: niceTitle(got.title, url, got.host),
        size: [video || got.thumb ? 360 : 300, null],
        content: {
          url, host: got.host, excerpt: got.excerpt,
          // the preview picture is stored locally like any other media
          ...(got.thumb ? { thumb: got.thumb } : {}),
          ...(video
            ? { provider: video.provider, video_id: video.video_id, embed: video.embed }
            : {}),
        },
      })
      S().run([{ kind: 'node.create', node }])
      spark(node.id)
      if (got.unreachable) {
        s.say(video
          ? 'added the video — could not reach the page for its title'
          : 'added the link — could not reach the page for a preview')
      }
      return node.id
    }
    const type: NodeType = got.mime.startsWith('video/') ? 'video'
      : got.mime.startsWith('image/') ? 'image' : 'file'
    const node = newNode({
      pos: freeSpot(pos, parent), parent, type,
      title: (got.orig_name || url.split('/').pop() || 'media'),
      size: [320, null],
      content: { sha256: got.sha256, alt: got.orig_name, source_url: url },
    })
    S().run([{ kind: 'node.create', node }])
    spark(node.id)
    return node.id
  } catch (err: any) {
    s.say(`could not fetch that URL: ${err.message}`, true)
    return null
  }
}

// ------------------------------------------------------------ auto-layout -- //

/** Tidies the current level only, animated in one undoable step. Never runs
 *  on its own - the point of a mind map is that you placed things. */
export function autoLayout(parent: string | null) {
  const s = S()
  const nodes = Object.values(s.nodes).filter((n) => (n.parent ?? null) === parent)
  if (nodes.length < 2) return
  const pos = new Map(nodes.map((n) => [n.id, { x: n.pos[0], y: n.pos[1] }]))
  const links = Object.values(s.edges)
    .filter((e) => pos.has(e.source) && pos.has(e.target))
  const k = 320
  for (let step = 0; step < 260; step++) {
    const temp = 60 * (1 - step / 260)
    const disp = new Map(nodes.map((n) => [n.id, { x: 0, y: 0 }]))
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = pos.get(nodes[i].id)!
        const b = pos.get(nodes[j].id)!
        let dx = a.x - b.x
        let dy = a.y - b.y
        let d = Math.hypot(dx, dy) || 0.01
        if (d < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d = 1 }
        const f = (k * k) / (d * d)
        const da = disp.get(nodes[i].id)!
        const db = disp.get(nodes[j].id)!
        da.x += dx * f; da.y += dy * f
        db.x -= dx * f; db.y -= dy * f
      }
    }
    for (const e of links) {
      const a = pos.get(e.source)!
      const b = pos.get(e.target)!
      const dx = a.x - b.x
      const dy = a.y - b.y
      const d = Math.hypot(dx, dy) || 0.01
      const f = d / k
      const da = disp.get(e.source)!
      const db = disp.get(e.target)!
      da.x -= dx * f; da.y -= dy * f
      db.x += dx * f; db.y += dy * f
    }
    for (const n of nodes) {
      const d = disp.get(n.id)!
      const mag = Math.hypot(d.x, d.y) || 1
      const p = pos.get(n.id)!
      p.x += (d.x / mag) * Math.min(mag, temp)
      p.y += (d.y / mag) * Math.min(mag, temp)
    }
  }
  S().run([{
    kind: 'node.move',
    moves: nodes.map((n) => ({
      id: n.id, x: Math.round(pos.get(n.id)!.x), y: Math.round(pos.get(n.id)!.y),
    })),
  }] as Op[])
}

/** Navigate to a node wherever it lives, opening the meta-nodes on the way. */
export function jumpToNode(id: string) {
  const all = S().nodes
  const chain: string[] = []
  let cur = all[id]
  if (!cur) return
  while (cur?.parent) { chain.unshift(cur.parent); cur = all[cur.parent] }
  useStore.setState({ path: chain })
  useUI.getState().set('focusId', id)
}
