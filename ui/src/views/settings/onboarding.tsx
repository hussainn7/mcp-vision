import { AnimatePresence, motion } from 'motion/react'
import { ArrowLeft, ArrowRight, ArrowUp, Check, Eye, Hand, Lock, Mic, MonitorUp, MousePointer2, WandSparkles } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { send, settings, type LiveState, type SettingsState, type TourStep } from '../../bridge'
import { Mascot } from '../../components/Mascot'
import { Chord, cn, useShortcutLabel } from '../../components/bits'
import { Avatar, GoogleSignIn } from './account'
import { PermissionAction, RestartBanner } from './basics'
import { ConnectAI } from './connect'
import { Button, Card } from './ui'

export const PRIVACY_URL = 'https://plip.dev/privacy'

const ALL: TourStep[] = ['welcome', 'permissions', 'brain', 'try', 'signin', 'done']

/** Sign-in comes last, after they've seen Plip work (a build without sign-in skips it). */
function stepsFor(state: SettingsState): TourStep[] {
  return state.account.available ? ALL : ALL.filter((step) => step !== 'signin')
}

/** Move the walkthrough; Plip saves it for restarts. */
function tour(step: TourStep) {
  settings.set((current) => ({ tour: { ...current.tour, step } }))
  send('tour-go', { step })
}

/** What's still missing, in walkthrough order. */
export function readiness(state: SettingsState) {
  const perms = state.permissions
  const needSpeech = state.voice.stt === 'apple'
  const hears = perms.microphone === true && (!needSpeech || perms.speech === true)
  const engine = state.engines.find((item) => item.selected)
  return {
    needSpeech,
    hears,
    sees: perms.screen === true,
    hands: perms.accessibility === true,
    permissions: perms.screen === true && perms.accessibility === true && hears,
    engine,
    brain: engine?.status === 'ready',
  }
}

