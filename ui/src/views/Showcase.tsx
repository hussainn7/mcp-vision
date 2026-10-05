import { motion } from 'motion/react'
import { Apple, BatteryFull, ChevronRight, Search, SlidersHorizontal, Wifi } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { island, mascot, useStore } from '../bridge'
import { Mascot } from '../components/Mascot'
import { ERROR_FRAME, FRAMES, applyFrame } from '../demo'
import { Island } from './Island'

const W = 1440
const H = 900
const NOTCH = { width: 196, height: 34 }
const CURSOR = { x: 930, y: 560 }

/**
 * A pretend MacBook desktop that plays Plip's whole flow. Used for the
 * browser preview, the README screenshots, and demos.
 * `#showcase?frame=listening` freezes on one frame.
 */
export function Showcase() {
  const params = new URLSearchParams(location.hash.split('?')[1])
  const frozen = params.get('frame')
  const [frameIndex, setFrameIndex] = useState(0)
  const [scale, setScale] = useState(1)
  const fileRef = useRef<HTMLSpanElement>(null)
  const exportRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const [targets, setTargets] = useState<Record<string, { x: number; y: number }>>({})

  useEffect(() => {
    island.set({ notch: { ...NOTCH, hasNotch: true } })
    const fit = () => setScale(Math.min(window.innerWidth / W, window.innerHeight / H))
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [])

  useEffect(() => {
    if (frozen) {
      const frame = frozen === 'error' ? ERROR_FRAME : FRAMES.find((item) => item.name === frozen)
      if (frame) applyFrame(frame)
      return
    }
    const frame = FRAMES[frameIndex % FRAMES.length]
    applyFrame(frame)
    const timer = window.setTimeout(() => setFrameIndex((index) => index + 1), frame.hold)
    return () => window.clearTimeout(timer)
  }, [frameIndex, frozen])

  const current = frozen ? (frozen === 'error' ? ERROR_FRAME : FRAMES.find((item) => item.name === frozen)) : FRAMES[frameIndex % FRAMES.length]
  const at = current?.mascot?.at ?? 'cursor'
  const showMenu = at === 'export'

  useLayoutEffect(() => {
    const canvas = canvasRef.current?.getBoundingClientRect()
    const point = (element: Element | null, dx: number, dy: number) => {
      if (!element || !canvas) return { x: 0, y: 0 }
      const rect = element.getBoundingClientRect()
      return { x: (rect.left - canvas.left) / scale + dx, y: (rect.top - canvas.top) / scale + dy }
    }
    setTargets({ file: point(fileRef.current, 34, 30), export: point(exportRef.current, 170, 26) })
  }, [scale, showMenu])

  // Plip lives in the notch: it only drips out to point, then floats back up.
  const home = { x: W / 2, y: NOTCH.height - 4 }
  const spot = at === 'notch' ? home : at === 'cursor' ? { x: CURSOR.x + 35, y: CURSOR.y + 25 } : targets[at] ?? home

  return (
    <div className="grid h-full w-full place-items-center overflow-hidden bg-black">
      <div ref={canvasRef} className="relative origin-center overflow-hidden rounded-[18px]" style={{ width: W, height: H, transform: `scale(${scale})` }}>
        <Wallpaper />
        <MenuBar fileRef={fileRef} highlight={at === 'file' || showMenu} />
        <AppWindow />
        {showMenu && <FileMenu exportRef={exportRef} />}

        {/* the physical notch, then Plip's island fused onto it */}
        <div className="absolute left-1/2 top-0 z-40 -translate-x-1/2 rounded-b-[12px] bg-black" style={{ width: NOTCH.width, height: NOTCH.height }} />
        <div className="absolute inset-x-0 top-0 z-50">
          <Island />
        </div>

        <SystemCursor x={CURSOR.x} y={CURSOR.y} />
        <FlyingPlip x={spot.x} y={spot.y} docked={at === 'notch'} />
      </div>
    </div>
  )
}

function FlyingPlip({ x, y, docked }: { x: number; y: number; docked: boolean }) {
  const state = useStore(mascot)
  return (
    <motion.div
      className="absolute z-[60]"
      initial={false}
      animate={{ left: x - 11, top: y - 11, opacity: docked ? 0 : 1, scale: docked ? 0.3 : 1 }}
      transition={{ type: 'spring', stiffness: 90, damping: 15, mass: 0.9, opacity: { duration: 0.25 } }}
    >
      <Mascot mood={state.mood} level={state.level} look={state.look} lean={state.lean} size={22} />
      {state.mood === 'pointing' && state.label && !docked && (
        <motion.div
          key={state.label}
          initial={{ opacity: 0, scale: 0.6, x: -4 }}
          animate={{ opacity: 1, scale: 1, x: 0 }}
          transition={{ delay: 0.5, type: 'spring', stiffness: 500, damping: 26 }}
          className="brand-gradient absolute left-[25px] top-[14px] origin-left whitespace-nowrap rounded-full px-2 py-[3px] text-[11px] font-semibold text-slate-950 shadow-[0_6px_18px_-6px_rgba(95,142,244,0.9),inset_0_1px_0_rgba(255,255,255,0.5)]"
        >
          {state.label}
        </motion.div>
      )}
    </motion.div>
  )
}

function Wallpaper() {
  return (
    <div className="absolute inset-0 overflow-hidden bg-[#06131f]">
      <div className="absolute -left-40 -top-40 size-[900px] rounded-full bg-[#0891b2] opacity-55 blur-[140px]" />
      <div className="absolute -right-52 top-24 size-[760px] rounded-full bg-[#2563eb] opacity-50 blur-[140px]" />
      <div className="absolute -bottom-72 left-1/3 size-[820px] rounded-full bg-[#14b8a6] opacity-35 blur-[160px]" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,rgba(255,255,255,0.08),transparent_60%)]" />
    </div>
  )
}

