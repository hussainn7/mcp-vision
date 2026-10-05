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

const BODY: Record<'plip' | 'error', [string, string, string]> = {
  plip: ['#a9c9fb', '#4f7ff0', '#2c58e6'],
  error: ['#fecdd3', '#fb7185', '#be123c'],
}

// Traced from the app icon: a blue hood curling over a soft white face with one pill eye.
const HOOD = 'M15 60 C9 57 8 52 8.2 46 C8.5 30 22 4 37 4.1 C42 4 45.5 6.5 47.3 9.4 C51 14 54.5 21 55.5 27.1 C56.5 34 52 40 45.9 43.5 C40 47 31 48.5 25.9 50.6 C22 53 19 61 15 60 Z'
const FACE = 'M25.9 50.6 C23.5 46 22.5 40 24 33 C25.5 25 27.5 18 31.5 14.5 C36 10.5 42 9.5 47.3 9.4 C51 14 54.5 21 55.5 27.1 C56.5 34 52 40 45.9 43.5 C40 47 31 48.5 25.9 50.6 Z'
const EYE = { x: 43.3, y: 25.4 }

/**
 * Plip, as on the app icon. It leans toward what it's pointing at, squashes
 * while it talks, and its one eye follows `look`. All vector, so it reads at
 * 18 px in the notch and 160 px on the welcome screen.
 */
export function Mascot({ mood = 'idle', level = 0, look = { x: 0, y: 0 }, lean = 0, size = 64, className, glow = true }: MascotProps) {
  const id = useId().replace(/:/g, '')
  const reduce = useReducedMotion()
  const open = Math.max(0, Math.min(1, level))
  const [light, mid, deep] = BODY[mood === 'error' ? 'error' : 'plip']
  const pointing = mood === 'pointing'
  // The hood's tip is "up", i.e. -90°.
  const magnitude = Math.hypot(look.x, look.y)
  const aim = pointing && magnitude > 0.2 ? (Math.atan2(look.y, look.x) * 180) / Math.PI + 90 : 0
  const pupil = mood === 'thinking' ? { x: 1.4, y: -1.8 } : { x: Math.max(-1, Math.min(1, look.x)) * 2.2, y: Math.max(-1, Math.min(1, look.y)) * 1.8 }

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
        style={{ overflow: 'visible', filter: glow ? `drop-shadow(0 ${size * 0.05}px ${size * 0.14}px rgba(79,127,240,0.5))` : undefined }}
        role="img"
        aria-label={`Plip is ${mood}`}
      >
        <defs>
          <linearGradient id={`${id}-body`} x1="0.7" y1="0" x2="0.2" y2="1">
            <stop offset="0" stopColor={light} />
            <stop offset="0.45" stopColor={mid} />
            <stop offset="1" stopColor={deep} />
          </linearGradient>
          <radialGradient id={`${id}-face`} cx="0.62" cy="0.5" r="0.62">
            <stop offset="0" stopColor="#ffffff" />
            <stop offset="0.55" stopColor="#e8effc" />
            <stop offset="1" stopColor={mood === 'error' ? '#fecdd3' : '#a9c6f6'} />
          </radialGradient>
        </defs>

        {/* listening ripples */}
        {mood === 'listening' && !reduce &&
          [0, 0.7].map((delay) => (
            <motion.ellipse
              key={delay}
              cx="30"
              cy="60"
              rx="14"
              ry="3.4"
              fill="none"
              stroke="#7aa2f7"
              strokeWidth="1.4"
              initial={{ opacity: 0.6, scale: 0.8 }}
              animate={{ opacity: 0, scale: 1.9 + open }}
              transition={{ duration: 1.6, repeat: Infinity, delay, ease: 'easeOut' }}
              style={{ transformOrigin: '30px 60px', transformBox: 'view-box' }}
            />
          ))}

        <motion.g
          animate={mood === 'speaking' && !reduce ? { scaleY: [1, 1 - open * 0.06, 1], scaleX: [1, 1 + open * 0.05, 1] } : { scaleX: 1, scaleY: 1 }}
          transition={{ duration: 0.32, repeat: mood === 'speaking' ? Infinity : 0 }}
          style={{ transformOrigin: '30px 60px', transformBox: 'view-box' }}
        >
          <path d={HOOD} fill={`url(#${id}-body)`} />
          <path d={FACE} fill={`url(#${id}-face)`} />

          {/* eye */}
          {mood === 'happy' ? (
            <path d={`M${EYE.x - 3.2} ${EYE.y + 1.5} Q${EYE.x} ${EYE.y - 3.5} ${EYE.x + 3.2} ${EYE.y + 1.5}`} stroke="#161b22" strokeWidth="2.6" fill="none" strokeLinecap="round" />
          ) : mood === 'error' ? (
            <rect x={EYE.x - 3} y={EYE.y - 1.2} width="6" height="2.6" rx="1.3" fill="#161b22" />
          ) : (
            <g className={reduce ? '' : 'animate-blink'} style={{ transformOrigin: `${EYE.x}px ${EYE.y}px`, transformBox: 'view-box' }}>
              <motion.g animate={{ x: pupil.x, y: pupil.y }} transition={{ type: 'spring', stiffness: 260, damping: 18 }}>
                <rect x={EYE.x - 2.95} y={EYE.y - 5.45} width="5.9" height="10.9" rx="2.95" fill="#161b22" />
              </motion.g>
            </g>
          )}
        </motion.g>

        {/* thinking bubbles */}
        {mood === 'thinking' && !reduce &&
          [0, 0.45, 0.9].map((delay, index) => (
            <motion.circle
              key={delay}
              cx={50 + index * 3}
              cy="22"
              r={1.6 + index * 0.6}
              fill="#a9c9fb"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: [0, 0.9, 0], y: [8, -6, -14] }}
              transition={{ duration: 1.5, repeat: Infinity, delay }}
            />
          ))}
      </svg>
    </motion.div>
  )
}