/** First run: intro, permissions, a brain, one practice ask, then sign in. */
export function Onboarding({ state }: { state: SettingsState }) {
  const order = stepsFor(state)
  const counted = order.slice(1, -1)
  const step = order.includes(state.tour?.step) ? state.tour.step : 'welcome'
  const index = order.indexOf(step)
  const next = () => tour(order[Math.min(index + 1, order.length - 1)])
  const back = () => tour(order[Math.max(index - 1, 0)])
  const skip = () => (state.account.required ? tour('signin') : send('finish-onboarding'))
  const props = { state, steps: counted, next, back }

  return (
    <div className="absolute inset-0 z-40 flex flex-col bg-ink/[0.97] backdrop-blur-xl">
      <header className="relative z-10 flex items-center justify-between px-8 pt-6">
        <div className="flex w-40 items-center gap-2">
          <Mascot size={26} mood="happy" glow={false} />
          <span className="text-[14px] font-semibold tracking-tight">Plip</span>
        </div>
        {step !== 'welcome' && (
          <div className="flex items-center gap-1.5" aria-label={`Step ${Math.max(counted.indexOf(step), 0) + 1} of ${counted.length}`}>
            {order.slice(1).map((item, position) => (
              <button
                key={item}
                aria-label={item}
                onClick={() => tour(item)}
                className={cn(
                  'h-1.5 rounded-full transition-all duration-500',
                  position + 1 < index ? 'w-4 bg-mint/80' : position + 1 === index ? 'w-7 brand-gradient' : 'w-3 bg-white/15 hover:bg-white/25',
                )}
              />
            ))}
          </div>
        )}
        <div className="flex w-40 justify-end">
          {step !== 'done' && step !== 'signin' && <Button variant="quiet" onClick={skip}>Skip setup</Button>}
        </div>
      </header>

      {/* m-auto, not items-center: tall steps scroll from the top */}
      <main className="relative z-10 flex flex-1 overflow-y-auto scrollbar-none px-8 py-6">
        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            initial={{ opacity: 0, y: 10, filter: 'blur(6px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -8, filter: 'blur(4px)' }}
            transition={{ duration: 0.24, ease: [0.32, 0.72, 0, 1] }}
            className="m-auto w-full max-w-[640px]"
          >
            {step === 'welcome' && <Welcome next={next} />}
            {step === 'permissions' && <SeeAndHear {...props} />}
            {step === 'brain' && <PickBrain {...props} />}
            {step === 'try' && <TryIt {...props} />}
            {step === 'signin' && <SignInStep {...props} />}
            {step === 'done' && <AllSet />}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}

function Frame({ step, steps, title, subtitle, children, footer }: {
  step: TourStep
  steps: TourStep[]
  title: React.ReactNode
  subtitle: React.ReactNode
  children: React.ReactNode
  footer: React.ReactNode
}) {
  return (
    <div>
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-plip-300">Step {steps.indexOf(step) + 1} of {steps.length}</div>
      <h1 className="text-gradient text-[30px] font-semibold leading-[1.1] tracking-[-0.035em]">{title}</h1>
      <p className="mt-2 max-w-[560px] text-[14px] leading-relaxed text-white/55">{subtitle}</p>
      <div className="mt-6">{children}</div>
      <div className="mt-7 flex items-center gap-2">{footer}</div>
    </div>
  )
}

function Back({ onClick }: { onClick: () => void }) {
  return <Button variant="quiet" onClick={onClick}><ArrowLeft className="size-3.5" /> Back</Button>
}

function Forward({ onClick, children = 'Continue', glow }: { onClick: () => void; children?: React.ReactNode; glow?: boolean }) {
  return (
    <Button variant={glow ? 'brand' : 'primary'} onClick={onClick} className="ml-auto px-5 py-2 text-[13px]">
      {children} <ArrowRight className="size-3.5" />
    </Button>
  )
}

function Later({ onClick, label = 'Skip for now' }: { onClick: () => void; label?: string }) {
  return <Button variant="quiet" className="ml-auto" onClick={onClick}>{label}</Button>
}

/** Auto-advance once done (not if it was done on arrival). */
function useAdvance(done: boolean, next: () => void, delay = 1300) {
  const arrivedDone = useRef(done)
  useEffect(() => {
    if (!done || arrivedDone.current) return
    const timer = window.setTimeout(next, delay)
    return () => window.clearTimeout(timer)
  }, [done])                                   // eslint-disable-line react-hooks/exhaustive-deps
}

// -- 0. welcome -----------------------------------------------------------------------------

const PROMISES = [
  { icon: Eye, title: 'Sees what you see', text: 'Ask about any app, website or error message on your screen.' },
  { icon: MousePointer2, title: 'Shows you where', text: 'Points at the exact button to click, step by step.' },
  { icon: WandSparkles, title: 'Does it for you', text: 'Clicks, types and opens things when you ask it to.' },
]

function Welcome({ next }: { next: () => void }) {
  return (
    <div className="mx-auto max-w-[520px] text-center">
      <div className="flex justify-center"><Mascot size={72} mood="happy" /></div>
      <h1 className="text-gradient mt-5 text-[38px] font-semibold leading-none tracking-[-0.045em]">Hi, I’m Plip</h1>
      <p className="mt-3.5 text-[15px] leading-relaxed text-white/60">
        I’m a helper that lives at the top of your screen. Hold two keys, ask me something out loud, and I’ll answer, show you
        where to click, or do it for you. No typing, no chat window.
      </p>
      <ul className="mx-auto mt-7 max-w-[440px] space-y-3 text-left">
        {PROMISES.map(({ icon: Icon, title, text }, position) => (
          <motion.li key={title} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 + position * 0.08 }} className="flex items-start gap-3">
            <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-plip-400/15 text-plip-200"><Icon className="size-3.5" /></span>
            <span>
              <span className="block text-[13.5px] font-semibold tracking-tight">{title}</span>
              <span className="block text-[12.5px] text-white/45">{text}</span>
            </span>
          </motion.li>
        ))}
      </ul>
      <div className="mt-8 flex flex-col items-center gap-2.5">
        <Button variant="brand" onClick={next} className="px-6 py-2.5 text-[14px]">Set me up <ArrowRight className="size-4" /></Button>
        <span className="text-[12px] text-white/35">About two minutes. No tech skills needed.</span>
      </div>
      <div className="mt-6 flex items-center justify-center gap-1.5 text-[11.5px] text-white/35">
        <Lock className="size-3.5 shrink-0 text-mint" /> I only look and listen while you hold the keys.
        <button className="text-white/45 underline-offset-2 hover:text-white/70 hover:underline" onClick={() => send('open-url', { url: PRIVACY_URL })}>Privacy</button>
      </div>
    </div>
  )
}

// -- 1. see and hear --------------------------------------------------------------------------

type StepProps = { state: SettingsState; steps: TourStep[]; next: () => void; back: () => void }

function SeeAndHear({ state, steps, next, back }: StepProps) {
  const ready = readiness(state)
  const perms = state.permissions
  const talk = useShortcutLabel()
  const rows = [
    { id: 'accessibility', icon: Hand, title: 'Accessibility', why: `So ${talk} works, and Plip can click and type for you.`, on: ready.hands, value: perms.accessibility },
    { id: 'screen', icon: MonitorUp, title: 'Screen Recording', why: 'So Plip can see what you’re asking about. Only while you ask.', on: ready.sees, value: perms.screen },
    {
      id: perms.microphone === true ? 'speech' : 'microphone',
      icon: Mic,
      title: ready.needSpeech ? 'Microphone and Speech Recognition' : 'Microphone',
      why: 'So Plip hears you while you hold the keys. Nothing is recorded or saved.',
      on: ready.hears,
      value: ready.hears ? true : perms.microphone === true ? perms.speech : perms.microphone,
    },
  ]
  const current = rows.find((row) => !row.on)?.id
  const done = ready.permissions && !perms.restart
  useAdvance(done, next)

  return (
    <Frame
      step="permissions"
      steps={steps}
      title="Let Plip see and hear you"
      subtitle="For your safety, macOS makes you switch these on yourself. Click Allow: Plip opens the right page in System Settings and shows you exactly which switch to flip."
      footer={<><Back onClick={back} />{done ? <Forward onClick={next} glow /> : <Later onClick={next} />}</>}
    >
      <RestartBanner state={state} />
      <Card className="divide-y divide-white/[0.05] p-0">
        {rows.map(({ id, icon: Icon, title, why, on, value }) => (
          <div key={title} className={cn('relative flex items-center gap-4 px-5 py-4 transition', id === current && 'bg-plip-400/[0.05]')}>
            {id === current && <span className="absolute inset-y-3 left-0 w-[3px] rounded-full brand-gradient" />}
            <span className={cn('grid size-10 shrink-0 place-items-center rounded-xl hairline', on ? 'bg-mint/10 text-emerald-300' : 'bg-white/[0.06] text-white/75')}>
              {on ? <Check className="size-4" strokeWidth={3} /> : <Icon className="size-4" />}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[14px] font-semibold tracking-tight">{title}</div>
              <div className="text-[12.5px] text-white/45">{why}</div>
            </div>
            <PermissionAction id={id} value={on ? true : value} guiding={perms.guiding} />
          </div>
        ))}
      </Card>
      <div className="mt-3 text-[12px] text-white/35">
        Switched one on and it still says Allow?
        <button className="ml-1 text-plip-300 hover:underline" onClick={() => send('refresh')}>Check again</button>
      </div>
      <AnimatePresence>
        {done && (
          <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="mt-4 flex items-center gap-2 text-[13px] font-medium text-emerald-300">
            <Check className="size-4" strokeWidth={3} /> Plip can see and hear you now.
          </motion.div>
        )}
      </AnimatePresence>
    </Frame>
  )
}

// -- 2. a brain -------------------------------------------------------------------------------------

function PickBrain({ state, steps, next, back }: StepProps) {
  const ready = readiness(state)
  useAdvance(ready.brain, next, 1600)
  return (
    <Frame
      step="brain"
      steps={steps}
      title="Give Plip a brain"
      subtitle="Plip is the helper; an AI does the thinking. Plip can use an AI you already pay for, like ChatGPT or Claude, at no extra cost. Don’t have one? Google’s is free. Either way it’s a few clicks, nothing to type."
      footer={<><Back onClick={back} />{ready.brain ? <Forward onClick={next} glow /> : <Later onClick={next} />}</>}
    >
      <ConnectAI state={state} compact />
      <p className="mt-3 text-[11.5px] text-white/35">How fast answers come depends on the provider.</p>
    </Frame>
  )
}

// -- 3. try it -----------------------------------------------------------------------------------

const SAY = ['What can you do?', 'Where’s the Wi‑Fi menu?', 'Open Notes']

function TryIt({ state, steps, next, back }: StepProps) {
  const ready = readiness(state)
  const entered = useRef(Date.now() / 1000 - 1)
  const live: LiveState | null = state.live && state.live.at >= entered.current ? state.live : null
  const phase = live?.phase ?? 'idle'
  const holding = phase === 'listening'
  const talk = useShortcutLabel()
  const blocker = !ready.hands ? 'hands' : !ready.brain ? 'brain' : !ready.hears ? 'hears' : ''

  return (
    <Frame
      step="try"
      steps={steps}
      title={<>Hold <span className="text-white">{talk}</span> and ask</>}
      subtitle="Hold both keys down, say what you want, then let go. Plip answers up at the top of your screen."
      footer={<><Back onClick={back} />{phase === 'done' ? <Forward onClick={next} glow /> : <Later onClick={next} label="Try it later" />}</>}
    >
      {blocker && (
        <Card className="mb-4 flex items-center gap-3 border-sun/20 bg-sun/[0.06] py-3">
          <span className="flex-1 text-[12.5px] text-amber-100/80">
            {blocker === 'hands' ? 'The keys need Accessibility switched on first.' : blocker === 'brain' ? 'Plip needs a brain to answer.' : 'Plip needs the microphone to hear you.'}
          </span>
          {blocker === 'brain'
            ? <Button size="sm" variant="ghost" onClick={() => tour('brain')}>Give it a brain</Button>
            : <Button size="sm" variant="ghost" onClick={() => send('grant', { permission: blocker === 'hands' ? 'accessibility' : 'microphone' })}>Allow</Button>}
        </Card>
      )}
      <Card className="overflow-hidden px-6 py-7">
        <div className="flex items-center gap-7">
          <motion.div className="flex shrink-0 items-center gap-2" animate={{ scale: holding ? 0.94 : 1, y: holding ? 2 : 0 }} transition={{ type: 'spring', stiffness: 500, damping: 26 }}>
            <Chord className={cn('h-16 min-w-16 rounded-2xl text-[26px] transition-shadow', holding && 'shadow-[0_0_0_2px_rgba(111,158,245,0.9),0_0_28px_rgba(111,158,245,0.6)]')} />
          </motion.div>
          <div className="min-w-0 flex-1"><LiveLine live={live} /></div>
          <Mascot size={58} glow={false}
            mood={phase === 'done' ? 'happy' : phase === 'error' ? 'error' : phase === 'listening' ? 'listening' : phase === 'thinking' ? 'thinking' : phase === 'answering' ? 'speaking' : 'idle'} />
        </div>
      </Card>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="text-[12px] text-white/35">Try saying</span>
        {SAY.map((line) => (
          <span key={line} className="rounded-full bg-white/[0.05] px-3 py-1 text-[12.5px] text-white/70 hairline">“{line}”</span>
        ))}
      </div>
    </Frame>
  )
}

function LiveLine({ live }: { live: LiveState | null }) {
  const phase = live?.phase ?? 'idle'
  const said = live?.transcript ? <span className="text-white/45">“{live.transcript}”</span> : null
  return (
    <AnimatePresence mode="wait">
      <motion.div key={phase} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}>
        {phase === 'idle' && (
          <>
            <div className="shimmer-text text-[17px] font-semibold tracking-tight">Waiting for you to hold the keys…</div>
            <div className="mt-1 text-[12.5px] text-white/40">They’re at the bottom left of your keyboard.</div>
          </>
        )}
        {phase === 'listening' && (
          <>
            <div className="text-[17px] font-semibold tracking-tight text-plip-200">Listening… let go when you’re done</div>
            <div className="mt-1 truncate text-[13px]">{said ?? <span className="text-white/35">Say anything</span>}</div>
          </>
        )}
        {phase === 'thinking' && (
          <>
            <div className="text-[17px] font-semibold tracking-tight">Thinking…</div>
            <div className="mt-1 truncate text-[13px]">{said}</div>
          </>
        )}
        {phase === 'answering' && (
          <>
            <div className="flex items-center gap-1.5 text-[17px] font-semibold tracking-tight">Look up at the top of your screen <ArrowUp className="size-4 text-plip-300" /></div>
            <div className="mt-1 line-clamp-2 text-[13px] text-white/50">{live?.answer}</div>
          </>
        )}
        {phase === 'done' && (
          <>
            <div className="flex items-center gap-2 text-[17px] font-semibold tracking-tight text-emerald-300"><Check className="size-4" strokeWidth={3} /> That’s all there is to it</div>
            <div className="mt-1 line-clamp-2 text-[13px] text-white/50">{live?.answer || 'Plip’s answer is up at the top of your screen.'}</div>
          </>
        )}
        {phase === 'error' && (
          <>
            <div className="text-[17px] font-semibold tracking-tight text-rose-200">Almost</div>
            <div className="mt-1 line-clamp-2 text-[13px] text-white/50">{live?.error || 'Hold both keys while you talk, then let go.'}</div>
          </>
        )}
      </motion.div>
    </AnimatePresence>
  )
}

