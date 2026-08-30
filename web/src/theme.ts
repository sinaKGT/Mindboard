import type { BoardSettings } from './types'

export const THEMES = ['midnight', 'slate', 'ink', 'daylight'] as const
export type ThemeName = (typeof THEMES)[number]

/** The tokens the settings panel exposes. Deliberately short - a palette
 *  editor with forty swatches is a preferences screen, not a design. */
export const CUSTOMISABLE: { key: string; label: string }[] = [
  { key: '--mb-bg', label: 'Canvas' },
  { key: '--mb-surface', label: 'Node' },
  { key: '--mb-text', label: 'Text' },
  { key: '--mb-accent', label: 'Accent' },
  { key: '--mb-type-text', label: 'Text nodes' },
  { key: '--mb-type-image', label: 'Image nodes' },
  { key: '--mb-type-video', label: 'Video nodes' },
  { key: '--mb-type-meta', label: 'Meta-nodes' },
]

export const NODE_COLOURS = [
  '#6ea8fe', '#6fd7a4', '#cf8ce8', '#e6b95c', '#ff9e64', '#f97583', '#90a0b8',
]

export function readVar(key: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(key).trim()
  return v.startsWith('#') ? v : hexish(v)
}

function hexish(v: string): string {
  const m = v.match(/rgba?\((\d+)[,\s]+(\d+)[,\s]+(\d+)/)
  if (!m) return '#000000'
  return '#' + [1, 2, 3].map((i) => Number(m[i]).toString(16).padStart(2, '0')).join('')
}

export function applyTheme(settings: BoardSettings | undefined) {
  const root = document.documentElement
  root.dataset.theme = settings?.theme || 'midnight'
  // clear any previous overrides, then apply this board's
  for (const { key } of CUSTOMISABLE) root.style.removeProperty(key)
  for (const [key, value] of Object.entries(settings?.palette ?? {})) {
    if (CUSTOMISABLE.some((c) => c.key === key)) root.style.setProperty(key, value)
  }
}

function luminance(hex: string): number {
  const n = hex.replace('#', '')
  const rgb = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2) || '0', 16) / 255)
  const lin = rgb.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
}

/** A palette that drops text below 4.5:1 gets a warning, not a silent
 *  unreadable board. */
export function contrast(a: string, b: string): number {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (l1 + 0.05) / (l2 + 0.05)
}
