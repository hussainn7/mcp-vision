import { useId } from 'react'
import { cn } from './bits'

// The AIs Plip connects to: their real marks, drawn our way (soft tile, top light, a little glow).
const TILES: Record<string, string> = {
  'claude-code': 'bg-gradient-to-b from-[#2a1d18] to-[#1a1210] shadow-[inset_0_0_0_1px_rgba(217,119,87,0.35)]',
  anthropic: 'bg-gradient-to-b from-[#f4ece2] to-[#e3d6c6] shadow-[inset_0_0_0_1px_rgba(0,0,0,0.08)]',
  codex: 'bg-gradient-to-b from-[#fafafa] to-[#e8e8e8] shadow-[inset_0_0_0_1px_rgba(0,0,0,0.08)]',
  cursor: 'bg-gradient-to-b from-[#1c1c20] to-[#0c0c0e] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.1)]',
  gemini: 'bg-gradient-to-b from-[#16182a] to-[#0d0e19] shadow-[inset_0_0_0_1px_rgba(145,119,199,0.35)]',
  'gemini-api': 'bg-gradient-to-b from-[#fbfbfd] to-[#eceef3] shadow-[inset_0_0_0_1px_rgba(0,0,0,0.06)]',
}

export function EngineMark({ id, size = 40, className }: { id: string; size?: number; className?: string }) {
  return (
    <span
      className={cn('relative grid shrink-0 place-items-center overflow-hidden rounded-[30%] shadow-lg', TILES[id] ?? 'bg-white/10', className)}
      style={{ width: size, height: size }}
    >
      <span className="pointer-events-none absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-white/15 to-transparent" />
      <Mark id={id} size={Math.round(size * 0.58)} />
    </span>
  )
}

function Mark({ id, size }: { id: string; size: number }) {
  const gradient = useId()
  const svg = (children: React.ReactNode) => (
    <svg viewBox="0 0 24 24" width={size} height={size} className="relative drop-shadow-[0_1px_1px_rgba(0,0,0,0.25)]" aria-hidden>{children}</svg>
  )
  switch (id) {
    case 'claude-code':                               // Claude's spark: uneven rounded rays
      return svg(
        <g stroke="#e0835e" strokeLinecap="round">
          {[9.6, 8.2, 10.2, 7.6, 9.4, 8.6, 10, 7.8, 9.2, 8.8, 9.8, 8].map((length, ray) => {
            const angle = (ray * 30 + 8) * (Math.PI / 180)
            return <line key={ray} x1={12 + Math.cos(angle) * 2} y1={12 + Math.sin(angle) * 2} x2={12 + Math.cos(angle) * length}
              y2={12 + Math.sin(angle) * length} strokeWidth={ray % 2 ? 2.1 : 2.6} />
          })}
        </g>,
      )
    case 'anthropic':                                 // Anthropic's A\
      return svg(
        <g fill="#191919" transform="translate(-1.3 0)">
          <path fillRule="evenodd" d="M2.5 20 8.7 4H12l6.2 16h-3.4l-1.3-3.5H7.2L5.9 20zm5.7-6.4h4.3l-2.15-5.7z" />
          <path d="M13.7 4H17l6.2 16h-3.3z" />
        </g>,
      )
    case 'codex':                                     // OpenAI's knot: three loops at 60°
      return svg(
        <g fill="none" stroke="#0d0d0d" strokeWidth="1.9">
          {[0, 60, 120].map((turn) => (
            <rect key={turn} x="8.4" y="2.6" width="7.2" height="18.8" rx="3.6" transform={`rotate(${turn} 12 12) translate(1.15 0)`} />
          ))}
        </g>,
      )
    case 'cursor':                                    // Cursor's cube
      return svg(
        <g strokeLinejoin="round">
          <path d="M12 2.8 20 7.4 12 12 4 7.4z" fill="#f2f2f2" />
          <path d="M4 7.4 12 12v9.2l-8-4.6z" fill="#8c8c92" />
          <path d="M20 7.4 12 12v9.2l8-4.6z" fill="#3a3a40" />
          <path d="M12 2.8 20 7.4v9.2L12 21.2 4 16.6V7.4z" fill="none" stroke="rgba(255,255,255,0.25)" strokeWidth="0.6" />
        </g>,
      )
    case 'gemini':                                    // Gemini's star, its blue-to-violet
      return svg(
        <>
          <defs>
            <linearGradient id={gradient} x1="4" y1="20" x2="20" y2="4" gradientUnits="userSpaceOnUse">
              <stop stopColor="#3f8cff" />
              <stop offset="0.55" stopColor="#9177c7" />
              <stop offset="1" stopColor="#d96570" />
            </linearGradient>
          </defs>
          <path d="M12 1.8C12 7.4 16.6 12 22.2 12 16.6 12 12 16.6 12 22.2 12 16.6 7.4 12 1.8 12 7.4 12 12 7.4 12 1.8z" fill={`url(#${gradient})`} />
        </>,
      )
    case 'gemini-api':                                // the free key: Gemini's star in Google's four colors
      return svg(
        <>
          <defs>
            {[['t', '0,0 24,0 12,12'], ['r', '24,0 24,24 12,12'], ['b', '24,24 0,24 12,12'], ['l', '0,24 0,0 12,12']].map(([side, points]) => (
              <clipPath key={side} id={`${gradient}${side}`}><polygon points={points} /></clipPath>
            ))}
          </defs>
          {[['t', '#4285f4'], ['r', '#ea4335'], ['b', '#fbbc04'], ['l', '#34a853']].map(([side, color]) => (
            <path key={side} clipPath={`url(#${gradient}${side})`} fill={color}
              d="M12 1.8C12 7.4 16.6 12 22.2 12 16.6 12 12 16.6 12 22.2 12 16.6 7.4 12 1.8 12 7.4 12 12 7.4 12 1.8z" />
          ))}
        </>,
      )
    default:
      return svg(<circle cx="12" cy="12" r="5" fill="currentColor" />)
  }
}
