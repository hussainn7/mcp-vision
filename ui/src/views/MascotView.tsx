import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { mascot, useStore } from '../bridge'
import { Mascot } from '../components/Mascot'

/** Types the label out like Clicky's bubble, 30-55 ms per character. */
function useTyped(text: string) {
  const [shown, setShown] = useState('')
  useEffect(() => {
    setShown('')
    if (!text) return
    let index = 0
    let timer = 0
    const step = () => {
      index += 1
      setShown(text.slice(0, index))
      if (index < text.length) timer = window.setTimeout(step, 30 + Math.random() * 25)
    }
    timer = window.setTimeout(step, 80)
    return () => window.clearTimeout(timer)
  }, [text])
  return shown
}

// Small on purpose: a 22 px droplet is easy to follow and never covers what it points at.
export const MASCOT_SIZE = 22
export const MASCOT_ANCHOR = { x: 26, y: 26 }

export function MascotView() {
  const state = useStore(mascot)
  const typed = useTyped(state.mood === 'pointing' ? state.label : '')

  return (
    <div className="relative h-full w-full">
      <div className="absolute" style={{ left: MASCOT_ANCHOR.x - MASCOT_SIZE / 2, top: MASCOT_ANCHOR.y - MASCOT_SIZE / 2 }}>
        <Mascot mood={state.mood} level={state.level} look={state.look} lean={state.lean} size={MASCOT_SIZE} />
      </div>
      <AnimatePresence>
        {typed && (
          <motion.div
            key={state.label}
            initial={{ opacity: 0, scale: 0.6, x: -4 }}
            animate={{ opacity: 1, scale: 1, x: 0 }}
            exit={{ opacity: 0, scale: 0.9, transition: { duration: 0.25 } }}
            transition={{ type: 'spring', stiffness: 500, damping: 26 }}
            style={{ left: MASCOT_ANCHOR.x + MASCOT_SIZE / 2 + 3, top: MASCOT_ANCHOR.y + 3, transformOrigin: 'left center' }}
            className="brand-gradient absolute whitespace-nowrap rounded-full px-2 py-[3px] text-[11px] font-semibold tracking-tight text-slate-950 shadow-[0_6px_18px_-6px_rgba(95,142,244,0.9),inset_0_1px_0_rgba(255,255,255,0.5)]"
          >
            {typed}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
