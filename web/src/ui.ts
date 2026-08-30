/** Ephemeral interface state - never persisted, never an op. */
import { create } from 'zustand'

import type { ImageKind } from './exportImage'

interface UI {
  /** A connection started by clicking a port, waiting for its target. */
  linking: { sourceId: string } | null
  /** Registered by the canvas so the command palette can reach it. */
  exportImage: ((kind: ImageKind, scale: number) => Promise<void>) | null
  editingId: string | null
  editingEdgeId: string | null
  paletteOpen: boolean
  themeOpen: boolean
  minimap: boolean
  focusId: string | null
  selected: string[]
  selectedEdges: string[]
  /** The group a dragged node is hovering over, about to go inside. */
  dropTarget: string | null
  sparkIds: string[]
  set<K extends keyof UI>(key: K, value: UI[K]): void
}

export const useUI = create<UI>()((set) => ({
  linking: null,
  exportImage: null,
  editingId: null,
  editingEdgeId: null,
  paletteOpen: false,
  themeOpen: false,
  minimap: false,
  focusId: null,
  selected: [],
  selectedEdges: [],
  dropTarget: null,
  sparkIds: [],
  set: (key, value) => set({ [key]: value } as any),
}))
