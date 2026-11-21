/** The keys, behind one round button in the bottom-left corner.
 *
 *  Deliberately not a menu: it says what the canvas can do and does nothing
 *  itself. It used to sit open in the corner; now it is a single keyboard
 *  button, and the list opens above it on a click and closes on another click,
 *  a click anywhere else, or Esc.
 */
import { useEffect, useRef, useState } from 'react'

import { Icon } from './icons'

const MAC = typeof navigator !== 'undefined' && /mac/i.test(navigator.platform)
const MOD = MAC ? '⌘' : 'Ctrl'
const ALT = MAC ? '⌥' : 'Alt'

const KEYS: { keys: string[]; what: string }[] = [
  { keys: ['a'], what: 'new card where you point' },
  { keys: ['c'], what: 'connect it, then click a card' },
  { keys: ['drag'], what: 'move a card' },
  { keys: ['click a picture or PDF'], what: 'open it' },
  { keys: [ALT, 'drag'], what: 'move it with its links' },
  { keys: ['drop on a group'], what: 'put the card inside' },
  { keys: [MOD, 'G'], what: 'group what is selected' },
  { keys: [MOD, 'C / V / D'], what: 'copy, paste, duplicate' },
  { keys: ['Del'], what: 'delete what is selected' },
  { keys: [MOD, 'Z'], what: 'undo' },
  { keys: [MOD, 'J'], what: 'ask your board' },
  { keys: [MOD, 'K'], what: 'everything else' },
]

export function Shortcuts() {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  // a click anywhere else, or Esc, closes it
  useEffect(() => {
    if (!open) return
    const away = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('pointerdown', away, true)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('pointerdown', away, true)
      window.removeEventListener('keydown', esc)
    }
  }, [open])

  return (
    <div ref={root} className="keys-corner">
      {open ? (
        <div className="keys" role="dialog" aria-label="Shortcuts">
          <div className="keys-head"><span>Shortcuts</span></div>
          <dl>
            {KEYS.map(({ keys, what }) => (
              <div className="keys-row" key={what}>
                <dt>{keys.map((k) => <kbd key={k}>{k}</kbd>)}</dt>
                <dd>{what}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      <button className={`keys-btn ${open ? 'on' : ''}`} aria-expanded={open}
        title={open ? 'Hide the shortcuts' : 'Shortcuts'} onClick={() => setOpen((o) => !o)}>
        <Icon name={open ? 'x' : 'keys'} size={16} />
      </button>
    </div>
  )
}
