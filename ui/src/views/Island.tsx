import { AnimatePresence, motion } from 'motion/react'
import { AlertTriangle, Check, ChevronUp, Clock3, FileText, Settings2, Sparkles, Square, X } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { island, send, useStore, type IslandState, type Mood, type Phase } from '../bridge'
import { Mascot } from '../components/Mascot'
import { Chord, EngineBadge, StepChips, StreamingText, Waveform, cn } from '../components/bits'

type Mode = 'hidden' | 'compact' | 'mini' | 'peek' | Exclude<Phase, 'idle'>

const MOOD: Record<Mode, Mood> = {
  hidden: 'idle',
  compact: 'idle',
  mini: 'speaking',
  peek: 'happy',
  listening: 'listening',
  thinking: 'thinking',
  answering: 'speaking',
  error: 'error',
}

const SPRING = { type: 'spring', stiffness: 420, damping: 34, mass: 0.9 } as const
const LINGER_MS = 7000
const MAX_BODY = 360

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
  const [pointerHover, setHover] = useState(false)
  // The native host tracks hover itself (WebKit tracking areas are flaky in a never-key panel).
  const hover = pointerHover || state.hovered
  const [lingerOver, setLingerOver] = useState(false)
  const [minimizedLocal, setMinimized] = useState(false)
  const minimized = minimizedLocal || Boolean(state.minimized)
  const bodyRef = useRef<HTMLDivElement>(null)
  const [bodyHeight, setBodyHeight] = useState(0)

  // A new question always opens the island again.
  useEffect(() => {
    if (state.phase === 'listening' || state.phase === 'thinking') setMinimized(false)
  }, [state.phase])
  useEffect(() => {
    if (state.confirm) setMinimized(false)
  }, [state.confirm])

  // Finished answers linger, then tuck back into the notch unless hovered or waiting on a confirm.
  useEffect(() => {
    setLingerOver(false)
    if (state.phase !== 'answering' || !state.done || state.confirm) return
    const timer = window.setTimeout(() => setLingerOver(true), LINGER_MS + state.plan.length * 2500)
    return () => window.clearTimeout(timer)
  }, [state.phase, state.done, state.answer, state.confirm, state.plan.length])

  let mode: Mode
  if (state.phase === 'idle' || (state.phase === 'answering' && lingerOver && !hover)) {
    mode = hover ? 'peek' : state.idleVisible ? 'compact' : 'hidden'
  } else if (minimized && (state.phase === 'answering' || state.phase === 'error')) {
    mode = hover ? state.phase : 'mini'
  } else {
    mode = state.phase
  }

  const notchW = state.notch.width
  const notchH = Math.max(24, state.notch.height)
  const rich = state.plan.length > 0 || state.confirm !== null || state.results.length > 0
  const wide = {
    compact: notchW + 76, mini: notchW + 96, hidden: notchW,
    peek: Math.max(notchW + 260, 440), listening: Math.max(notchW + 230, 440),
    thinking: rich ? 520 : 470, answering: rich ? 540 : 520, error: 470,
  }[mode]
  const expanded = !['compact', 'hidden', 'mini'].includes(mode)

  useLayoutEffect(() => {
    const element = bodyRef.current
    if (!element) return
    const observer = new ResizeObserver(() => setBodyHeight(element.offsetHeight))
    observer.observe(element)
    setBodyHeight(element.offsetHeight)
    return () => observer.disconnect()
  }, [mode])

  const height = expanded ? notchH + Math.min(bodyHeight, MAX_BODY) : notchH
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
        animate={{ width: wide, height, borderBottomLeftRadius: radius, borderBottomRightRadius: radius, opacity: mode === 'hidden' ? 0 : 1 }}
        transition={SPRING}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onClick={() => mode === 'mini' && setMinimized(false)}
        style={{ boxShadow: expanded ? '0 24px 60px -20px rgba(0,0,0,0.9), 0 0 0 1px rgba(255,255,255,0.05)' : 'none' }}
      >
        <Shoulder side="left" size={10} />
        <Shoulder side="right" size={10} />

        {/* Ears: content lives left and right of the physical notch. */}
        <div className="absolute inset-x-0 top-0 flex items-center" style={{ height: notchH }}>
          <div className="flex h-full items-center pl-3" style={{ width: earWidth }}>
            <motion.div layout transition={SPRING} className="flex items-center gap-2">
              <Mascot mood={mood} size={Math.min(24, notchH - 6)} level={state.level} glow={false} />
              <AnimatePresence>
                {expanded && earWidth > 90 && (
                  <motion.span
                    key={mode}
                    initial={{ opacity: 0, x: -6 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0 }}
                    className="text-[12px] font-semibold tracking-tight text-white/80"
                  >
                    {mode === 'listening' ? 'Listening' : mode === 'thinking' ? 'Thinking' : mode === 'error' ? 'Hmm' : state.confirm ? 'Your call' : 'Plip'}
                  </motion.span>
                )}
              </AnimatePresence>
            </motion.div>
          </div>
          <div style={{ width: notchW }} />
          <div className="flex h-full items-center justify-end gap-1.5 pr-3" style={{ width: earWidth }}>
            <RightEar mode={mode} level={state.level} done={state.done} />
            {expanded && (mode === 'answering' || mode === 'error') && (
              <button
                aria-label="Minimize"
                title="Minimize"
                onClick={(event) => {
                  event.stopPropagation()
                  setMinimized(true)
                  setHover(false)
                }}
                className="grid size-6 place-items-center rounded-full text-white/45 transition hover:bg-white/10 hover:text-white"
              >
                <ChevronUp className="size-3.5" strokeWidth={2.5} />
              </button>
            )}
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

