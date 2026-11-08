/** An image card, full screen, to be read rather than glanced at.
 *
 *  Opened by a click on the picture (nodes.tsx), closed by Esc, the cross or a
 *  click on the dark around it. It shows the file the card already loaded, so
 *  nothing new is fetched. Scroll zooms around the pointer, dragging moves the
 *  picture, double-click flips between fitted and actual pixels.
 *
 *  While it is open it is modal in fact, not just in looks. The canvas listens
 *  on `window`, so keys pressed here would otherwise reach the board behind:
 *  Delete would remove the selected card, Esc would also leave the group,
 *  Ctrl+X would cut the card. Every key and clipboard event is stopped in the
 *  capture phase, before anything else on the page sees it.
 *
 *  It is ephemeral interface state - never an op, never saved, nothing to undo.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { api } from './api'
import { Icon } from './icons'
import { CLICK_SLOP } from './nodes'
import { useStore } from './store'
import { useUI } from './ui'

/** The double-click that opened the viewer is still one intent. Its second
 *  click lands here, and must neither close the viewer nor zoom it - the same
 *  rule, and the same half second, as a link's cover. */
const SETTLE_MS = 500
const MAX_SCALE = 8
const STEP = 1.25
/** Room kept clear around a fitted picture: the controls above, the caption below. */
const PAD = { x: 40, top: 56, bottom: 76 }

interface View { s: number; x: number; y: number }

export function Viewer() {
  const viewing = useUI((s) => s.viewing)
  const node = useStore((s) => (viewing ? s.nodes[viewing.nodeId] : undefined))
  const boardId = useStore((s) => s.boardId)
  const setUI = useUI((s) => s.set)

  // A card that is gone has nothing left to show; a closed board takes the
  // viewer with it rather than leaving it set for the next board.
  useEffect(() => {
    if (viewing && !node) setUI('viewing', null)
  }, [viewing, node, setUI])
  useEffect(() => () => useUI.getState().set('viewing', null), [])

  const sha = node?.content.sha256 as string | undefined
  if (!viewing || !node || !boardId || !sha) return null
  return (
    <Open key={viewing.nodeId} src={api.assetUrl(boardId, sha)} title={node.title}
      alt={(node.content.alt as string | undefined) || node.title} openedAt={viewing.openedAt} />
  )
}

/** The open picture itself. The canvas opens it for an image card (above); an
 *  answer opens it for a picture it cites (AnswerPictures), with `onClose` and
 *  an `action` of its own - "Show on its board". */
