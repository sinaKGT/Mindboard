import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'

import { api } from './api'
import { Canvas } from './Canvas'
// the welcome page: two signs in sand, the first thing you see (DESIGN.md 43)
const Welcome = lazy(() => import('./experts/Welcome').then((m) => ({ default: m.Welcome }))
  .catch((err) => {
    if (!sessionStorage.getItem('mb_reloaded')) { sessionStorage.setItem('mb_reloaded', '1'); location.reload() }
    throw err
  }))
// the Experts page brings three.js: loaded only when it is opened
// (after an update, a tab still open asks for the old build's file: reload once to get the new one)
const ExpertsPage = lazy(() => import('./experts/ExpertsPage').then((m) => ({ default: m.ExpertsPage }))
  .catch((err) => {
    if (!sessionStorage.getItem('mb_reloaded')) { sessionStorage.setItem('mb_reloaded', '1'); location.reload() }
    throw err
  }))
import { Launcher } from './Launcher'
import { Palette } from './Palette'
import { useStore } from './store'
import { applyTheme } from './theme'
import { useUI } from './ui'

export default function App() {
  const boardId = useStore((s) => s.boardId)
  const settings = useStore((s) => s.board?.settings)
  const toast = useStore((s) => s.toast)
  const say = useStore((s) => s.say)
  const open = useStore((s) => s.open)
  const setUI = useUI((s) => s.set)
  const fileRef = useRef<HTMLInputElement>(null)
  // the Experts page lives at #/experts (DESIGN.md 40)
  // which page without a board: the welcome (''), your boards, or your experts
  const [page, setPage] = useState(location.hash)

  useEffect(() => { applyTheme(settings) }, [settings])

  // The open board lives in the URL, so a refresh keeps your place and the
  // browser's back button walks out of a board the way you'd expect.
  useEffect(() => {
    const fromHash = () => {
      setPage(location.hash)
      const id = location.hash.match(/^#\/b\/([0-9A-Za-z]{26})/)?.[1] ?? null
      const current = useStore.getState().boardId
      if (id && id !== current) void useStore.getState().open(id)
      else if (!id && current) useStore.getState().close()
    }
    fromHash()
    window.addEventListener('hashchange', fromHash)
    return () => window.removeEventListener('hashchange', fromHash)
  }, [])

  useEffect(() => {
    // leaving a board goes back to your boards; the other pages keep their place
    const h = location.hash
    const want = boardId ? `#/b/${boardId}` : (h === '#/experts' || h === '#/boards' || h === '' || h === '#/'
      ? (h === '#/' ? '' : h) : '#/boards')
    if (location.hash !== want) history.replaceState({}, '', location.pathname + want)
    setPage(want)   // replaceState fires no hashchange
  }, [boardId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setUI('paletteOpen', !useUI.getState().paletteOpen)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setUI])

  const pickBundle = useCallback(() => fileRef.current?.click(), [])

  const doImport = async (file: File) => {
    try {
      const result = await api.importBundle(file)
      if (result.kind === 'board') {
        say(`imported "${result.title}" - ${result.node_count} nodes`)
        await open(result.id)
        return
      }
      const { restored, skipped, failed } = result
      const parts = [`restored ${restored.length} board${restored.length === 1 ? '' : 's'}`]
      if (skipped.length) parts.push(`${skipped.length} already here`)
      if (failed.length) parts.push(`${failed.length} failed`)
      say(parts.join(' \u00b7 '), failed.length > 0)
      if (restored.length === 1) await open(restored[0].id)
      else window.dispatchEvent(new CustomEvent('mb:boards-changed'))
    } catch (e: any) {
      say(e.message, true)
    }
  }

  return (
    <>
      {boardId ? <Canvas />
        : page === '#/experts' ? <Suspense fallback={null}><ExpertsPage /></Suspense>
        : page === '#/boards' ? <Launcher onImport={pickBundle} />
        : <Suspense fallback={null}><Welcome /></Suspense>}
      <Palette onImport={pickBundle} />
      <input ref={fileRef} type="file" accept=".mindboard,.zip" hidden
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) void doImport(f)
        }} />
      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.msg}</div>}
    </>
  )
}
