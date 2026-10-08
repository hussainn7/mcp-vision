import { motion } from 'motion/react'
import { ArrowDownToLine, CalendarClock, Check, ChevronRight, Clock3, FolderSearch, Plane, Type, TrendingUp, WandSparkles, Zap } from 'lucide-react'
import { send, type SettingsState } from '../../bridge'
import { Mascot } from '../../components/Mascot'
import { Chord, cn } from '../../components/bits'
import type { Tab } from './index'
import { Button, Card, Section, Stat } from './ui'

const TRY = [
  { icon: FolderSearch, text: 'Find my lease PDF', hint: 'Spotlight search', tint: 'from-sky-300 to-cyan-400' },
  { icon: WandSparkles, text: 'Tidy up my desktop', hint: 'Sorted into folders, undoable', tint: 'from-teal-200 to-emerald-400' },
  { icon: Type, text: 'Make this email friendlier', hint: 'Rewrites your selection', tint: 'from-violet-200 to-indigo-400' },
  { icon: CalendarClock, text: 'Remind me to call Mom at 6', hint: 'Reminders & timers', tint: 'from-amber-200 to-orange-400' },
  { icon: Plane, text: 'Flights to Miami next Friday', hint: 'Google Flights + summary', tint: 'from-indigo-200 to-sky-400' },
  { icon: Zap, text: 'How do I turn on 2FA in GitHub?', hint: 'Step-by-step checklist', tint: 'from-fuchsia-200 to-pink-400' },
]

function greeting(): string {
  const hour = new Date().getHours()
  return hour < 5 ? 'Up late' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}

export function HomeTab({ state, go }: { state: SettingsState; go: (tab: Tab) => void }) {
  const perms = state.permissions
  const permsReady = perms.screen === true && perms.accessibility === true && perms.microphone !== false
  const engine = state.engines.find((item) => item.selected)
  const brainReady = engine?.status === 'ready'
  const known = (state.memory?.facts.length ?? 0) > 0
  const steps = [
    { label: 'Let Plip see and hear', done: permsReady, tab: 'permissions' as Tab, detail: 'Screen, Accessibility, Microphone' },
    { label: 'Connect your AI', done: brainReady, tab: 'brain' as Tab, detail: engine ? `${engine.label} · ${engine.via}` : 'Use your Claude, ChatGPT, Cursor or Gemini plan' },
    { label: 'Tell Plip about you', done: known, tab: 'memory' as Tab, detail: known ? `${state.memory!.facts.length} things saved` : 'Import from Contacts, your browser, or ChatGPT' },
  ]
  const progress = steps.filter((step) => step.done).length / steps.length
  const first = state.memory?.profile['name.first']

  const update = state.update.available

  return (
    <div>
      {update && (
        <Card className="mb-6 flex items-center gap-4 glow-ring">
          <span className="grid size-9 shrink-0 place-items-center rounded-xl brand-gradient text-slate-950"><ArrowDownToLine className="size-4" /></span>
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] font-semibold tracking-tight">Plip {update.version} is out</div>
            <div className="text-[12px] text-white/45">You have {state.update.current}. Download it, quit Plip, then drag the new one into Applications.</div>
          </div>
          <Button variant="quiet" onClick={() => send('open-url', { url: update.page })}>What’s new</Button>
          <Button variant="brand" onClick={() => send('update-download')}>Download</Button>
        </Card>
      )}
      <div className="relative mb-7 flex items-center gap-6">
        <div className="relative">
          <div className="absolute inset-0 -z-10 scale-[1.7] rounded-full bg-plip-400/25 blur-3xl" />
          <Mascot size={92} mood={progress === 1 ? 'happy' : 'idle'} />
        </div>
        <div className="flex-1">
          <div className="mb-1 text-[13px] font-medium text-white/45">{greeting()}{first ? `, ${first}` : ''}</div>
          <h1 className="text-gradient text-[34px] font-semibold leading-[1.05] tracking-[-0.04em]">What should we get done?</h1>
          <div className="mt-3 flex items-center gap-2 text-[13px] text-white/50">
            Hold <Chord /> and ask, or tell Plip to do it for you.
          </div>
        </div>
      </div>

      <div className="mb-7 grid grid-cols-3 gap-3">
        <Stat icon={Clock3} value={`${state.stats.minutesSaved}`} label="minutes saved" />
        <Stat icon={TrendingUp} value={`${state.stats.actionsWeek}`} label="things done this week" tone="text-dew" />
        <Stat icon={Zap} value={`${state.stats.answers}`} label="questions answered" tone="text-sun" />
      </div>

      {progress < 1 && (
        <Card className="mb-7">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-[13px] font-semibold">Finish setting up</div>
            <div className="font-mono text-[11px] text-white/40">{Math.round(progress * 100)}%</div>
          </div>
          <div className="mb-3 h-1 overflow-hidden rounded-full bg-white/[0.06]">
            <motion.div className="h-full rounded-full brand-gradient" animate={{ width: `${progress * 100}%` }} transition={{ type: 'spring', stiffness: 120, damping: 20 }} />
          </div>
          <div className="space-y-0.5">
            {steps.map((step, index) => (
              <button key={step.label} onClick={() => go(step.tab)} className="group flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition hover:bg-white/[0.04]">
                <span className={cn('grid size-6 place-items-center rounded-full text-[11px] font-bold', step.done ? 'bg-mint/15 text-emerald-300' : 'bg-white/[0.07] text-white/50')}>
                  {step.done ? <Check className="size-3.5" strokeWidth={3} /> : index + 1}
                </span>
                <div className="flex-1">
                  <div className={cn('text-[13.5px] font-medium', step.done ? 'text-white/55' : 'text-white')}>{step.label}</div>
                  <div className="text-[12px] text-white/35">{step.detail}</div>
                </div>
                <ChevronRight className="size-4 text-white/25 transition group-hover:translate-x-0.5 group-hover:text-white/60" />
              </button>
            ))}
          </div>
        </Card>
      )}


      <Section title="Try saying">
        <div className="grid grid-cols-2 gap-2.5">
          {TRY.map(({ icon: Icon, text, hint, tint }) => (
            <Card key={text} className="flex items-center gap-3 p-3">
              <span className={cn('grid size-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br text-slate-950', tint)}>
                <Icon className="size-4" />
              </span>
              <div className="min-w-0">
                <div className="truncate text-[13px] font-medium text-white/90">“{text}”</div>
                <div className="truncate text-[11.5px] text-white/35">{hint}</div>
              </div>
            </Card>
          ))}
        </div>
      </Section>
    </div>
  )
}
