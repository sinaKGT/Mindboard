/** Mindboard's mark (DESIGN.md 59): an ankh drawn as a small graph - its
 *  teardrop loop is the mind, and its arms and foot end in nodes, cards linked on a board.
 *  The Experts' ankh and the boards' graphs in one sign. It takes the colour
 *  it is given, so on the first page it follows the page's theme; the browser
 *  tab uses the same drawing in the Experts' gold (public/favicon.svg). */
export function Logo({ size = 26, className = '' }: { size?: number; className?: string }) {
  return (
    <svg className={`mb-logo ${className}`} width={size} height={size} viewBox="0 0 32 32"
      role="img" aria-label="Mindboard">
      <g fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round">
        <path className="mb-logo-loop" d="M16 16.2C10.6 12.6 10.8 4 16 4s5.4 8.6 0 12.2Z" strokeLinejoin="round" />
        <path className="mb-logo-arms" d="M7 19.5H25M16 16.2v9.3" />
      </g>
      <g fill="currentColor" className="mb-logo-nodes">
        <circle cx="7" cy="19.5" r="2.9" />
        <circle cx="25" cy="19.5" r="2.9" />
        <circle cx="16" cy="26.5" r="2.9" />
      </g>
    </svg>
  )
}