function MenuBar({ fileRef, highlight }: { fileRef: React.RefObject<HTMLSpanElement | null>; highlight: boolean }) {
  const items = ['Edit', 'Insert', 'Slide', 'Format', 'Arrange', 'View', 'Play', 'Window', 'Help']
  return (
    <div className="absolute inset-x-0 top-0 z-30 flex h-[34px] items-center justify-between bg-black/25 px-4 text-[13.5px] text-white/95 backdrop-blur-2xl">
      <div className="flex items-center gap-5">
        <Apple className="size-4 fill-white" />
        <span className="font-bold">Keynote</span>
        <span ref={fileRef} className={highlight ? 'rounded-[5px] bg-white/20 px-1.5 py-0.5 -mx-1.5' : ''}>File</span>
        {items.map((item) => <span key={item}>{item}</span>)}
      </div>
      <div className="flex items-center gap-4 text-white/90">
        <BatteryFull className="size-[18px]" />
        <Wifi className="size-4" />
        <Search className="size-[15px]" />
        <SlidersHorizontal className="size-[15px]" />
        <span className="font-medium">Thu Oct 2&nbsp;&nbsp;9:41 AM</span>
      </div>
    </div>
  )
}

function FileMenu({ exportRef }: { exportRef: React.RefObject<HTMLDivElement | null> }) {
  const rows = ['New', 'Open…', 'Open Recent', null, 'Close', 'Save', 'Duplicate', 'Rename…', null, 'Export To', 'Share…', null, 'Print…']
  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      className="absolute left-[132px] top-[36px] z-30 w-[230px] rounded-[10px] bg-[#2b2b33]/85 p-1.5 text-[13.5px] text-white/90 shadow-2xl ring-1 ring-white/10 backdrop-blur-2xl"
    >
      {rows.map((row, index) =>
        row === null ? (
          <div key={index} className="mx-2 my-1 h-px bg-white/10" />
        ) : (
          <div
            key={row}
            ref={row === 'Export To' ? exportRef : undefined}
            className={row === 'Export To' ? 'flex items-center justify-between rounded-[6px] bg-[#3d6bff] px-2.5 py-[3px]' : 'px-2.5 py-[3px]'}
          >
            {row}
            {row === 'Export To' && <ChevronRight className="size-3.5" />}
          </div>
        ),
      )}
    </motion.div>
  )
}

function AppWindow() {
  return (
    <div className="absolute left-[190px] top-[96px] z-10 h-[700px] w-[1060px] overflow-hidden rounded-[14px] bg-[#1d1d22] shadow-[0_40px_120px_-30px_rgba(0,0,0,0.9)] ring-1 ring-white/10">
      <div className="flex h-[52px] items-center gap-2 border-b border-white/[0.06] bg-[#26262c] px-4">
        <span className="size-3 rounded-full bg-[#ff5f57]" />
        <span className="size-3 rounded-full bg-[#febc2e]" />
        <span className="size-3 rounded-full bg-[#28c840]" />
        <div className="ml-6 flex gap-2">
          {['View', 'Zoom', 'Add Slide', 'Play', 'Table', 'Chart', 'Text', 'Shape', 'Media'].map((label) => (
            <span key={label} className="rounded-md px-2.5 py-1 text-[11.5px] text-white/55">{label}</span>
          ))}
        </div>
        <span className="ml-auto text-[13px] font-semibold text-white/80">Q4 Roadmap.key</span>
      </div>
      <div className="flex h-[648px]">
        <div className="w-[150px] space-y-3 border-r border-white/[0.06] p-3">
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className={`aspect-video rounded-md ${index === 1 ? 'ring-2 ring-[#3d6bff]' : ''} bg-gradient-to-br from-white/[0.08] to-white/[0.02]`} />
          ))}
        </div>
        <div className="grid flex-1 place-items-center bg-[#151519]">
          <div className="aspect-video w-[760px] rounded-lg bg-gradient-to-br from-[#f8fafc] to-[#e2e8f0] p-12 shadow-2xl">
            <div className="text-[13px] font-semibold uppercase tracking-[0.2em] text-[#3d6bff]">Q4 2026</div>
            <div className="mt-3 text-[46px] font-bold leading-tight tracking-tight text-[#0f172a]">Ship the buddy.</div>
            <div className="mt-6 grid grid-cols-3 gap-4">
              {['Notch island', 'Use your plan', 'Guided steps'].map((label) => (
                <div key={label} className="rounded-xl bg-white p-4 text-[14px] font-semibold text-[#334155] shadow-sm">{label}</div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function SystemCursor({ x, y }: { x: number; y: number }) {
  return (
    <svg className="absolute z-[55]" style={{ left: x, top: y }} width="22" height="30" viewBox="0 0 22 30" aria-hidden>
      <path d="M2 2 L2 23 L7.5 17.8 L11.2 26.5 L14.6 25 L11 16.5 L18.5 16.5 Z" fill="black" stroke="white" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  )
}
