/** The keys, in the corner, so nobody has to remember them.
 *
 *  Deliberately not a menu: it says what the canvas can do and does nothing
 *  itself. It folds away to a chip and remembers that you folded it.
 */
import { useEffect, useState } from 'react'

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
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem('mb_keys') !== 'off' } catch { return true }
  })
  useEffect(() => {
    try { localStorage.setItem('mb_keys', open ? 'on' : 'off') } catch { /* private mode */ }
  }, [open])

  if (!open) {
    return (
      <button className="keys-chip" title="Show the shortcuts" onClick={() => setOpen(true)}>
        <Icon name="keys" size={13} /> Keys
      </button>
    )
  }

  return (
    <div className="keys">
      <div className="keys-head">
        <span>Shortcuts</span>
        <button title="Hide these" onClick={() => setOpen(false)}>
          <Icon name="x" size={11} />
        </button>
      </div>
      <dl>
        {KEYS.map(({ keys, what }) => (
          <div className="keys-row" key={what}>
            <dt>{keys.map((k) => <kbd key={k}>{k}</kbd>)}</dt>
            <dd>{what}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