function RightEar({ mode, level, done }: { mode: Mode; level: number; done: boolean }) {
  if (mode === 'listening') return <Waveform level={level} bars={9} color="bg-dew" />
  // TTS reports no level; keep the bars alive while Plip is still talking.
  if (mode === 'answering' || mode === 'mini')
    return done && mode === 'mini' ? <Check className="size-3.5 text-mint" strokeWidth={3} /> : <Waveform level={done ? 0.04 : Math.max(level, 0.42)} bars={7} color="bg-plip-200" />
  if (mode === 'thinking')
    return (
      <span className="flex gap-1">
        {[0, 1, 2].map((dot) => (
          <motion.span
            key={dot}
            className="size-1.5 rounded-full bg-plip-300"
            animate={{ opacity: [0.25, 1, 0.25], y: [0, -2, 0] }}
            transition={{ duration: 0.9, repeat: Infinity, delay: dot * 0.15 }}
          />
        ))}
      </span>
    )
  if (mode === 'error') return <AlertTriangle className="size-3.5 text-coral" />
  if (mode === 'peek') return <Sparkles className="size-3.5 text-plip-300" />
  return <span className="size-1.5 rounded-full bg-plip-300/60 shadow-[0_0_8px_rgba(143,179,250,0.7)]" />
}

function Body({ mode }: { mode: Mode }) {
  const state = useStore(island)

  if (mode === 'peek') {
    return (
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-[13px] text-white/70">
          <span>Hold</span>
          <Chord />
          <span>and ask, or tell me to do something</span>
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
            <span className="ml-0.5 inline-block h-[17px] w-[2px] translate-y-[3px] bg-dew animate-caret" />
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
        <p className="text-[14px] leading-snug text-rose-100/90">{state.error || 'Something went sideways.'}</p>
        {state.fixable && (
          <button
            className="shrink-0 rounded-full bg-white/10 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-white/15"
            onClick={() => send('open-settings', { tab: 'brain' })}
          >
            Fix setup
          </button>
        )}
      </div>
    )
  }

  // answering
  return (
    <div className="space-y-3">
      {state.walkthrough && !state.plan.length && <WalkthroughBar state={state} />}
      {state.plan.length > 0 && <Checklist plan={state.plan} index={state.planIndex} />}
      <div className="max-h-[150px] overflow-y-auto scrollbar-none [mask-image:linear-gradient(to_bottom,black_80%,transparent)]">
        <StreamingText text={state.answer} className="text-[15px] leading-[1.45] tracking-[-0.01em] text-white/92" />
      </div>
      {state.results.length > 0 && <Results items={state.results} />}
      {state.confirm && <ConfirmCard />}
      {!state.confirm && (
        <div className="flex items-center justify-between gap-3">
          <StepChips steps={state.steps.filter((step) => step.status !== 'active').slice(-2)} max={2} />
          <div className="flex items-center gap-2">
            {!state.done && (
              <button
                onClick={() => send('stop')}
                className="flex items-center gap-1 rounded-full bg-white/[0.06] px-2 py-1 text-[10.5px] font-medium text-white/55 hover:bg-white/10 hover:text-white"
              >
                <Square className="size-2.5 fill-current" /> Stop
              </button>
            )}
            <EngineBadge engine={state.engine} latencyMs={state.latencyMs} />
          </div>
        </div>
      )}
    </div>
  )
}

