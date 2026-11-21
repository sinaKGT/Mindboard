/** One tooltip for the whole interface (DESIGN.md 73), in place of the
 *  browser's own: the plain white box it draws for `title` looked foreign
 *  beside everything else. Every `title` stays where it is written; on first
 *  hover it moves to `data-tip` (so the browser's box never shows), the
 *  element keeps it as its accessible name when it has none, and this draws
 *  it in the app's own surface and theme after a short pause. A shortcut
 *  written after two spaces in brackets - "Ask  (Enter)" - is set apart. */

const DELAY = 450          // ms of resting on an element before it shows
const GAP = 8              // px between the element and the tip

let tip: HTMLDivElement | null = null
let timer = 0
let current: HTMLElement | null = null

function el(): HTMLDivElement {
  if (!tip) {
    tip = document.createElement('div')
    tip.className = 'mb-tip'
    tip.setAttribute('role', 'tooltip')
    document.body.appendChild(tip)
  }
  return tip
}

function hide() {
  window.clearTimeout(timer)
  current = null
  tip?.classList.remove('on')
}

function show(target: HTMLElement) {
  const text = target.dataset.tip
  if (!text || !target.isConnected) return
  const t = el()
  t.replaceChildren()
  const m = text.match(/^([\s\S]*?)\s{2,}\(([^()]+)\)\s*$/)
  t.append(document.createTextNode(m ? m[1] : text))
  if (m) {
    const k = document.createElement('kbd')
    k.textContent = m[2]
    t.append(k)
  }
  t.classList.add('on')
  // below the element, centred; above it when there is no room below;
  // always inside the window
  const r = target.getBoundingClientRect()
  const w = t.offsetWidth, h = t.offsetHeight
  let x = r.left + r.width / 2 - w / 2
  let y = r.bottom + GAP
  if (y + h > window.innerHeight - 4) y = r.top - GAP - h
  x = Math.max(6, Math.min(x, window.innerWidth - w - 6))
  t.style.transform = `translate(${Math.round(x)}px, ${Math.round(Math.max(4, y))}px)`
}

/** Moves a `title` written since the last hover to `data-tip`. */
function adopt(target: HTMLElement) {
  const title = target.getAttribute('title')
  if (title === null) return
  target.removeAttribute('title')
  if (!title.trim()) { delete target.dataset.tip; return }
  target.dataset.tip = title
  if (!target.getAttribute('aria-label') && !target.textContent?.trim()) {
    target.setAttribute('aria-label', title)
  }
}

export function installTooltips() {
  document.addEventListener('pointerover', (e) => {
    if (e.pointerType === 'touch') return
    const found = (e.target as Element | null)?.closest?.('[title], [data-tip]') as HTMLElement | null
    if (!found) return
    adopt(found)
    if (found === current) return
    hide()
    if (!found.dataset.tip) return
    current = found
    timer = window.setTimeout(() => { if (current === found) show(found) }, DELAY)
  }, true)
  document.addEventListener('pointerout', (e) => {
    if (!current) return
    const to = e.relatedTarget as Node | null
    if (to && current.contains(to)) return
    hide()
  }, true)
  // a press, a key, a scroll or a wheel means the person has moved on
  for (const type of ['pointerdown', 'keydown', 'wheel', 'scroll'] as const) {
    window.addEventListener(type, hide, { capture: true, passive: true })
  }
  window.addEventListener('blur', hide)
}
