import { AnimatePresence, motion } from 'motion/react'
import {
  ArrowRight, AudioLines, BrainCircuit, Check, ChevronRight, Clock3, Copy, Eye, Gauge, Hand, Home, Info,
  KeyRound, Lock, MessageSquareText, Mic, MonitorUp, MousePointer2, ShieldCheck, Sparkles, Zap,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { send, settings, useStore, type Engine, type SettingsState } from '../bridge'
import { Mascot } from '../components/Mascot'
import { Keycap, cn } from '../components/bits'

type Tab = 'home' | 'brain' | 'voice' | 'permissions' | 'history' | 'about'

const TABS: { id: Tab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'home', label: 'Home', icon: Home },
  { id: 'brain', label: 'Brain', icon: BrainCircuit },
  { id: 'voice', label: 'Voice', icon: AudioLines },
  { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
  { id: 'history', label: 'History', icon: Clock3 },
  { id: 'about', label: 'About', icon: Info },
]

export function Settings() {
  const state = useStore(settings)
  const [tab, setTab] = useState<Tab>(() => (new URLSearchParams(location.hash.split('?')[1]).get('tab') as Tab) || 'home')

  useEffect(() => {
    const onHash = () => {
      const next = new URLSearchParams(location.hash.split('?')[1]).get('tab') as Tab | null
      if (next) setTab(next)
    }
    window.addEventListener('hashchange', onHash)
    send('settings-ready')
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  return (
    <div className="relative flex h-full overflow-hidden bg-panel text-white noise">
      <div className="pointer-events-none absolute -left-40 -top-56 size-[520px] rounded-full bg-blip-500/20 blur-[120px] animate-aurora" />
      <div className="pointer-events-none absolute -right-40 top-40 size-[420px] rounded-full bg-violet-glow/15 blur-[120px] animate-aurora [animation-delay:-6s]" />

      <aside className="relative z-10 flex w-[208px] shrink-0 flex-col border-r border-white/[0.06] bg-black/20 px-3 py-4 backdrop-blur-xl">
        <div className="mb-5 flex items-center gap-2.5 px-2">
          <Mascot size={30} mood="happy" glow={false} />
          <div>
            <div className="text-[15px] font-semibold tracking-tight">Blip</div>
            <div className="font-mono text-[10.5px] text-white/35">v{state.version}</div>
          </div>
        </div>
        <nav className="space-y-0.5">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                'relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition',
                tab === id ? 'text-white' : 'text-white/50 hover:bg-white/[0.04] hover:text-white/80',
              )}
            >
              {tab === id && (
                <motion.span layoutId="tab" className="absolute inset-0 rounded-lg bg-white/[0.07] hairline" transition={{ type: 'spring', stiffness: 500, damping: 38 }} />
              )}
              <Icon className="relative size-4" />
              <span className="relative">{label}</span>
            </button>
          ))}
        </nav>
        <div className="mt-auto rounded-xl bg-white/[0.03] p-3 hairline">
          <div className="mb-2 text-[11px] font-medium text-white/45">Talk to Blip</div>
          <div className="flex items-center gap-1.5">
            <Keycap>⌃</Keycap>
            <span className="text-white/30">+</span>
            <Keycap>⌥</Keycap>
            <span className="ml-1 text-[11px] text-white/40">hold</span>
          </div>
        </div>
      </aside>

      <main className="relative z-10 flex-1 overflow-y-auto scrollbar-none px-9 py-8">
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 8, filter: 'blur(6px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -6, filter: 'blur(4px)' }}
            transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
          >
            {tab === 'home' && <HomeTab state={state} go={setTab} />}
            {tab === 'brain' && <BrainTab state={state} />}
            {tab === 'voice' && <VoiceTab state={state} />}
            {tab === 'permissions' && <PermissionsTab state={state} />}
            {tab === 'history' && <HistoryTab state={state} />}
            {tab === 'about' && <AboutTab state={state} />}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}

// -- shared ---------------------------------------------------------------------