function WalkthroughBar({ state }: { state: IslandState }) {
  const walk = state.walkthrough!
  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-1">
        {Array.from({ length: walk.total }, (_, index) => (
          <span
            key={index}
            className={cn(
              'h-1 rounded-full transition-all duration-500',
              index < walk.index ? 'w-4 bg-mint' : index === walk.index ? 'w-6 bg-plip-400' : 'w-2.5 bg-white/15',
            )}
          />
        ))}
      </div>
      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-plip-300">
        Step {walk.index + 1} of {walk.total}
      </span>
      <span className="truncate text-[11px] text-white/45">{walk.label}</span>
    </div>
  )
}

/** The plan from [PLAN: …]: done steps checked, the current one lit. */
function Checklist({ plan, index }: { plan: string[]; index: number }) {
  return (
    <ol className="space-y-1.5">
      {plan.map((step, position) => {
        const done = position < index
        const current = position === index
        return (
          <motion.li
            key={step + position}
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: position * 0.05 }}
            className="flex items-center gap-2.5"
          >
            <span
              className={cn(
                'grid size-[18px] shrink-0 place-items-center rounded-full text-[10px] font-bold transition-colors',
                done ? 'bg-mint text-slate-950' : current ? 'brand-gradient text-slate-950 shadow-[0_0_12px_rgba(95,142,244,0.55)]' : 'bg-white/[0.07] text-white/40',
              )}
            >
              {done ? <Check className="size-3" strokeWidth={3.5} /> : position + 1}
            </span>
            <span className={cn('truncate text-[13px] tracking-tight', done ? 'text-white/40 line-through decoration-white/20' : current ? 'font-semibold text-white' : 'text-white/55')}>
              {step}
            </span>
            {current && <span className="ml-auto shrink-0 rounded-full bg-plip-400/15 px-2 py-0.5 text-[10px] font-semibold text-plip-200">now</span>}
          </motion.li>
        )
      })}
    </ol>
  )
}

function Results({ items }: { items: IslandState['results'] }) {
  return (
    <div className="space-y-1">
      {items.slice(0, 4).map((item) => (
        <button
          key={(item.path || '') + item.title}
          onClick={() => item.path && send('open-path', { path: item.path })}
          className="flex w-full items-center gap-2.5 rounded-xl px-2 py-1.5 text-left transition hover:bg-white/[0.06]"
        >
          <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-plip-400/12 text-plip-200">
            <FileText className="size-3.5" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[13px] font-medium text-white/90">{item.title}</span>
            {item.detail && <span className="block truncate font-mono text-[10.5px] text-white/35">{item.detail}</span>}
          </span>
        </button>
      ))}
    </div>
  )
}

function ConfirmCard() {
  const { confirm } = useStore(island)
  if (!confirm) return null
  return (
    <motion.div
      initial={{ opacity: 0, y: 6, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={SPRING}
      className="rounded-2xl bg-white/[0.06] p-3 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.08)]"
    >
      <div className="text-[13.5px] font-semibold tracking-tight text-white">{confirm.title}</div>
      {confirm.lines.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {confirm.lines.slice(0, 4).map((line) => (
            <li key={line} className="truncate text-[12.5px] text-white/60">{line}</li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex items-center justify-between">
        <span className="flex items-center gap-1 text-[11px] text-white/35">or hold <Chord className="h-5 min-w-5 rounded-md px-1 text-[10px]" /> and say yes</span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => send('confirm-action', { accept: false })}
            className="flex items-center gap-1 rounded-full px-3 py-1.5 text-[12px] font-medium text-white/65 hover:bg-white/10 hover:text-white"
          >
            <X className="size-3.5" /> Cancel
          </button>
          <button
            onClick={() => send('confirm-action', { accept: true })}
            className="flex items-center gap-1 rounded-full bg-white px-3.5 py-1.5 text-[12px] font-semibold text-slate-950 shadow-[0_6px_18px_-6px_rgba(255,255,255,0.5)] hover:bg-plip-50"
          >
            <Check className="size-3.5" strokeWidth={3} /> {confirm.confirm}
          </button>
        </div>
      </div>
    </motion.div>
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
