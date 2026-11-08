/** The first page (DESIGN.md 43): two signs in sand - knowledge for your
 *  experts, writing for your boards. Nothing else to read or learn. */
import { useEffect, useRef } from 'react'

import './experts.css'
import { WelcomeScene } from './welcomeScene'

export function Welcome() {
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!host.current) return
    const scene = new WelcomeScene(host.current, (door) => { location.hash = door === 'boards' ? '#/boards' : '#/experts' })
    return () => scene.dispose()
  }, [])
  return (
    <div className="xp wl" ref={host}>
      <div className="wl-title">
        <div className="wl-name">Mindboard</div>
        <div className="wl-line">Write what you learn. Find your own gaps.</div>
      </div>
    </div>
  )
}
