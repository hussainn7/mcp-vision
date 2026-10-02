import { motion, useReducedMotion } from 'motion/react'
import { useId } from 'react'
import type { Mood } from '../bridge'

const BULB: Record<Mood, string> = {
  idle: '#a9c2ff',
  listening: '#34d399',
  thinking: '#fbbf24',
  speaking: '#f472b6',
  pointing: '#7dd3fc',
  happy: '#a7f3d0',
  error: '#f87171',
}

export interface MascotProps {
  mood?: Mood
  level?: number
  look?: { x: number; y: number }
  lean?: number
  size?: number
  className?: string
  glow?: boolean
}

/**
 * Plip: a soft electric-blue blob with an antenna. Everything is vector so it
 * stays crisp from the 18 px notch glyph to the 160 px onboarding hero.
 */
export function Mascot({ mood = 'idle', level = 0, look = { x: 0, y: 0 }, lean = 0, size = 64, className, glow = true }: MascotProps) {
  const id = useId().replace(/:/g, '')
  const reduce = useReducedMotion()
  const thinking = mood === 'thinking'
  const lookX = thinking ? 0.8 : look.x
  const lookY = thinking ? -0.9 : look.y
  const pupil = { x: Math.max(-1, Math.min(1, lookX)) * 1.7, y: Math.max(-1, Math.min(1, lookY)) * 1.9 }
  const open = Math.max(0, Math.min(1, level))
  const squint = mood === 'error'

  return (
    <motion.div
      className={className}
      style={{ width: size, height: size, position: 'relative' }}
      animate={{ rotate: lean, scale: mood === 'listening' ? 1.06 : 1 }}
      transition={{ type: 'spring', stiffness: 320, damping: 18 }}
    >
      {mood === 'listening' && !reduce && (
        <>
          {[0, 0.6].map((delay) => (
            <span
              key={delay}
              className="absolute inset-0 rounded-full border border-emerald-300/60 animate-pulse-ring"
              style={{ animationDelay: `${delay}s`, transform: `scale(${1 + open * 0.25})` }}
            />
          ))}
        </>
      )}
      <svg
        viewBox="0 0 64 64"
        width={size}
        height={size}
        className={reduce || mood === 'pointing' ? '' : 'animate-float'}
        style={{
          overflow: 'visible',
          filter: glow ? `drop-shadow(0 ${size * 0.06}px ${size * 0.16}px rgba(91,140,255,0.55))` : undefined,
        }}
        aria-label={`Plip is ${mood}`}
        role="img"
      >
        <defs>
          <linearGradient id={`${id}-body`} x1="0.15" y1="0.05" x2="0.9" y2="1">
            <stop offset="0" stopColor={mood === 'error' ? '#ff9a9a' : '#86a9ff'} />
            <stop offset="0.55" stopColor={mood === 'error' ? '#f05d6e' : '#4f74ff'} />
            <stop offset="1" stopColor={mood === 'error' ? '#c2185b' : '#6d4bff'} />
          </linearGradient>
          <radialGradient id={`${id}-shine`} cx="0.32" cy="0.22" r="0.45">
            <stop offset="0" stopColor="#ffffff" stopOpacity="0.75" />
            <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
          </radialGradient>
          <radialGradient id={`${id}-bulb`} cx="0.5" cy="0.5" r="0.5">
            <stop offset="0" stopColor="#ffffff" />
            <stop offset="0.45" stopColor={BULB[mood]} />
            <stop offset="1" stopColor={BULB[mood]} stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* antenna */}
        <path d="M32 11 C32 6 35 4 38 3" stroke="#6f8dff" strokeWidth="2.2" fill="none" strokeLinecap="round" />
        <motion.circle
          cx="38.5"
          cy="2.8"
          r="5"
          fill={`url(#${id}-bulb)`}
          animate={{ opacity: mood === 'thinking' ? [0.5, 1, 0.5] : 1, scale: mood === 'listening' ? 1 + open * 0.5 : 1 }}
          transition={mood === 'thinking' ? { duration: 1.1, repeat: Infinity } : { type: 'spring', stiffness: 400, damping: 20 }}
        />
        <circle cx="38.5" cy="2.8" r="2.1" fill={BULB[mood]} />

        {/* body */}
        <path
          d="M32 9.5 C47.5 9.5 55.5 19.5 55.5 33 C55.5 47.5 45.5 56.5 32 56.5 C18.5 56.5 8.5 47.5 8.5 33 C8.5 19.5 16.5 9.5 32 9.5 Z"
          fill={`url(#${id}-body)`}
        />
        <path
          d="M32 9.5 C47.5 9.5 55.5 19.5 55.5 33 C55.5 47.5 45.5 56.5 32 56.5 C18.5 56.5 8.5 47.5 8.5 33 C8.5 19.5 16.5 9.5 32 9.5 Z"
          fill={`url(#${id}-shine)`}
        />
        <path d="M14 42 C18 50 26 53.5 32 53.5" stroke="#ffffff" strokeOpacity="0.12" strokeWidth="1.6" fill="none" strokeLinecap="round" />

        {/* cheeks */}
        <ellipse cx="18.5" cy="39" rx="3.4" ry="2.1" fill="#ff8fc6" opacity={mood === 'happy' || mood === 'speaking' ? 0.55 : 0.32} />
        <ellipse cx="45.5" cy="39" rx="3.4" ry="2.1" fill="#ff8fc6" opacity={mood === 'happy' || mood === 'speaking' ? 0.55 : 0.32} />

        {/* eyes */}
        {mood === 'happy' ? (
          <g stroke="#0b1022" strokeWidth="2.4" fill="none" strokeLinecap="round">
            <path d="M20 31.5 Q24 26.5 28 31.5" />
            <path d="M36 31.5 Q40 26.5 44 31.5" />
          </g>
        ) : (
          <g className={reduce || squint ? '' : 'animate-blink'} style={{ transformOrigin: '32px 30px', transformBox: 'view-box' }}>
            {[24, 40].map((cx) => (
              <g key={cx}>
                <rect
                  x={cx - 4.4}
                  y={squint ? 28 : mood === 'listening' ? 23.5 : 24.5}
                  width="8.8"
                  height={squint ? 4 : mood === 'listening' ? 13 : 12}
                  rx="4.4"
                  fill="#ffffff"
                />
                {!squint && (
                  <motion.g animate={{ x: pupil.x, y: pupil.y }} transition={{ type: 'spring', stiffness: 260, damping: 18 }}>
                    <circle cx={cx} cy="31" r="2.9" fill="#0b1022" />
                    <circle cx={cx + 1} cy="29.8" r="0.9" fill="#ffffff" />
                  </motion.g>
                )}
              </g>
            ))}
          </g>
        )}

        {/* mouth */}
        {mood === 'speaking' ? (
          <g>
            <ellipse cx="32" cy="42.2" rx={3 + open * 0.8} ry={1.1 + open * 3.2} fill="#0b1022" />
            <ellipse cx="32" cy={43.2 + open * 1.4} rx={1.8} ry={0.6 + open * 0.9} fill="#ff6fa8" />
          </g>
        ) : mood === 'thinking' ? (
          <path d="M29 42.5 Q32 41.2 35 42.5" stroke="#0b1022" strokeWidth="1.8" fill="none" strokeLinecap="round" />
        ) : mood === 'error' ? (
          <path d="M28.5 44 Q32 40.8 35.5 44" stroke="#0b1022" strokeWidth="1.8" fill="none" strokeLinecap="round" />
        ) : mood === 'listening' ? (
          <ellipse cx="32" cy="42.3" rx="2.2" ry="1.7" fill="#0b1022" />
        ) : (
          <path d="M27.5 41 Q32 45.4 36.5 41" stroke="#0b1022" strokeWidth="1.9" fill="none" strokeLinecap="round" />
        )}

        {/* thinking orbit */}
        {thinking && !reduce && (
          <g style={{ transformOrigin: '32px 33px', transformBox: 'view-box' }}>
            {[0, 0.53, 1.06].map((delay) => (
              <circle
                key={delay}
                cx="32"
                cy="33"
                r="1.9"
                fill="#fde68a"
                className="animate-orbit"
                style={{ ['--orbit-r' as string]: '30px', animationDelay: `-${delay}s`, transformOrigin: '32px 33px', transformBox: 'view-box' }}
              />
            ))}
          </g>
        )}
      </svg>
    </motion.div>
  )
}