function Header({ title, subtitle, eyebrow }: { title: string; subtitle: string; eyebrow?: string }) {
  return (
    <div className="mb-7">
      {eyebrow && <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-blip-300">{eyebrow}</div>}
      <h1 className="text-[26px] font-semibold tracking-[-0.03em]">{title}</h1>
      <p className="mt-1.5 max-w-[520px] text-[14px] leading-relaxed text-white/50">{subtitle}</p>
    </div>
  )
}

function Card({ children, className, active }: { children: React.ReactNode; className?: string; active?: boolean }) {
  return <div className={cn('relative rounded-2xl p-4 glass transition', active && 'glow-ring', className)}>{children}</div>

}

function Button({ children, onClick, variant = 'primary', className, disabled }: {
  children: React.ReactNode
  onClick?: () => void
  variant?: 'primary' | 'ghost' | 'quiet'
  className?: string
  disabled?: boolean
}) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold tracking-tight transition active:scale-[0.97] disabled:opacity-40',
        variant === 'primary' &&
          'bg-gradient-to-b from-blip-400 to-blip-600 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.3),0_8px_20px_-8px_rgba(61,107,255,0.9)] hover:brightness-110',
        variant === 'ghost' && 'bg-white/[0.07] text-white/85 hairline hover:bg-white/[0.11]',
        variant === 'quiet' && 'text-white/55 hover:text-white',
        className,
      )}
    >
      {children}
    </button>
  )
}

function Pill({ tone, children }: { tone: 'good' | 'warn' | 'bad' | 'muted'; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold',
        tone === 'good' && 'bg-emerald-400/12 text-emerald-300',
        tone === 'warn' && 'bg-amber-400/12 text-amber-200',
        tone === 'bad' && 'bg-red-400/12 text-red-300',
        tone === 'muted' && 'bg-white/[0.06] text-white/45',
      )}
    >
      <span className={cn('size-1.5 rounded-full', tone === 'good' ? 'bg-emerald-300' : tone === 'warn' ? 'bg-amber-300' : tone === 'bad' ? 'bg-red-300' : 'bg-white/30')} />
      {children}
    </span>
  )
}

function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (value: T) => void }) {
  return (
    <div className="inline-flex rounded-full bg-black/40 p-1 hairline">
      {options.map((option) => (
        <button
          key={option.value}
          onClick={() => onChange(option.value)}
          className={cn('relative rounded-full px-3 py-1 text-[12px] font-semibold transition', value === option.value ? 'text-white' : 'text-white/45 hover:text-white/75')}
        >
          {value === option.value && (
            <motion.span layoutId={`seg-${options.map((o) => o.value).join()}`} className="absolute inset-0 rounded-full bg-white/[0.12] hairline" transition={{ type: 'spring', stiffness: 500, damping: 36 }} />
          )}
          <span className="relative">{option.label}</span>
        </button>
      ))}
    </div>
  )
}

function KeyField({ name, placeholder, saved }: { name: string; placeholder: string; saved: boolean }) {
  const [value, setValue] = useState('')
  const [editing, setEditing] = useState(!saved)
  useEffect(() => setEditing(!saved), [saved])
  if (!editing)
    return (
      <div className="flex items-center gap-2">
        <Pill tone="good">Key saved</Pill>
        <Button variant="quiet" onClick={() => setEditing(true)}>Replace</Button>
      </div>
    )
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (!value.trim()) return
        send('set-key', { name, value: value.trim() })
        setValue('')
      }}
    >
      <div className="relative flex-1">
        <KeyRound className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-white/30" />
        <input
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={placeholder}
          className="h-9 w-full rounded-full bg-black/40 pl-8 pr-3 font-mono text-[12px] text-white outline-none hairline placeholder:font-sans placeholder:text-white/25 focus:shadow-[inset_0_0_0_1px_rgba(143,180,255,0.6)]"
        />
      </div>
      <Button disabled={!value.trim()}>Save</Button>
    </form>
  )
}

// -- tabs ---------------------------------------------------------------------------

