import { AnimatePresence, motion } from 'motion/react'
import { Check, CircleDashed, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
import type { EngineBadge as Badge, Step } from '../bridge'

export const cn = (...parts: Parameters<typeof clsx>) => twMerge(clsx(parts))

/** Mirrored bars that follow the live level with a little organic jitter. */
export function Waveform({ level, bars = 18, className, color = 'bg-white' }: {
  level: number
  bars?: number
  className?: string
  color?: string
}) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let frame = 0
    const loop = () => {
      setTick((value) => value + 1)
      frame = requestAnimationFrame(loop)
    }
    frame = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(frame)
  }, [])
  const energy = Math.pow(Math.min(Math.max(level - 0.01, 0) * 2.6, 1), 0.7)
  return (
    <div className={cn('flex items-center gap-[2px]', className)}>
      {Array.from({ length: bars }, (_, index) => {
        const middle = 1 - Math.abs(index - (bars - 1) / 2) / ((bars - 1) / 2)
        const wobble = (Math.sin(tick / 7 + index * 0.9) + 1) / 2
        const height = 3 + energy * 15 * (0.35 + middle * 0.65) * (0.65 + wobble * 0.35) + wobble * 1.5
        return <span key={index} className={cn('w-[2.5px] rounded-full transition-[height] duration-75', color)} style={{ height }} />
      })}
    </div>
  )
}

export function Keycap({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        'inline-flex h-7 min-w-7 items-center justify-center rounded-lg px-2 font-sans text-[13px] font-medium text-white/90',
        'bg-gradient-to-b from-white/[0.14] to-white/[0.05] shadow-[inset_0_1px_0_rgba(255,255,255,0.18),inset_0_-2px_0_rgba(0,0,0,0.35),0_4px_12px_-4px_rgba(0,0,0,0.6)]',
        className,
      )}
    >
      {children}
    </kbd>
  )
}

export function StepChips({ steps, max = 4 }: { steps: Step[]; max?: number }) {
  const visible = steps.slice(-max)
  return (
    <div className="flex flex-wrap gap-1.5">
      <AnimatePresence initial={false}>
        {visible.map((step) => (
          <motion.span
            key={step.id}
            layout
            initial={{ opacity: 0, y: 6, filter: 'blur(4px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, scale: 0.9 }}
            transition={{ type: 'spring', stiffness: 420, damping: 30 }}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium tracking-tight',
              step.status === 'error' ? 'bg-red-500/15 text-red-200' : 'bg-white/[0.07] text-white/75',
            )}
          >
            {step.status === 'active' && <CircleDashed className="size-3 animate-spin-slow text-blip-300" />}
            {step.status === 'done' && <Check className="size-3 text-mint" strokeWidth={3} />}
            {step.status === 'skipped' && <span className="size-1.5 rounded-full bg-white/30" />}
            {step.status === 'error' && <X className="size-3 text-red-300" strokeWidth={3} />}
            <span>{step.label}</span>
            {step.detail && <span className="font-mono text-[10px] text-white/40">{step.detail}</span>}
          </motion.span>
        ))}
      </AnimatePresence>
    </div>
  )
}

export function EngineBadge({ engine, latencyMs }: { engine: Badge | null; latencyMs?: number | null }) {
  if (!engine) return null
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-2 py-0.5 text-[10.5px] font-medium text-white/55">
      <span
        className={cn(
          'size-1.5 rounded-full',
          engine.kind === 'subscription' ? 'bg-violet-glow' : engine.kind === 'local' ? 'bg-mint' : 'bg-blip-400',
        )}
      />
      {engine.label}
      {engine.kind === 'subscription' && <span className="text-white/35">· plan</span>}
      {latencyMs != null && <span className="font-mono text-white/35">{(latencyMs / 1000).toFixed(1)}s</span>}
    </span>
  )
}

/** Reveals streamed text with a soft fade on the newest characters. */
export function StreamingText({ text, className }: { text: string; className?: string }) {
  const previous = useRef(0)
  const stable = text.slice(0, previous.current)
  const fresh = text.slice(previous.current)
  useEffect(() => {
    previous.current = text.length
  })
  return (
    <p className={className}>
      {stable}
      <motion.span key={text.length} initial={{ opacity: 0.25 }} animate={{ opacity: 1 }} transition={{ duration: 0.35 }}>
        {fresh}
      </motion.span>
    </p>
  )
}
