import { motion, useReducedMotion } from 'motion/react'
import { useId } from 'react'
import type { Mood } from '../bridge'

export interface MascotProps {
  mood?: Mood
  level?: number
  look?: { x: number; y: number }
  lean?: number
  size?: number
  className?: string
  glow?: boolean
}

const BODY: Record<'water' | 'error', [string, string, string]> = {
  water: ['#cffafe', '#22d3ee', '#0369a1'],
  error: ['#fecdd3', '#fb7185', '#be123c'],
}

/**
 * Plip is a droplet. The tip leads while it flies and points at what it's
 * talking about when it lands; otherwise it sits upright and wobbles a little.
 * All vector, so it reads at 18 px in the notch and 160 px on the welcome screen.
 */
export function Mascot({ mood = 'idle', level = 0, look = { x: 0, y: 0 }, lean = 0, size = 64, className, glow = true }: MascotProps) {
  const id = useId().replace(/:/g, '')
  const reduce = useReducedMotion()
  const open = Math.max(0, Math.min(1, level))
  const [light, mid, deep] = BODY[mood === 'error' ? 'error' : 'water']
  const pointing = mood === 'pointing'
  // Tip points along `look` while pointing (the tip is "up", i.e. -90°).
  const magnitude = Math.hypot(look.x, look.y)
  const aim = pointing && magnitude > 0.2 ? (Math.atan2(look.y, look.x) * 180) / Math.PI + 90 : 0
  const pupils = mood === 'thinking' ? { x: 1.2, y: -1.6 } : { x: Math.max(-1, Math.min(1, look.x)) * 1.6, y: Math.max(-1, Math.min(1, look.y)) * 1.4 }

  return (
    <motion.div
      className={className}
      style={{ width: size, height: size, position: 'relative' }}
      animate={{ rotate: aim + lean * 0.4, scale: mood === 'listening' ? 1 + open * 0.08 : 1 }}
      transition={{ type: 'spring', stiffness: 300, damping: 20 }}
    >
      <svg
        viewBox="0 0 64 64"
        width={size}
        height={size}
        className={reduce || pointing ? '' : 'animate-float'}
        style={{ overflow: 'visible', filter: glow ? `drop-shadow(0 ${size * 0.05}px ${size * 0.14}px rgba(34,211,238,0.5))` : undefined }}
        role="img"
        aria-label={`Plip is ${mood}`}
      >
        <defs>
          <linearGradient id={`${id}-body`} x1="0.2" y1="0" x2="0.8" y2="1">
            <stop offset="0" stopColor={light} />
            <stop offset="0.5" stopColor={mid} />
            <stop offset="1" stopColor={deep} />
          </linearGradient>
          <radialGradient id={`${id}-depth`} cx="0.5" cy="0.78" r="0.55">
            <stop offset="0" stopColor="#ffffff" stopOpacity="0.28" />
            <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* listening ripples */}
        {mood === 'listening' && !reduce &&
          [0, 0.7].map((delay) => (
            <motion.ellipse
              key={delay}
              cx="32"
              cy="60"
              rx="14"
              ry="3.4"
              fill="none"
              stroke="#5eead4"
              strokeWidth="1.4"
              initial={{ opacity: 0.6, scale: 0.8 }}
              animate={{ opacity: 0, scale: 1.9 + open }}
              transition={{ duration: 1.6, repeat: Infinity, delay, ease: 'easeOut' }}
              style={{ transformOrigin: '32px 60px', transformBox: 'view-box' }}
            />
          ))}

        {/* body */}
        <motion.path
          d="M32 3.5 C32 3.5 11 25.5 11 40.5 C11 52 20.4 60.5 32 60.5 C43.6 60.5 53 52 53 40.5 C53 25.5 32 3.5 32 3.5 Z"
          fill={`url(#${id}-body)`}
          animate={mood === 'speaking' && !reduce ? { scaleY: [1, 1 - open * 0.06, 1], scaleX: [1, 1 + open * 0.05, 1] } : { scaleX: 1, scaleY: 1 }}
          transition={{ duration: 0.32, repeat: mood === 'speaking' ? Infinity : 0 }}
          style={{ transformOrigin: '32px 60px', transformBox: 'view-box' }}
        />
        <path d="M32 3.5 C32 3.5 11 25.5 11 40.5 C11 52 20.4 60.5 32 60.5 C43.6 60.5 53 52 53 40.5 C53 25.5 32 3.5 32 3.5 Z" fill={`url(#${id}-depth)`} />
        {/* specular highlights */}
        <ellipse cx="22.5" cy="33" rx="3.2" ry="6.4" fill="#ffffff" opacity="0.55" transform="rotate(28 22.5 33)" />
        <circle cx="25.5" cy="24.5" r="1.6" fill="#ffffff" opacity="0.7" />

        {/* cheeks */}
        <ellipse cx="20" cy="47" rx="3.2" ry="1.9" fill="#f9a8d4" opacity={mood === 'happy' || mood === 'speaking' ? 0.6 : 0.35} />
        <ellipse cx="44" cy="47" rx="3.2" ry="1.9" fill="#f9a8d4" opacity={mood === 'happy' || mood === 'speaking' ? 0.6 : 0.35} />

        {/* eyes */}
        {mood === 'happy' ? (
          <g stroke="#082f49" strokeWidth="2.4" fill="none" strokeLinecap="round">
            <path d="M22.5 41 Q26 36.8 29.5 41" />
            <path d="M34.5 41 Q38 36.8 41.5 41" />
          </g>
        ) : (
          <g className={reduce || mood === 'error' ? '' : 'animate-blink'} style={{ transformOrigin: '32px 40px', transformBox: 'view-box' }}>
            {[26, 38].map((cx) => (
              <g key={cx}>
                <rect x={cx - 3.6} y={mood === 'error' ? 38.5 : 34.5} width="7.2" height={mood === 'error' ? 3.6 : 10.5} rx="3.6" fill="#ffffff" />
                {mood !== 'error' && (
                  <motion.g animate={{ x: pupils.x, y: pupils.y }} transition={{ type: 'spring', stiffness: 260, damping: 18 }}>
                    <circle cx={cx} cy="40.3" r="2.5" fill="#082f49" />
                    <circle cx={cx + 0.9} cy="39.2" r="0.8" fill="#ffffff" />
                  </motion.g>
                )}
              </g>
            ))}
          </g>
        )}

        {/* mouth */}
        {mood === 'speaking' ? (
          <ellipse cx="32" cy="50.4" rx={2.6 + open * 0.8} ry={0.9 + open * 2.6} fill="#082f49" />
        ) : mood === 'error' ? (
          <path d="M28.5 52 Q32 49 35.5 52" stroke="#082f49" strokeWidth="1.8" fill="none" strokeLinecap="round" />
        ) : mood === 'thinking' ? (
          <path d="M29.5 50.5 Q32 49.6 34.5 50.5" stroke="#082f49" strokeWidth="1.7" fill="none" strokeLinecap="round" />
        ) : mood === 'listening' ? (
          <ellipse cx="32" cy="50.5" rx="1.9" ry="1.5" fill="#082f49" />
        ) : (
          <path d="M28 49.2 Q32 53 36 49.2" stroke="#082f49" strokeWidth="1.8" fill="none" strokeLinecap="round" />
        )}

        {/* thinking bubbles */}
        {mood === 'thinking' && !reduce &&
          [0, 0.45, 0.9].map((delay, index) => (
            <motion.circle
              key={delay}
              cx={50 + index * 3}
              cy="22"
              r={1.6 + index * 0.6}
              fill="#a5f3fc"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: [0, 0.9, 0], y: [8, -6, -14] }}
              transition={{ duration: 1.5, repeat: Infinity, delay }}
            />
          ))}
      </svg>
    </motion.div>
  )
}