function HomeTab({ state, go }: { state: SettingsState; go: (tab: Tab) => void }) {
  const perms = state.permissions
  const permsReady = perms.screen === true && perms.accessibility === true && perms.microphone !== false
  const engine = state.engines.find((item) => item.selected)
  const brainReady = engine?.status === 'ready'
  const steps = [
    { label: 'Let Blip see and hear', done: permsReady, tab: 'permissions' as Tab, detail: 'Screen, Accessibility, Mic' },
    { label: 'Pick a brain', done: brainReady, tab: 'brain' as Tab, detail: engine ? `${engine.label} · ${engine.via}` : 'Use your Claude, ChatGPT or Cursor plan' },
    { label: 'Give it a voice', done: state.voice.tts !== 'off', tab: 'voice' as Tab, detail: state.voice.elevenlabs ? 'ElevenLabs' : 'macOS voice' },
  ]
  const progress = steps.filter((step) => step.done).length / steps.length

  return (
    <div>
      <div className="relative mb-8 flex items-center gap-7">
        <div className="relative">
          <div className="absolute inset-0 -z-10 scale-150 rounded-full bg-blip-500/30 blur-3xl" />
          <Mascot size={118} mood={progress === 1 ? 'happy' : 'idle'} />
        </div>
        <div>
          <div className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-2.5 py-1 text-[11px] font-semibold text-blip-300 hairline">
            <Sparkles className="size-3" /> Lives in your notch
          </div>
          <h1 className="text-gradient text-[38px] font-semibold leading-[1.05] tracking-[-0.04em]">Meet Blip.</h1>
          <p className="mt-2 max-w-[400px] text-[14.5px] leading-relaxed text-white/55">
            Your AI buddy for the Mac. Hold <span className="text-white/80">Control + Option</span>, ask anything out loud, and Blip
            looks at your screen, talks you through it, and flies over to exactly where you need to click.
          </p>
        </div>
      </div>

      <Card className="mb-5">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-[13px] font-semibold">Get set up</div>
          <div className="font-mono text-[11px] text-white/40">{Math.round(progress * 100)}%</div>
        </div>
        <div className="mb-4 h-1 overflow-hidden rounded-full bg-white/[0.06]">
          <motion.div className="h-full rounded-full bg-gradient-to-r from-blip-400 via-violet-glow to-rose-glow" animate={{ width: `${progress * 100}%` }} transition={{ type: 'spring', stiffness: 120, damping: 20 }} />
        </div>
        <div className="space-y-1">
          {steps.map((step, index) => (
            <button key={step.label} onClick={() => go(step.tab)} className="group flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition hover:bg-white/[0.04]">
              <span className={cn('grid size-6 place-items-center rounded-full text-[11px] font-bold', step.done ? 'bg-emerald-400/15 text-emerald-300' : 'bg-white/[0.07] text-white/50')}>
                {step.done ? <Check className="size-3.5" strokeWidth={3} /> : index + 1}
              </span>
              <div className="flex-1">
                <div className={cn('text-[13.5px] font-medium', step.done ? 'text-white/60' : 'text-white')}>{step.label}</div>
                <div className="text-[12px] text-white/35">{step.detail}</div>
              </div>
              <ChevronRight className="size-4 text-white/25 transition group-hover:translate-x-0.5 group-hover:text-white/60" />
            </button>
          ))}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-3">
        {[
          { icon: Eye, title: 'Sees your screen', text: 'Every display, cursor screen first. Only when the question needs it.' },
          { icon: MousePointer2, title: 'Points at things', text: 'Flies to the exact button, menu, or field, snapped to the real control.' },
          { icon: MessageSquareText, title: 'Walks you through it', text: 'Multi-step guides that wait for you and check your screen each step.' },
          { icon: Zap, title: 'Uses your plan', text: 'Runs on your Claude, ChatGPT or Cursor subscription. No new bill.' },
        ].map(({ icon: Icon, title, text }) => (
          <Card key={title} className="p-4">
            <Icon className="mb-3 size-4.5 text-blip-300" />
            <div className="text-[13.5px] font-semibold tracking-tight">{title}</div>
            <div className="mt-1 text-[12.5px] leading-relaxed text-white/45">{text}</div>
          </Card>
        ))}
      </div>
    </div>
  )
}

