import { AnimatePresence, motion } from 'motion/react'
import { ArrowLeft, ArrowRight, Check, Hand, Mic, MonitorUp } from 'lucide-react'
import { useState } from 'react'
import { send, type SettingsState } from '../../bridge'
import { Mascot } from '../../components/Mascot'
import { Keycap, cn } from '../../components/bits'
import { ConnectAI } from './connect'
import { Button } from './ui'

const PERMS = [
  { key: 'screen', icon: MonitorUp, title: 'Screen Recording', why: 'So Plip can see what you’re asking about.' },
  { key: 'accessibility', icon: Hand, title: 'Accessibility', why: 'For the ⌃⌥ shortcut and pointing at things.' },
  { key: 'microphone', icon: Mic, title: 'Microphone', why: 'Only while you hold the shortcut. Never saved.' },
] as const

/** First-run walkthrough: what Plip is, permissions, connect an AI, how to ask. */
export function Onboarding({ state }: { state: SettingsState }) {
  const [step, setStep] = useState(0)
  const engine = state.engines.find((item) => item.selected)
  const perms = state.permissions
  const steps = [
    {
      title: 'Hi, I’m Plip',
      body: (
        <div className="space-y-3 text-[13.5px] leading-relaxed text-white/65">
          <p>I live in your MacBook’s notch. Ask me anything about what’s on your screen and I’ll answer out loud and point at things.</p>
          <p>Setup takes about a minute: give me a few permissions, connect an AI, and you’re done.</p>
        </div>
      ),
    },
    {
      title: 'Let me see and hear',
      body: (
        <div className="space-y-2">
          {PERMS.map(({ key, icon: Icon, title, why }) => {
            const granted = perms[key] === true
            return (
              <div key={key} className="flex items-center gap-3 rounded-xl bg-white/[0.03] px-3 py-2.5 hairline">
                <Icon className="size-4 text-plip-300" />
                <div className="flex-1">
                  <div className="text-[13px] font-medium">{title}</div>
                  <div className="text-[11.5px] text-white/40">{why}</div>
                </div>
                {granted ? (
                  <span className="inline-flex items-center gap-1 text-[12px] font-semibold text-emerald-300"><Check className="size-3.5" strokeWidth={3} /> On</span>
                ) : (
                  <Button size="sm" onClick={() => send('grant', { permission: key })}>Allow</Button>
                )}
              </div>
            )
          })}
          <div className="pt-1 text-[11.5px] text-white/35">
            macOS opens System Settings: switch Plip on, then come back. If it still says “Allow”, quit and reopen Plip.
            <button className="ml-1 text-plip-300 hover:underline" onClick={() => send('refresh')}>Check again</button>
          </div>
        </div>
      ),
    },
    { title: 'Connect your AI', body: <ConnectAI state={state} compact /> },
    {
      title: 'You’re all set',
      body: (
        <div className="space-y-4 text-[13.5px] text-white/65">
          <div className="flex items-center gap-2">
            Hold <Keycap>⌃ control</Keycap> + <Keycap>⌥ option</Keycap>, talk, then let go.
          </div>
          <div className="space-y-1.5 text-white/50">
            <div>Try: “What does this button do?”</div>
            <div>Try: “How do I export this as a PDF?”</div>
            <div>Try: “Find my lease PDF”</div>
          </div>
          {!engine || engine.status !== 'ready' ? (
            <div className="rounded-xl bg-sun/10 px-3 py-2 text-[12px] text-sun">No AI connected yet. You can connect one any time under Brain.</div>
          ) : null}
        </div>
      ),
    },
  ]
  const last = step === steps.length - 1

  return (
    <div className="absolute inset-0 z-40 grid place-items-center bg-ink/95 backdrop-blur-xl">
      <div className="w-[520px]">
        <div className="mb-6 flex justify-center"><Mascot size={84} mood={last ? 'happy' : 'idle'} /></div>
        <div className="mb-5 flex justify-center gap-1.5">
          {steps.map((_, index) => (
            <span key={index} className={cn('h-1 rounded-full transition-all', index === step ? 'w-6 brand-gradient' : 'w-2 bg-white/15')} />
          ))}
        </div>
        <AnimatePresence mode="wait">
          <motion.div key={step} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }} transition={{ duration: 0.18 }}>
            <h1 className="text-gradient mb-4 text-center text-[28px] font-semibold tracking-[-0.03em]">{steps[step].title}</h1>
            <div className="min-h-[190px]">{steps[step].body}</div>
          </motion.div>
        </AnimatePresence>
        <div className="mt-6 flex items-center justify-between">
          {step > 0 ? (
            <Button variant="quiet" onClick={() => setStep(step - 1)}><ArrowLeft className="size-3.5" /> Back</Button>
          ) : (
            <Button variant="quiet" onClick={() => send('finish-onboarding')}>Skip</Button>
          )}
          <Button variant="brand" onClick={() => (last ? send('finish-onboarding') : setStep(step + 1))}>
            {last ? 'Start using Plip' : 'Continue'} <ArrowRight className="size-3.5" />
          </Button>
        </div>
      </div>
    </div>
  )
}