// -- 4. sign in ------------------------------------------------------------------------------------

function SignInStep({ state, steps, next, back }: StepProps) {
  const user = state.account.user
  useAdvance(Boolean(user), next)
  return (
    <Frame
      step="signin"
      steps={steps}
      title={user ? 'You’re signed in' : 'Last step: sign in'}
      subtitle="Sign in with Google to keep using Plip. It’s free, and your account is only your name, email and picture."
      footer={<><Back onClick={back} />{user && <Forward onClick={next} glow />}</>}
    >
      {user ? (
        <Card className="flex items-center gap-4 p-5">
          <Avatar user={user} size={44} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-semibold tracking-tight">{user.name || user.email}</div>
            {user.name && <div className="truncate text-[12.5px] text-white/50">{user.email}</div>}
          </div>
          <Check className="size-5 text-emerald-300" strokeWidth={3} />
        </Card>
      ) : (
        <Card className="px-6 pb-3 pt-7"><GoogleSignIn state={state} /></Card>
      )}
      <div className="mt-4 flex items-center gap-1.5 text-[11.5px] text-white/35">
        <Lock className="size-3.5 shrink-0 text-mint" /> What you ask, your screen and your memory stay on this Mac.
        <button className="text-white/45 underline-offset-2 hover:text-white/70 hover:underline" onClick={() => send('open-url', { url: PRIVACY_URL })}>Privacy</button>
      </div>
    </Frame>
  )
}

