import { AnimatePresence, motion } from 'motion/react'
import { AlertTriangle, Clock3, Settings2, Sparkles } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { island, send, useStore, type Mood, type Phase } from '../bridge'
import { Mascot } from '../components/Mascot'
import { EngineBadge, Keycap, StepChips, StreamingText, Waveform, cn } from '../components/bits'

type Mode = 'hidden' | 'compact' | 'peek' | Exclude<Phase, 'idle'>

const MOOD: Record<Mode, Mood> = {
  hidden: 'idle',
  compact: 'idle',
  peek: 'happy',
  listening: 'listening',
  thinking: 'thinking',
  answering: 'speaking',
  error: 'error',
}

const SPRING = { type: 'spring', stiffness: 420, damping: 34, mass: 0.9 } as const
const LINGER_MS = 7000

/** Concave "shoulder" that makes the island read as part of the notch. */
function Shoulder({ side, size }: { side: 'left' | 'right'; size: number }) {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute top-0"
      style={{
        [side]: -size,
        width: size,
        height: size,
        background: `radial-gradient(circle at bottom ${side === 'left' ? 'left' : 'right'}, transparent ${size - 0.5}px, #000 ${size}px)`,
      }}
    />
  )
}

export function Island() {
  const state = useStore(island)
  const [hover, setHover] = useState(false)
  const [lingerOver, setLingerOver] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)
  const [bodyHeight, setBodyHeight] = useState(0)

  // Finished answers linger, then tuck back into the notch unless hovered.
  useEffect(() => {
    setLingerOver(false)
    if (state.phase !== 'answering' || !state.done) return
    const timer = window.setTimeout(() => setLingerOver(true), LINGER_MS)
    return () => window.clearTimeout(timer)
  }, [state.phase, state.done, state.answer])

  let mode: Mode
  if (state.phase === 'idle' || (state.phase === 'answering' && lingerOver && !hover)) {
    mode = hover ? 'peek' : state.idleVisible ? 'compact' : 'hidden'
  } else {
    mode = state.phase
  }

  const notchW = state.notch.width
  const notchH = Math.max(24, state.notch.height)
  const wide = { compact: notchW + 76, peek: Math.max(notchW + 260, 440), listening: Math.max(notchW + 230, 440), thinking: 470, answering: 520, error: 470, hidden: notchW }[mode]
  const expanded = !['compact', 'hidden'].includes(mode)

  useLayoutEffect(() => {
    const element = bodyRef.current
    if (!element) return
    const observer = new ResizeObserver(() => setBodyHeight(element.offsetHeight))
    observer.observe(element)
    setBodyHeight(element.offsetHeight)
    return () => observer.disconnect()
  }, [mode])

  const height = expanded ? notchH + Math.min(bodyHeight, 280) : notchH
  const radius = expanded ? 26 : Math.round(notchH * 0.42)

  // Tell the native window which pixels are interactive (the rest clicks through).
  useEffect(() => {
    send('island-rect', { width: Math.round(wide), height: Math.round(height), mode })
  }, [wide, height, mode])

  const mood = MOOD[mode]
  const earWidth = (wide - notchW) / 2

  return (
    <div className="pointer-events-none flex w-full justify-center">
      <motion.div
        className="pointer-events-auto relative overflow-visible bg-black text-white"
        initial={false}
        animate={{
          width: wide,
          height,
          borderBottomLeftRadius: radius,
          borderBottomRightRadius: radius,
          opacity: mode === 'hidden' ? 0 : 1,
        }}
        transition={SPRING}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{ boxShadow: expanded ? '0 24px 60px -20px rgba(0,0,0,0.9), 0 0 0 1px rgba(255,255,255,0.04)' : 'none' }}
      >
        <Shoulder side="left" size={10} />
        <Shoulder side="right" size={10} />

        {/* Ears: content lives left and right of the physical notch. */}
        <div className="absolute inset-x-0 top-0 flex items-center" style={{ height: notchH }}>
          <div className="flex h-full items-center pl-3" style={{ width: earWidth }}>
            <motion.div layout transition={SPRING} className="flex items-center gap-2">
              <Mascot mood={mood} size={Math.min(26, notchH - 6)} level={state.level} glow={false} />
              <AnimatePresence>
                {expanded && earWidth > 90 && (
                  <motion.span
                    key={mode}
                    initial={{ opacity: 0, x: -6 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0 }}
                    className="text-[12px] font-semibold tracking-tight text-white/80"
                  >
                    {mode === 'listening' ? 'Listening' : mode === 'thinking' ? 'Thinking' : mode === 'error' ? 'Hmm' : 'Blip'}
                  </motion.span>
                )}
              </AnimatePresence>
            </motion.div>
          </div>
          <div style={{ width: notchW }} />
          <div className="flex h-full items-center justify-end pr-3.5" style={{ width: earWidth }}>
            <RightEar mode={mode} level={state.level} />
          </div>
        </div>

        {/* Body below the notch */}
        <div className="absolute inset-x-0 overflow-hidden" style={{ top: notchH }}>
          <AnimatePresence mode="popLayout" initial={false}>
            {expanded && (
              <motion.div
                key={mode}
                ref={bodyRef}
                initial={{ opacity: 0, y: -8, filter: 'blur(6px)' }}
                animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                exit={{ opacity: 0, y: -4, filter: 'blur(4px)', transition: { duration: 0.12 } }}
                transition={{ ...SPRING, delay: 0.04 }}
                className="px-5 pb-4 pt-2.5"
              >
                <Body mode={mode} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </motion.div>
    </div>
  )
}