export function Open({ src, title, alt, openedAt, onClose, action }: {
  src: string; title: string; alt: string; openedAt: number
  onClose?: () => void; action?: { label: string; run: () => void }
}) {
  const root = useRef<HTMLDivElement>(null)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const [view, setViewState] = useState<View>({ s: 1, x: 0, y: 0 })
  const [easing, setEasing] = useState(false)
  const [full, setFull] = useState(false)
  const viewRef = useRef(view)
  /** Still showing the fitted view - so a resized window refits it. */
  const fitted = useRef(true)
  const pan = useRef<{ id: number; x: number; y: number; vx: number; vy: number;
    onPicture: boolean; travelled: number } | null>(null)

  const setView = (next: View, isHome = false) => {
    viewRef.current = next
    fitted.current = isHome
    setViewState(next)
  }

  const settled = () => Date.now() - openedAt >= SETTLE_MS

  const close = () => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
    if (onClose) onClose()
    else useUI.getState().set('viewing', null)
  }

  /** Fitted to the window, but never blown up past its own pixels: a small
   *  picture enlarged to fill the screen only looks worse. */
  const home = useCallback((): View | null => {
    const el = root.current
    if (!el || !natural) return null
    const W = el.clientWidth
    const H = el.clientHeight
    const fit = Math.min((W - 2 * PAD.x) / natural.w, (H - PAD.top - PAD.bottom) / natural.h)
    const s = Math.max(0.01, Math.min(fit, 1))
    return {
      s,
      x: (W - natural.w * s) / 2,
      y: PAD.top + (H - PAD.top - PAD.bottom - natural.h * s) / 2,
    }
  }, [natural])

  const zoomAt = (factor: number, cx: number, cy: number, smooth = false) => {
    const v = viewRef.current
    const floor = Math.max(0.01, (home()?.s ?? 1) * 0.5)
    const s = Math.min(MAX_SCALE, Math.max(floor, v.s * factor))
    const k = s / v.s
    const next = { s, x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k }
    if (smooth) glide(next)
    else setView(next)
  }

  /** For the discrete moves - a key, a double-click - the picture glides there.
   *  Scrolling and dragging follow the hand directly. */
  const glide = (next: View, isHome = false) => {
    setEasing(true)
    setView(next, isHome)
    window.setTimeout(() => setEasing(false), 180)
  }

  const toHome = () => { const h = home(); if (h) glide(h, true) }
  const toActual = (cx: number, cy: number) => zoomAt(1 / viewRef.current.s, cx, cy, true)

  const local = (clientX: number, clientY: number) => {
    const box = root.current?.getBoundingClientRect()
    return { x: clientX - (box?.left ?? 0), y: clientY - (box?.top ?? 0) }
  }
  const middle = () => ({
    x: (root.current?.clientWidth ?? 0) / 2, y: (root.current?.clientHeight ?? 0) / 2,
  })
  /** Hit-tested by position: the root holds pointer capture during a drag, so
   *  the browser reports every click and double-click as landing on the root. */
  const onPicture = (px: number, py: number) => {
    const { s, x, y } = viewRef.current
    return !!natural && px >= x && py >= y && px <= x + natural.w * s && py <= y + natural.h * s
  }

  // land fitted, the moment the picture's size is known
  useLayoutEffect(() => {
    const h = home()
    if (h) setView(h, true)
  }, [home])

  // A window that changes size - full screen, a resized browser - refits a
  // fitted picture, and keeps a zoomed one where it was against the middle.
  useEffect(() => {
    const el = root.current
    if (!el || !natural) return
    let last = { w: el.clientWidth, h: el.clientHeight }
    const ro = new ResizeObserver(() => {
      const now = { w: el.clientWidth, h: el.clientHeight }
      if (now.w === last.w && now.h === last.h) return
      const h = home()
      const v = viewRef.current
      if (fitted.current && h) setView(h, true)
      else setView({ ...v, x: v.x + (now.w - last.w) / 2, y: v.y + (now.h - last.h) / 2 })
      last = now
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [natural, home])

  // The latest handlers, for listeners registered once.
  const act = useRef({ zoomAt, close, toHome, toActual, middle, local })
  act.current = { zoomAt, close, toHome, toActual, middle, local }

  // The wheel has to be a native, non-passive listener: React's is passive,
  // and without preventDefault a trackpad pinch (ctrl+wheel) zooms the page.
  useEffect(() => {
    const el = root.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const px = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY
      const at = act.current.local(e.clientX, e.clientY)
      act.current.zoomAt(Math.exp(-px * (e.ctrlKey ? 0.01 : 0.0015)), at.x, at.y)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement === root.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  const toggleFull = () => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
    else void root.current?.requestFullscreen().catch(() => {})
  }

  // Modal for the keyboard and the clipboard: see the note at the top.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      e.stopPropagation()
      if (e.ctrlKey || e.metaKey || e.altKey) return     // the browser's own keys still work
      const a = act.current
      const c = a.middle()
      switch (e.key) {
        case 'Escape': e.preventDefault(); a.close(); break
        case 'f': case 'F': e.preventDefault(); toggleFull(); break
        case '0': e.preventDefault(); a.toHome(); break
        case '1': e.preventDefault(); a.toActual(c.x, c.y); break
        case '+': case '=': e.preventDefault(); a.zoomAt(STEP, c.x, c.y, true); break
        case '-': case '_': e.preventDefault(); a.zoomAt(1 / STEP, c.x, c.y, true); break
      }
    }
    const swallow = (e: Event) => e.stopPropagation()
    const kinds = ['keyup', 'copy', 'cut', 'paste'] as const
    window.addEventListener('keydown', onKey, true)
    kinds.forEach((k) => window.addEventListener(k, swallow, true))
    root.current?.focus()
    return () => {
      window.removeEventListener('keydown', onKey, true)
      kinds.forEach((k) => window.removeEventListener(k, swallow, true))
    }
  }, [])

  // ----------------------------------------------------------- pointer --
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    const at = local(e.clientX, e.clientY)
    const v = viewRef.current
    pan.current = {
      id: e.pointerId, x: e.clientX, y: e.clientY, vx: v.x, vy: v.y,
      onPicture: onPicture(at.x, at.y), travelled: 0,
    }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const p = pan.current
    if (!p || p.id !== e.pointerId) return
    const dx = e.clientX - p.x
    const dy = e.clientY - p.y
    p.travelled = Math.max(p.travelled, Math.hypot(dx, dy))
    if (p.travelled < CLICK_SLOP) return
    setView({ ...viewRef.current, x: p.vx + dx, y: p.vy + dy })
  }
  const onPointerUp = (e: React.PointerEvent) => {
    const p = pan.current
    pan.current = null
    if (!p || p.id !== e.pointerId) return
    // a click on the dark around the picture closes it; a drag that happens to
    // end there does not
    if (!p.onPicture && p.travelled < CLICK_SLOP && settled()) close()
  }
  const onDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    const at = local(e.clientX, e.clientY)
    if (!settled() || !onPicture(at.x, at.y)) return
    const h = home()
    if (!h) return
    if (Math.abs(viewRef.current.s - h.s) > 0.01) { toHome(); return }
    // fitted: go to its own pixels - or, for a picture already shown at its
    // own pixels, twice that
    if (h.s < 0.99) toActual(at.x, at.y)
    else zoomAt(2, at.x, at.y, true)
  }

  const { s, x, y } = view
  return (
    <div ref={root} className={`viewer${easing ? ' easing' : ''}`} role="dialog" aria-modal="true"
      aria-label={title || 'Image'} tabIndex={-1}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
      onPointerCancel={() => { pan.current = null }}
      onDoubleClick={onDoubleClick}
      // a file dropped here must not make the browser navigate away to it
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'none' }}
      onDrop={(e) => e.preventDefault()}>
      <img className={`viewer-img${s >= 3 ? ' pixels' : ''}`} src={src} alt={alt}
        draggable={false}
        onLoad={(e) => setNatural({
          w: e.currentTarget.naturalWidth || 1, h: e.currentTarget.naturalHeight || 1 })}
        style={natural
          ? { width: natural.w * s, height: natural.h * s, transform: `translate(${x}px, ${y}px)` }
          : { visibility: 'hidden' }} />

      <div className="viewer-bar" onPointerDown={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}>
        <button className="viewer-zoom" title="Fit to the window  (0)" onClick={toHome}>
          {Math.round(s * 100)}%
        </button>
        <button className="viewer-btn" title={full ? 'Leave full screen  (F)' : 'Full screen  (F)'}
          onClick={toggleFull}>
          <Icon name={full ? 'shrink' : 'fit'} size={15} />
        </button>
        <button className="viewer-btn" title="Close  (Esc)" onClick={close}>
          <Icon name="x" size={15} />
        </button>
      </div>

      <div className="viewer-caption">
        {title ? <div className="viewer-title">{title}</div> : null}
        {action ? (
          <button className="viewer-action" onPointerDown={(e) => e.stopPropagation()}
            onClick={() => { close(); action.run() }}>{action.label}</button>
        ) : null}
        <div className="viewer-hint">
          scroll to zoom · drag to move · double-click for actual size · F full screen · Esc to close
        </div>
      </div>
    </div>
  )
}
