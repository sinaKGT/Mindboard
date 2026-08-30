/** Ephemeral interface state - never persisted, never an op. */
import { create } from 'zustand'

interface UI {
  editingId: string | null
  editingEdgeId: string | null
  paletteOpen: boolean
  themeOpen: boolean
  minimap: boolean
  focusId: string | null
  selected: string[]
  sparkIds: string[]
  set<K extends keyof UI>(key: K, value: UI[K]): void
}

export const useUI = create<UI>()((set) => ({
  editingId: null,
  editingEdgeId: null,
  paletteOpen: false,
  themeOpen: false,
  minimap: false,
  focusId: null,
  selected: [],
  sparkIds: [],
  set: (key, value) => set({ [key]: value } as any),
}))