function RightEar({ mode, level }: { mode: Mode; level: number }) {
  if (mode === 'listening') return <Waveform level={level} bars={9} color="bg-emerald-300" />
  if (mode === 'answering') return <Waveform level={level * 0.8} bars={7} color="bg-white/85" />
  if (mode === 'thinking')
    return (
      <span className="flex gap-1">
        {[0, 1, 2].map((dot) => (
          <motion.span
            key={dot}
            className="size-1.5 rounded-full bg-amber-300"
            animate={{ opacity: [0.25, 1, 0.25], y: [0, -2, 0] }}
            transition={{ duration: 0.9, repeat: Infinity, delay: dot * 0.15 }}
          />
        ))}
      </span>
    )
  if (mode === 'error') return <AlertTriangle className="size-3.5 text-red-300" />
  if (mode === 'peek') return <Sparkles className="size-3.5 text-blip-300" />
  return <span className="size-1.5 rounded-full bg-white/25 shadow-[0_0_8px_rgba(143,180,255,0.6)]" />
}

function Body({ mode }: { mode: Mode }) {
  const state = useStore(island)

  if (mode === 'peek') {
    return (
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-[13px] text-white/70">
          <span>Hold</span>
          <Keycap>⌃</Keycap>
          <Keycap>⌥</Keycap>
          <span>and ask anything</span>
        </div>
        <div className="flex items-center gap-1">
          <IconButton label="History" onClick={() => send('open-settings', { tab: 'history' })}>
            <Clock3 className="size-4" />
          </IconButton>
          <IconButton label="Settings" onClick={() => send('open-settings', { tab: 'home' })}>
            <Settings2 className="size-4" />
          </IconButton>
        </div>
      </div>
    )
  }

  if (mode === 'listening') {
    return (
      <div className="min-h-[34px]">
        {state.transcript ? (
          <p className="text-[17px] font-medium leading-snug tracking-tight text-white">
            {state.transcript}
            <span className="ml-0.5 inline-block h-[17px] w-[2px] translate-y-[3px] bg-emerald-300 animate-caret" />
          </p>
        ) : (
          <p className="shimmer-text text-[17px] font-medium tracking-tight">Listening… ask me anything</p>
        )}
      </div>
    )
  }

  if (mode === 'thinking') {
    return (
      <div className="space-y-2.5">
        {state.transcript && <p className="truncate text-[13px] text-white/45">“{state.transcript}”</p>}
        <StepChips steps={state.steps} />
        {state.steps.length === 0 && <p className="shimmer-text text-[15px] font-medium">Looking at your screen…</p>}
      </div>
    )
  }

  if (mode === 'error') {
    return (
      <div className="flex items-start justify-between gap-4">
        <p className="text-[14px] leading-snug text-red-100/90">{state.error || 'Something went sideways.'}</p>
        <button
          className="shrink-0 rounded-full bg-white/10 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-white/15"
          onClick={() => send('open-settings', { tab: 'brain' })}
        >
          Fix setup
        </button>
      </div>
    )
  }

  // answering
  return (
    <div className="space-y-3">
      {state.walkthrough && (
        <div className="flex items-center gap-2">
          <div className="flex gap-1">
            {Array.from({ length: state.walkthrough.total }, (_, index) => (
              <span
                key={index}
                className={cn(
                  'h-1 rounded-full transition-all duration-500',
                  index < state.walkthrough!.index ? 'w-4 bg-mint' : index === state.walkthrough!.index ? 'w-6 bg-blip-400' : 'w-2.5 bg-white/15',
                )}
              />
            ))}
          </div>
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-blip-300">
            Step {state.walkthrough.index + 1} of {state.walkthrough.total}
          </span>
          <span className="truncate text-[11px] text-white/45">{state.walkthrough.label}</span>
        </div>
      )}
      <div className="max-h-[170px] overflow-y-auto scrollbar-none [mask-image:linear-gradient(to_bottom,black_80%,transparent)]">
        <StreamingText text={state.answer} className="text-[15px] leading-[1.45] tracking-[-0.01em] text-white/92" />
      </div>
      <div className="flex items-center justify-between">
        <StepChips steps={state.steps.filter((step) => step.status !== 'active').slice(-2)} max={2} />
        <EngineBadge engine={state.engine} latencyMs={state.latencyMs} />
      </div>
    </div>
  )
}

function IconButton({ children, label, onClick }: { children: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      aria-label={label}
      title={label}
      onClick={onClick}
      className="grid size-8 place-items-center rounded-full text-white/60 transition hover:bg-white/10 hover:text-white"
    >
      {children}
    </button>
  )
}
