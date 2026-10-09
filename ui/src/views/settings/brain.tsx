import { ArrowRight, Check, Gauge, Plug, RotateCcw, Zap } from 'lucide-react'
import { send, type Engine, type KeyCheck, type SettingsState } from '../../bridge'
import { cn } from '../../components/bits'
import { ConnectAI, Connecting, ENGINE_GLYPH } from './connect'
import { money } from './usage'
import { Button, Card, Header, KeyField, Pill, Section, Segmented } from './ui'

const STATUS: Record<Engine['status'], [string, 'good' | 'warn' | 'muted']> = {
  ready: ['Ready', 'good'], unknown: ['Checking', 'muted'], 'not-installed': ['Not installed', 'warn'],
  'logged-out': ['Sign in needed', 'warn'], 'missing-key': ['Add key', 'warn'],
}

function EngineCard({ engine, check }: { engine: Engine; check?: KeyCheck | null }) {
  const glyph = ENGINE_GLYPH[engine.id] ?? { bg: 'bg-white/10', text: 'text-white', glyph: '•' }
  const connect = engine.connect
  const busy = connect?.state === 'installing' || connect?.state === 'signing-in'
  const [label, tone] = busy ? (['Connecting', 'muted'] as const) : STATUS[engine.status]
  const usable = !busy && (engine.status === 'ready' || engine.status === 'unknown')
  const canConnect = engine.kind === 'subscription' && !busy && (engine.status === 'not-installed' || engine.status === 'logged-out')

  return (
    <Card active={engine.selected} className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <span className={cn('grid size-10 shrink-0 place-items-center rounded-xl text-[18px] font-bold shadow-lg', glyph.bg, glyph.text)}>{glyph.glyph}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[14px] font-semibold tracking-tight">{engine.label}</span>
            {engine.kind === 'subscription' && <span className="shrink-0 rounded-md bg-plip-400/15 px-1.5 py-0.5 text-[9.5px] font-bold uppercase tracking-wider text-plip-200">Plan</span>}
          </div>
          <div className="mt-1"><Pill tone={tone}>{label}</Pill></div>
        </div>
      </div>
      <div className="text-[12px] font-medium text-white/55">{engine.via}</div>
      {engine.detail && <div className="-mt-1.5 text-[12px] leading-relaxed text-white/40">{engine.detail}</div>}
      <div className="mt-auto flex items-center gap-2">
        {usable && !engine.selected && (
          <Button onClick={() => send('select-engine', { id: engine.id })}>
            Use {engine.label} <ArrowRight className="size-3.5" />
          </Button>
        )}
        {engine.selected && usable && (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-emerald-300">
            <Check className="size-4" strokeWidth={3} /> In use
          </span>
        )}
        {canConnect && (
          <Button variant="brand" onClick={() => send('engine-connect', { id: engine.id })}>
            {connect?.state === 'failed' ? <RotateCcw className="size-3.5" /> : <Plug className="size-3.5" />}
            {connect?.state === 'failed' ? 'Try again' : `Connect ${engine.label}`}
          </Button>
        )}
      </div>
      {connect && (busy || connect.state === 'failed') && <Connecting id={engine.id} progress={connect} />}
      {engine.status === 'missing-key' && engine.keyName && <KeyField name={engine.keyName} placeholder={`Paste ${engine.keyName}`} saved={false} />}
      {check && check.state !== 'ok' && (
        <div className={cn('text-[12px]', check.state === 'bad' ? 'text-rose-200/90' : 'text-white/55')}>{check.message}</div>
      )}
    </Card>
  )
}

export function BrainTab({ state }: { state: SettingsState }) {
  const plans = state.engines.filter((engine) => engine.kind === 'subscription')
  const keys = state.engines.filter((engine) => engine.kind !== 'subscription')
  return (
    <div>
      <Header
        eyebrow="Brain"
        title="The AI Plip thinks with"
        subtitle="Use the plan you already pay for (Claude, ChatGPT or Cursor): one click connects it, with no extra bill. No plan? Google’s Gemini is free."
      />
      <div className="mb-6"><ConnectAI state={state} /></div>
      {state.usage && state.usage.periods['30'].requests > 0 && (
        <a href="#settings?tab=activity" className="card card-hover mb-6 flex items-center gap-3 px-4 py-3 text-[12.5px] text-white/60">
          <span className="text-gradient text-[15px] font-semibold">{money(state.usage.periods['30'].cost)}</span>
          <span>worth of {state.usage.periods['30'].requests} requests at API prices in the last 30 days{state.usage.billed > 0 ? '' : ', covered by your plan'}.</span>
          <span className="ml-auto inline-flex items-center gap-1 font-semibold text-white/80">See usage <ArrowRight className="size-3.5" /></span>
        </a>
      )}
      <Section title="Your subscriptions">
        <div className="grid grid-cols-2 gap-3">{plans.map((engine) => <EngineCard key={engine.id} engine={engine} />)}</div>
      </Section>
      <Section title="API keys">
        <div className="grid grid-cols-2 gap-3">
          {keys.map((engine) => <EngineCard key={engine.id} engine={engine} check={state.keyCheck?.name === engine.keyName ? state.keyCheck : null} />)}
        </div>
      </Section>
      <div className="grid grid-cols-2 gap-3">
        <Card>
          <div className="mb-1 flex items-center gap-2 text-[13.5px] font-semibold"><Gauge className="size-4 text-plip-300" /> Reasoning depth</div>
          <div className="mb-3 text-[12px] text-white/40">Fast answers, or deeper thinking for hard questions.</div>
          <Segmented
            value={state.depth}
            options={[{ value: 'fast', label: 'Fast' }, { value: 'balanced', label: 'Balanced' }, { value: 'deep', label: 'Deep' }]}
            onChange={(depth) => send('set-depth', { depth })}
          />
        </Card>
        <Card>
          <div className="mb-1 flex items-center justify-between">
            <div className="flex items-center gap-2 text-[13.5px] font-semibold"><Zap className="size-4 text-sun" /> Jev fast router</div>
            {state.jev.configured ? <Pill tone="good">{state.jev.latencyMs ? `${state.jev.latencyMs} ms` : 'On'}</Pill> : <Pill tone="muted">Optional</Pill>}
          </div>
          <div className="mb-3 text-[12px] leading-relaxed text-white/40">A ~100 ms System-1 model decides if Plip needs your screen, which monitor, and which control to snap to.</div>
          <KeyField name="TYPESAFE_API_KEY" placeholder="TypeSafe key (console.typesafe.ai)" saved={state.jev.configured} />
        </Card>
      </div>
    </div>
  )
}