const ENGINE_GLYPH: Record<string, { bg: string; text: string; glyph: string }> = {
  'claude-code': { bg: 'bg-[#d97757]', text: 'text-white', glyph: '✳' },
  codex: { bg: 'bg-white', text: 'text-black', glyph: '◎' },
  cursor: { bg: 'bg-[#0f0f12] hairline', text: 'text-white', glyph: '▲' },
  gemini: { bg: 'bg-gradient-to-br from-[#4f7cff] to-[#b46bff]', text: 'text-white', glyph: '✦' },
  anthropic: { bg: 'bg-[#e8dccf]', text: 'text-[#1f1b16]', glyph: 'A' },
  openrouter: { bg: 'bg-gradient-to-br from-[#6467f2] to-[#3b3fd1]', text: 'text-white', glyph: '⇄' },
}

function EngineCard({ engine }: { engine: Engine }) {
  const glyph = ENGINE_GLYPH[engine.id] ?? { bg: 'bg-white/10', text: 'text-white', glyph: '•' }
  const tone = engine.status === 'ready' ? 'good' : engine.status === 'unknown' ? 'muted' : 'warn'
  const statusLabel = { ready: 'Ready', 'not-installed': 'Not installed', 'logged-out': 'Sign in needed', 'missing-key': 'Add key', unknown: 'Checking' }[engine.status]
  const [copied, setCopied] = useState(false)

  return (
    <Card active={engine.selected} className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <span className={cn('grid size-10 shrink-0 place-items-center rounded-xl text-[18px] font-bold shadow-lg', glyph.bg, glyph.text)}>{glyph.glyph}</span>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span className="truncate text-[14px] font-semibold tracking-tight">{engine.label}</span>
          {engine.kind === 'subscription' && <span className="shrink-0 rounded-md bg-violet-glow/15 px-1.5 py-0.5 text-[9.5px] font-bold uppercase tracking-wider text-violet-300">Plan</span>}
        </div>
        <span className="shrink-0"><Pill tone={tone}>{statusLabel}</Pill></span>
      </div>
      <div className="-mt-1 text-[12px] font-medium text-white/50">{engine.via}</div>
      {engine.detail && <div className="text-[12px] leading-relaxed text-white/45">{engine.detail}</div>}
      <div className="mt-auto flex items-center gap-2">
        {engine.status === 'ready' && !engine.selected && (
          <Button onClick={() => send('select-engine', { id: engine.id })}>
            Use {engine.label} <ArrowRight className="size-3.5" />
          </Button>
        )}
        {engine.selected && engine.status === 'ready' && (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-emerald-300">
            <Check className="size-4" strokeWidth={3} /> In use
          </span>
        )}
        {engine.status === 'not-installed' && engine.install && (
          <Button
            variant="ghost"
            onClick={() => {
              send('copy', { text: engine.install })
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1600)
            }}
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} {copied ? 'Copied' : 'Copy install command'}
          </Button>
        )}
        {engine.status === 'logged-out' && (
          <Button variant="ghost" onClick={() => send('engine-login', { id: engine.id })}>
            <Lock className="size-3.5" /> Sign in
          </Button>
        )}
      </div>
      {engine.status === 'missing-key' && engine.keyName && <KeyField name={engine.keyName} placeholder={`Paste ${engine.keyName}`} saved={false} />}
    </Card>
  )
}

