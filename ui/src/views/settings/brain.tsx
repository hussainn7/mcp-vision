import { ArrowRight, Check, Copy, Gauge, Lock, Zap } from 'lucide-react'
import { useState } from 'react'
import { send, type Engine, type SettingsState } from '../../bridge'
import { cn } from '../../components/bits'
import { ConnectAI } from './connect'
import { Button, Card, Header, KeyField, Pill, Section, Segmented, Toggle } from './ui'

const ENGINE_GLYPH: Record<string, { bg: string; text: string; glyph: string }> = {
  'claude-code': { bg: 'bg-[#d97757]', text: 'text-white', glyph: '✳' },
  codex: { bg: 'bg-white', text: 'text-black', glyph: '◎' },
  cursor: { bg: 'bg-[#111114] hairline', text: 'text-white', glyph: '▲' },
  gemini: { bg: 'bg-gradient-to-br from-[#4f7cff] to-[#b46bff]', text: 'text-white', glyph: '✦' },
  anthropic: { bg: 'bg-[#e8dccf]', text: 'text-[#1f1b16]', glyph: 'A' },
}
const STATUS: Record<Engine['status'], [string, 'good' | 'warn' | 'muted']> = {
  ready: ['Ready', 'good'], unknown: ['Checking', 'muted'], 'not-installed': ['Not installed', 'warn'],
  'logged-out': ['Sign in needed', 'warn'], 'missing-key': ['Add key', 'warn'],
}

function EngineCard({ engine }: { engine: Engine }) {
  const glyph = ENGINE_GLYPH[engine.id] ?? { bg: 'bg-white/10', text: 'text-white', glyph: '•' }
  const [label, tone] = STATUS[engine.status]
  const [copied, setCopied] = useState(false)
  const usable = engine.status === 'ready' || engine.status === 'unknown'

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

export function BrainTab({ state }: { state: SettingsState }) {
  const plans = state.engines.filter((engine) => engine.kind === 'subscription')
  const keys = state.engines.filter((engine) => engine.kind !== 'subscription')
  return (
    <div>
      <Header
        eyebrow="Brain"
        title="Use the AI you already pay for"
        subtitle="Plip thinks with your Claude, ChatGPT, Cursor or Gemini plan through their official command-line apps, with their tools switched off. No extra bill. Or paste an API key."
      />
      <div className="mb-6"><ConnectAI state={state} /></div>
      <Section title="Or pick one yourself">
        <div className="grid grid-cols-2 gap-3">{plans.map((engine) => <EngineCard key={engine.id} engine={engine} />)}</div>
      </Section>
      <Section title="API keys">
        <div className="grid grid-cols-2 gap-3">{keys.map((engine) => <EngineCard key={engine.id} engine={engine} />)}</div>
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
          <label className="mt-4 flex cursor-pointer items-center justify-between gap-3">
            <span>
              <span className="block text-[12.5px] font-medium">Guided walkthroughs</span>
              <span className="block text-[11.5px] text-white/40">A checklist in the notch; Plip waits and re-checks each step</span>
            </span>
            <Toggle checked={state.walkthroughs} onChange={(enabled) => send('set-walkthroughs', { enabled })} />
          </label>
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
