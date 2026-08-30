const P = (d: string, extra?: Record<string, any>) => ({ d, ...extra })

const PATHS: Record<string, ReturnType<typeof P>[]> = {
  plus: [P('M8 3.5v9M3.5 8h9')],
  trash: [P('M3 4.5h10M6.5 4.5V3h3v1.5M5 4.5l.6 8.2a1 1 0 0 0 1 .8h2.8a1 1 0 0 0 1-.8L11 4.5')],
  branch: [P('M4 4v3.5a2 2 0 0 0 2 2h6'), P('M9.5 7l2.5 2.5-2.5 2.5')],
  enter: [P('M6 3.5h4.5A1.5 1.5 0 0 1 12 5v6a1.5 1.5 0 0 1-1.5 1.5H6'), P('M3.5 8h5M6.5 6l2 2-2 2')],
  up: [P('M4 10l4-4 4 4')],
  down: [P('M4 6l4 4 4 4'.replace('4 4 4 4', '4 4 4-4'))],
  search: [P('M7.2 11.4a4.2 4.2 0 1 0 0-8.4 4.2 4.2 0 0 0 0 8.4ZM10.4 10.4 13.5 13.5')],
  upload: [P('M8 11V3.5M5 6l3-3 3 3'), P('M3 11.5v1A1.5 1.5 0 0 0 4.5 14h7a1.5 1.5 0 0 0 1.5-1.5v-1')],
  download: [P('M8 3v7.5M5 8l3 3 3-3'), P('M3 12.5v.5A1.5 1.5 0 0 0 4.5 14.5h7A1.5 1.5 0 0 0 13 13v-.5')],
  palette: [P('M8 13.5a5.5 5.5 0 1 1 5.5-5.5c0 1.4-1.2 2-2.2 2H10a1.2 1.2 0 0 0-.9 2c.3.4.2 1.5-1.1 1.5Z'), P('M5.6 6.4h.01M8 5h.01M10.4 6.6h.01')],
  grid: [P('M2.5 2.5h4.2v4.2H2.5zM9.3 2.5h4.2v4.2H9.3zM2.5 9.3h4.2v4.2H2.5zM9.3 9.3h4.2v4.2H9.3z')],
  fit: [P('M3 6V3.5h2.5M13 6V3.5h-2.5M3 10v2.5h2.5M13 10v2.5h-2.5')],
  minus: [P('M3.5 8h9')],
  link: [P('M6.5 9.5 9.5 6.5'), P('M7.5 4.8 9 3.3a2.4 2.4 0 0 1 3.4 3.4l-1.5 1.5'), P('M8.5 11.2 7 12.7a2.4 2.4 0 0 1-3.4-3.4l1.5-1.5')],
  undo: [P('M6 5.5 3.5 8 6 10.5'), P('M3.5 8h6A3 3 0 0 1 12.5 11v.5')],
  x: [P('M4 4l8 8M12 4l-8 8')],
  chevron: [P('M6 4l4 4-4 4')],
  layers: [P('M8 2.5 14 6l-6 3.5L2 6l6-3.5Z'), P('M2.5 9.5 8 12.7l5.5-3.2')],
  type: [P('M4 4.5h8M8 4.5v8')],
  bullets: [P('M6.5 4h7M6.5 8h7M6.5 12h7'), P('M3.3 4h.01M3.3 8h.01M3.3 12h.01',
    {strokeWidth: 2.2})],
  numbers: [P('M6.5 4h7M6.5 8h7M6.5 12h7'),
    P('M2.4 5.6V2.9l-.9.6'), P('M1.6 7.6h1.7L1.6 9.9h1.8'), P('M1.6 11.4h1.7v1.1H2.3h1v1.1H1.6')],
  image: [P('M2.5 3.5h11v9h-11z'), P('M2.5 10.5l3-3 2.5 2.5 2-2 3.5 3.5'), P('M6 6.2h.01')],
  copy: [P('M6 6h7.5v7.5H6z'), P('M10.5 6V3.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1H6')],
  keys: [P('M2 4.5h12v7H2z'), P('M4.6 7h.01M7.3 7h.01M10 7h.01M12.7 7h.01M5.4 9.4h5.2',
    {strokeWidth: 1.6})],
  external: [P('M9 3.5h3.5V7'), P('M12.5 3.5 7.5 8.5'), P('M11 9.5v2.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h2.5')],
}

export function Icon({ name, size = 15 }: { name: keyof typeof PATHS | string; size?: number }) {
  const paths = PATHS[name] ?? []
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      {paths.map((p, i) => <path key={i} {...p} />)}
    </svg>
  )
}