function BrainTab({ state }: { state: SettingsState }) {
  const plans = state.engines.filter((engine) => engine.kind === 'subscription')
  const keys = state.engines.filter((engine) => engine.kind !== 'subscription')
  return (
    <div>
      <Header eyebrow="Brain" title="Use the AI you already pay for" subtitle="Blip can think with your existing Claude, ChatGPT or Cursor subscription through their official apps, or with an API key. Pick one; switch anytime." />
      <div className="mb-3 text-[12px] font-semibold uppercase tracking-[0.1em] text-white/35">Your subscriptions</div>
      <div className="mb-7 grid grid-cols-2 gap-3">
        {plans.map((engine) => <EngineCard key={engine.id} engine={engine} />)}
      </div>
      <div className="mb-3 text-[12px] font-semibold uppercase tracking-[0.1em] text-white/35">API keys</div>
      <div className="mb-7 grid grid-cols-2 gap-3">
        {keys.map((engine) => <EngineCard key={engine.id} engine={engine} />)}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Card>
          <div className="mb-1 flex items-center gap-2 text-[13.5px] font-semibold"><Gauge className="size-4 text-blip-300" /> Reasoning depth</div>
          <div className="mb-3 text-[12px] text-white/40">Fast answers, or deeper thinking for hard questions.</div>
          <Segmented
            value={state.depth}
            options={[{ value: 'fast', label: 'Fast' }, { value: 'balanced', label: 'Balanced' }, { value: 'deep', label: 'Deep' }]}
            onChange={(depth) => send('set-depth', { depth })}
          />
          <label className="mt-4 flex cursor-pointer items-center justify-between gap-3">
            <span>
              <span className="block text-[12.5px] font-medium">Guided walkthroughs</span>
              <span className="block text-[11.5px] text-white/40">Waits for you, re-checks the screen each step</span>
            </span>
            <Toggle checked={state.walkthroughs} onChange={(enabled) => send('set-walkthroughs', { enabled })} />
          </label>
        </Card>
        <Card>
          <div className="mb-1 flex items-center justify-between">
            <div className="flex items-center gap-2 text-[13.5px] font-semibold"><Zap className="size-4 text-amber-300" /> Jev fast router</div>
            {state.jev.configured ? <Pill tone="good">{state.jev.latencyMs ? `${state.jev.latencyMs} ms` : 'On'}</Pill> : <Pill tone="muted">Optional</Pill>}
          </div>
          <div className="mb-3 text-[12px] leading-relaxed text-white/40">A ~100 ms System-1 model decides if Blip needs your screen, which monitor, and which control to snap to.</div>
          <KeyField name="TYPESAFE_API_KEY" placeholder="TypeSafe key (console.typesafe.ai)" saved={state.jev.configured} />
        </Card>
      </div>
    </div>
  )
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn('relative h-6 w-10 shrink-0 rounded-full transition', checked ? 'bg-blip-500' : 'bg-white/[0.12]')}
    >
      <motion.span layout transition={{ type: 'spring', stiffness: 600, damping: 32 }} className={cn('absolute top-0.5 size-5 rounded-full bg-white shadow', checked ? 'right-0.5' : 'left-0.5')} />
    </button>
  )
}

function VoiceTab({ state }: { state: SettingsState }) {
  return (
    <div>
      <Header eyebrow="Voice" title="How Blip sounds and listens" subtitle="Works out of the box with macOS voices and on-device recognition. Add keys for a more natural voice and faster streaming transcription." />
      <div className="space-y-3">
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <div>
              <div className="text-[13.5px] font-semibold">Speaking</div>
              <div className="text-[12px] text-white/40">ElevenLabs Flash streams sentence by sentence</div>
            </div>
            <Segmented
              value={state.voice.tts}
              options={[{ value: 'elevenlabs', label: 'ElevenLabs' }, { value: 'say', label: 'macOS' }, { value: 'off', label: 'Muted' }]}
              onChange={(tts) => send('set-voice', { tts })}
            />
          </div>
          {state.voice.tts === 'elevenlabs' && <KeyField name="ELEVENLABS_API_KEY" placeholder="ElevenLabs API key" saved={state.voice.elevenlabs} />}
          <div className="mt-3">
            <Button variant="ghost" onClick={() => send('test-voice')}><AudioLines className="size-3.5" /> Test voice</Button>
          </div>
        </Card>
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <div>
              <div className="text-[13.5px] font-semibold">Listening</div>
              <div className="text-[12px] text-white/40">Only while you hold Control + Option</div>
            </div>
            <Segmented
              value={state.voice.stt}
              options={[{ value: 'assemblyai', label: 'AssemblyAI' }, { value: 'apple', label: 'On-device' }]}
              onChange={(stt) => send('set-voice', { stt })}
            />
          </div>
          {state.voice.stt === 'assemblyai' && <KeyField name="ASSEMBLYAI_API_KEY" placeholder="AssemblyAI API key" saved={state.voice.assemblyai} />}
        </Card>
      </div>
    </div>
  )
}

