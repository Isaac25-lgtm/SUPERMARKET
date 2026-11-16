// Decorative foliage for the sign-in backdrop: soft leaf silhouettes in two corners.
// Purely ornamental (aria-hidden, no pointer events); colours come from .nature-leaves.
const LEAF = 'M0 0 C 26 -34, 84 -38, 120 0 C 84 38, 26 34, 0 0 Z'
const RIB = 'M4 0 C 40 -2, 80 -2, 116 0'

function Leaf({ x, y, r, s, alt, o }: { x: number; y: number; r: number; s: number; alt?: boolean; o: number }) {
  return (
    <g transform={`translate(${x} ${y}) rotate(${r}) scale(${s})`} className={alt ? 'alt' : undefined} opacity={o}>
      <path d={LEAF} fill="currentColor" />
      <path d={RIB} stroke="white" strokeOpacity="0.35" strokeWidth="1.4" fill="none" />
    </g>
  )
}

function Sprig({ flip }: { flip?: boolean }) {
  return (
    <svg viewBox="0 0 420 420" className="h-full w-full" style={flip ? { transform: 'scale(-1,-1)' } : undefined}>
      <path d="M410 10 C 330 70, 250 150, 190 260 C 160 315, 140 360, 130 410" stroke="currentColor" strokeOpacity="0.35" strokeWidth="2.5" fill="none" />
      <Leaf x={372} y={40} r={150} s={0.9} o={0.22} />
      <Leaf x={330} y={74} r={-60} s={1.05} alt o={0.2} />
      <Leaf x={290} y={118} r={160} s={1.15} o={0.18} />
      <Leaf x={248} y={168} r={-48} s={1.2} alt o={0.17} />
      <Leaf x={215} y={222} r={170} s={1.05} o={0.15} />
      <Leaf x={182} y={282} r={-35} s={0.95} alt o={0.13} />
      <Leaf x={156} y={344} r={185} s={0.8} o={0.11} />
    </svg>
  )
}

export function NatureLeaves() {
  return (
    <div aria-hidden className="nature-leaves pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      <div className="absolute -right-16 -top-10 size-[340px] sm:size-[440px]"><Sprig /></div>
      <div className="absolute -bottom-16 -left-16 size-[300px] sm:size-[400px]"><Sprig flip /></div>
    </div>
  )
}
