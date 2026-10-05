import { AnimatePresence, motion } from 'motion/react'
import { ArrowUp, Check, GripVertical, X } from 'lucide-react'
import { guide, send, useStore } from '../bridge'
import { Mascot } from '../components/Mascot'
import { cn } from '../components/bits'

const CARD = { left: 20, top: 10, width: 520, height: 128 }

/**
 * The permission card docked to the bottom of System Settings (like Codex's computer use):
 * an arrow up at the list, what to do in one line, and the app row to drag into the list.
 * The row is drawn here; the native window lays a real drag source over the same rectangle.
 */
export function Guide() {
  const state = useStore(guide)
  const row = state.row
  return (
    <div className="relative h-full w-full">
      <motion.div
        key={state.shown ?? 0}
        initial={{ opacity: 0, y: 14, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: 'spring', stiffness: 380, damping: 30 }}
        className="absolute overflow-hidden rounded-[22px] bg-[#0b0f1c] text-white hairline shadow-[0_22px_50px_-14px_rgba(0,0,0,0.8),0_0_0_1px_rgba(111,158,245,0.12)]"
        style={CARD}
      >
        <div className="pointer-events-none absolute -right-16 -top-20 size-48 rounded-full bg-plip-400/15 blur-3xl" />
        <AnimatePresence mode="wait" initial={false}>
          {state.granted ? (
            <motion.div
              key="done"
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              className="flex h-full items-center gap-4 px-6"
            >
              <Mascot size={46} mood="happy" glow={false} />
              <div className="flex-1">
                <div className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
                  <span className="grid size-5 place-items-center rounded-full bg-mint text-slate-950"><Check className="size-3.5" strokeWidth={3.5} /></span>
                  {state.name} is on
                </div>
                <div className="mt-1 text-[12px] text-white/45">Taking you back to {state.app}…</div>
              </div>
            </motion.div>
          ) : (
            <motion.div key="ask" exit={{ opacity: 0 }} className="h-full">
              <div className="flex items-center gap-2.5 px-4 pt-3.5">
                <motion.span
                  animate={{ y: [0, -4, 0] }}
                  transition={{ repeat: Infinity, duration: 1.3, ease: 'easeInOut' }}
                  className="grid size-6 place-items-center rounded-full bg-plip-400/15 text-plip-300"
                >
                  <ArrowUp className="size-4" strokeWidth={3} />
                </motion.span>
                <div className="min-w-0 flex-1 truncate text-[14px] font-semibold tracking-tight">{state.title}</div>
                <button
                  aria-label="Close"
                  onClick={() => send('guide-close')}
                  className="grid size-6 place-items-center rounded-full text-white/40 transition hover:bg-white/10 hover:text-white"
                >
                  <X className="size-3.5" />
                </button>
              </div>
              <div
                className={cn(
                  'absolute flex items-center gap-3 rounded-[14px] px-3 hairline',
                  state.draggable ? 'bg-white/[0.07]' : 'bg-white/[0.04]',
                )}
                style={{ left: row.x - CARD.left, top: row.y - CARD.top, width: row.width, height: row.height }}
              >
                {state.icon ? <img src={state.icon} alt="" className="size-7" draggable={false} /> : <Mascot size={26} glow={false} />}
                <span className="text-[14px] font-semibold tracking-tight">{state.app}</span>
                <span className="ml-auto flex items-center gap-1 text-[11.5px] font-medium text-white/40">
                  {state.draggable ? <><GripVertical className="size-3.5" /> drag me up</> : 'switch it on above'}
                </span>
              </div>
              <div className="absolute bottom-3 left-4 right-4 truncate text-[11.5px] text-white/45">{state.hint}</div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  )
}