function PermissionsTab({ state }: { state: SettingsState }) {
  const rows = [
    { key: 'screen', icon: MonitorUp, title: 'Screen Recording', why: 'So Blip can see what you are asking about.' },
    { key: 'accessibility', icon: Hand, title: 'Accessibility', why: 'For the Control + Option shortcut and pixel-perfect pointing.' },
    { key: 'microphone', icon: Mic, title: 'Microphone', why: 'Only while you hold the shortcut. Audio is never saved.' },
    { key: 'speech', icon: MessageSquareText, title: 'Speech Recognition', why: 'On-device transcription when AssemblyAI is off.' },
  ] as const
  return (
    <div>
      <Header eyebrow="Privacy first" title="Permissions" subtitle="macOS asks once for each. Blip only looks when you ask, and never sends anything you did not trigger." />
      <Card className="divide-y divide-white/[0.05] p-0">
        {rows.map(({ key, icon: Icon, title, why }) => {
          const value = state.permissions[key]
          return (
            <div key={key} className="flex items-center gap-4 px-5 py-4">
              <span className="grid size-9 place-items-center rounded-xl bg-white/[0.06] hairline"><Icon className="size-4 text-white/75" /></span>
              <div className="flex-1">
                <div className="text-[13.5px] font-semibold">{title}</div>
                <div className="text-[12px] text-white/40">{why}</div>
              </div>
              {value === true ? <Pill tone="good">Granted</Pill> : (
                <Button variant={value === false ? 'primary' : 'ghost'} onClick={() => send('grant', { permission: key })}>
                  {value === false ? 'Open Settings' : 'Allow'}
                </Button>
              )}
            </div>
          )
        })}
      </Card>
      <div className="mt-4 flex justify-end">
        <Button variant="quiet" onClick={() => send('refresh')}>Check again</Button>
      </div>
    </div>
  )
}

function HistoryTab({ state }: { state: SettingsState }) {
  if (!state.history.length)
    return (
      <div className="grid h-[420px] place-items-center text-center">
        <div>
          <Mascot size={84} mood="idle" className="mx-auto mb-4" />
          <div className="text-[15px] font-semibold">Nothing yet</div>
          <div className="mt-1 text-[13px] text-white/40">Hold Control + Option and ask Blip something.</div>
        </div>
      </div>
    )
  return (
    <div>
      <Header eyebrow="History" title="Recent questions" subtitle="Kept on this Mac only. Clear it anytime." />
      <div className="space-y-2">
        {state.history.slice().reverse().map((item) => (
          <Card key={item.at} className="p-4">
            <div className="mb-1 flex items-center justify-between gap-3">
              <div className="text-[13.5px] font-semibold tracking-tight">{item.question}</div>
              <div className="shrink-0 font-mono text-[10.5px] text-white/30">{new Date(item.at * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</div>
            </div>
            <div className="line-clamp-2 text-[12.5px] leading-relaxed text-white/50">{item.answer}</div>
          </Card>
        ))}
      </div>
      <div className="mt-4 flex justify-end">
        <Button variant="quiet" onClick={() => send('clear-history')}>Clear history</Button>
      </div>
    </div>
  )
}

function AboutTab({ state }: { state: SettingsState }) {
  return (
    <div className="grid h-[460px] place-items-center text-center">
      <div>
        <Mascot size={96} mood="happy" className="mx-auto mb-5" />
        <div className="text-gradient text-[28px] font-semibold tracking-[-0.03em]">Blip</div>
        <div className="mt-1 font-mono text-[12px] text-white/35">version {state.version}</div>
        <p className="mx-auto mt-4 max-w-[360px] text-[13px] leading-relaxed text-white/45">
          An open-source AI buddy for the Mac. Built on MCP-Vision, Claude, and TypeSafe Jev.
        </p>
        <div className="mt-5 flex justify-center gap-2">
          <Button variant="ghost" onClick={() => send('open-url', { url: 'https://github.com/hussainn7/mcp-vision' })}>GitHub</Button>
          <Button variant="ghost" onClick={() => send('quit')}>Quit Blip</Button>
        </div>
      </div>
    </div>
  )
}
