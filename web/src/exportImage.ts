/**
 * Export the whole level as an image - the real thing, rendered at whatever
 * resolution you ask for, not a screenshot of the visible viewport.
 */
import { getNodesBounds, type Node } from '@xyflow/react'
import { toPng, toSvg } from 'html-to-image'

export type ImageKind = 'png' | 'svg'

const PADDING = 64
/** Browsers refuse canvases beyond roughly 16k on a side; stay well clear. */
const MAX_SIDE = 12000

function slug(title: string): string {
  const out = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  return out || 'board'
}

function download(dataUrl: string, filename: string) {
  const a = document.createElement('a')
  a.href = dataUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
}

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

export async function exportBoardImage(opts: {
  nodes: Node[]
  title: string
  kind: ImageKind
  scale: number
  levelName?: string
}): Promise<{ filename: string; width: number; height: number; scale: number }> {
  const viewport = document.querySelector('.react-flow__viewport') as HTMLElement | null
  const stage = document.querySelector('.stage') as HTMLElement | null
  if (!viewport || !stage) throw new Error('the canvas is not open')

  const real = opts.nodes.filter((n) => !n.id.startsWith('ghost:'))
  if (!real.length) throw new Error('there is nothing on this level to export')

  const bounds = getNodesBounds(real)
  const width = Math.ceil(bounds.width + PADDING * 2)
  const height = Math.ceil(bounds.height + PADDING * 2)

  // honour the requested quality, but never ask for a canvas the browser will refuse
  const scale = Math.max(1, Math.min(opts.scale, MAX_SIDE / Math.max(width, height)))

  stage.classList.add('exporting')
  // let the class take effect before we read computed styles
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))

  const settings = {
    backgroundColor: cssVar('--mb-bg', '#0d0f13'),
    width,
    height,
    pixelRatio: scale,
    // assets are content-addressed and immutable; busting the cache only
    // produces 404s for the font and asset URLs
    cacheBust: false,
    style: {
      width: `${width}px`,
      height: `${height}px`,
      transform: `translate(${PADDING - bounds.x}px, ${PADDING - bounds.y}px) scale(1)`,
    },
    filter: (node: HTMLElement) => {
      const id = node?.dataset?.id
      return !(typeof id === 'string' && id.startsWith('ghost:'))
    },
  }

  try {
    const url = opts.kind === 'svg'
      ? await toSvg(viewport, settings)
      : await toPng(viewport, settings)
    const suffix = opts.levelName ? `-${slug(opts.levelName)}` : ''
    const filename = `${slug(opts.title)}${suffix}.${opts.kind}`
    download(url, filename)
    return { filename, width, height, scale }
  } finally {
    stage.classList.remove('exporting')
  }
}