// -- 5. all set ------------------------------------------------------------------------------------

const EXAMPLES = [
  { icon: Eye, text: 'What does this error mean?', hint: 'Reads your screen and explains' },
  { icon: MousePointer2, text: 'How do I export this as a PDF?', hint: 'Points at each step' },
  { icon: WandSparkles, text: 'Open Spotify and play my Discover Weekly', hint: 'Does it for you' },
  { icon: Hand, text: 'Remind me to call Mom at 6', hint: 'Reminders and timers' },
]

function AllSet() {
  return (
    <div>
      <div className="flex items-center gap-5">
        <div className="relative shrink-0">
          <div className="absolute inset-0 -z-10 scale-[1.7] rounded-full bg-plip-400/25 blur-3xl" />
          <Mascot size={84} mood="happy" />
        </div>
        <div>
          <h1 className="text-gradient text-[32px] font-semibold leading-[1.05] tracking-[-0.04em]">You’re all set</h1>
          <p className="mt-2 flex flex-wrap items-center gap-1.5 text-[14px] text-white/55">
            <ArrowUp className="size-4 text-plip-300" /> I’m up at the top of your screen. Hold <Chord className="h-6 min-w-6 text-[12px]" /> whenever you need a hand.
          </p>
        </div>
      </div>
      <div className="mb-2.5 mt-7 text-[11.5px] font-semibold uppercase tracking-[0.12em] text-white/35">Things to try first</div>
      <div className="grid grid-cols-2 gap-2.5">
        {EXAMPLES.map(({ icon: Icon, text, hint }, position) => (
          <motion.div key={text} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 + position * 0.05 }}>
            <Card className="flex items-center gap-3 p-3">
              <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-plip-400/15 text-plip-200"><Icon className="size-4" /></span>
              <div className="min-w-0">
                <div className="truncate text-[13px] font-medium text-white/90">“{text}”</div>
                <div className="truncate text-[11.5px] text-white/35">{hint}</div>
              </div>
            </Card>
          </motion.div>
        ))}
      </div>
      <div className="mt-4 text-[12px] text-white/35">You can change anything later in Settings, from the Plip icon in your menu bar.</div>
      <div className="mt-7 flex items-center">
        <Forward onClick={() => send('finish-onboarding')} glow>Start using Plip</Forward>
      </div>
    </div>
  )
}
