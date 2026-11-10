export type NodeType = 'text' | 'image' | 'video' | 'link' | 'file' | 'meta'
export type EdgeKind = 'relates' | 'causes' | 'supports' | 'contradicts' | 'part-of' | 'example-of'
export type Provenance = 'user' | 'import' | 'ai'

export const EDGE_KINDS: EdgeKind[] = [
  'relates', 'causes', 'supports', 'contradicts', 'part-of', 'example-of',
]

/** How each relationship reads on the canvas. The vocabulary is closed on
 *  purpose: v2's analysis reasons over typed relations. */
export const KIND_STYLE: Record<EdgeKind, { dash?: string; width: number; arrow: boolean }> = {
  'relates':    { width: 1.6, arrow: false },
  'causes':     { width: 2.0, arrow: true },
  'supports':   { width: 1.8, arrow: true },
  'contradicts':{ width: 1.8, arrow: true, dash: '6 4' },
  'part-of':    { width: 1.6, arrow: true, dash: '2 4' },
  'example-of': { width: 1.6, arrow: true, dash: '9 5' },
}

export type NodeTone = 'accent' | 'tinted' | 'solid'

export interface NodeStyle {
  color?: string
  /** How far the colour carries: a bar, a wash, or the whole card. */
  tone?: NodeTone
  fontSize?: number
  weight?: number
  align?: 'left' | 'center'
}

export interface MNode {
  id: string
  parent: string | null
  type: NodeType
  pos: [number, number]
  size: [number | null, number | null]
  z: number
  title: string
  content: Record<string, any>
  style: NodeStyle
  collapsed: boolean
  provenance: Provenance
}

export interface MEdge {
  id: string
  source: string
  target: string
  sourceHandle: string | null
  targetHandle: string | null
  label: string
  kind: EdgeKind
  directed: boolean
  style: Record<string, any>
  provenance: Provenance
}

export interface BoardSettings {
  theme?: string
  palette?: Record<string, string>
  /** the theme of your own this board uses, if any (DESIGN.md 52) */
  custom?: string | null
  /** its tile's colour on the first page (DESIGN.md 54) */
  tile?: string
  viewport?: { x: number; y: number; zoom: number }
}

export interface BoardMeta {
  id: string
  title: string
  description: string
  revision: number
  settings: BoardSettings
  created_at: string
  updated_at: string
  node_count: number
  edge_count: number
  counts_by_type?: Record<string, number>
  preview?: { nodes: { x: number; y: number; t: string; c?: string }[]; edges: [number, number][] }
}

export type Op =
  | { kind: 'node.create'; node: MNode }
  | { kind: 'node.update'; id: string; patch: Partial<MNode> }
  | { kind: 'node.move'; moves: { id: string; x: number; y: number }[] }
  | { kind: 'node.delete'; ids: string[] }
  | { kind: 'edge.create'; edge: MEdge }
  | { kind: 'edge.update'; id: string; patch: Partial<MEdge> }
  | { kind: 'edge.delete'; ids: string[] }
  | { kind: 'meta.wrap'; meta: MNode; children: { id: string; x: number; y: number }[] }
  | { kind: 'meta.unwrap'; metaId: string; children: { id: string; parent: string | null; x: number; y: number }[] }
  | { kind: 'board.update'; patch: { title?: string; description?: string; settings?: BoardSettings } }

export interface Envelope { opId: string; op: Op; origin?: Provenance }

const ENC = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'

/** ULIDs generated client-side, so a node exists the instant you create it. */
export function ulid(): string {
  let ts = Date.now()
  const time: string[] = []
  for (let i = 9; i >= 0; i--) { time[i] = ENC[ts % 32]; ts = Math.floor(ts / 32) }
  const rand: string[] = []
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  for (let i = 0; i < 16; i++) rand.push(ENC[bytes[i] % 32])
  return time.join('') + rand.join('')
}

export function newNode(partial: Partial<MNode> & { pos: [number, number] }): MNode {
  return {
    id: ulid(), parent: null, type: 'text', size: [null, null], z: 0, title: '',
    content: {}, style: {}, collapsed: true, provenance: 'user', ...partial,
  }
}

export function newEdge(source: string, target: string, partial: Partial<MEdge> = {}): MEdge {
  return {
    id: ulid(), source, target, sourceHandle: null, targetHandle: null,
    label: '', kind: 'relates', directed: true, style: {}, provenance: 'user', ...partial,
  }
}
