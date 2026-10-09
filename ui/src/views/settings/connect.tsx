import { Check, ExternalLink, LoaderCircle, Plug, RotateCcw } from 'lucide-react'
import { useState } from 'react'
import { send, type ConnectProgress, type Engine, type SettingsState } from '../../bridge'
import { cn } from '../../components/bits'
import { Button, Card, Input, Pill } from './ui'

export const ENGINE_GLYPH: Record<string, { bg: string; text: string; glyph: string }> = {
  'claude-code': { bg: 'bg-[#d97757]', text: 'text-white', glyph: '✳' },
  codex: { bg: 'bg-white', text: 'text-black', glyph: '◎' },
  cursor: { bg: 'bg-[#111114] hairline', text: 'text-white', glyph: '▲' },
  gemini: { bg: 'bg-gradient-to-br from-[#4f7cff] to-[#b46bff]', text: 'text-white', glyph: '✦' },
  'gemini-api': { bg: 'bg-gradient-to-br from-[#4f7cff] to-[#b46bff]', text: 'text-white', glyph: '✦' },
  anthropic: { bg: 'bg-[#e8dccf]', text: 'text-[#1f1b16]', glyph: 'A' },
}

export const GOOGLE_KEY_PAGE = 'https://aistudio.google.com/apikey'

/** The plans people pay for, in words they'd use. */
const PLANS: { id: string; plan: string }[] = [
  { id: 'claude-code', plan: 'Claude Pro or Max' },
  { id: 'codex', plan: 'ChatGPT Plus or Pro' },
  { id: 'cursor', plan: 'Cursor' },
]

/** Pick an AI the plain way: the plan you pay for (one click installs and signs in), or a free one from Google. */
export function ConnectAI({ state, compact = false }: { state: SettingsState; compact?: boolean }) {
  const engine = state.engines.find((item) => item.selected)
  const ready = engine?.status === 'ready'
  const [choosing, setChoosing] = useState(false)

  if (ready && !choosing) {
    return (
      <Card className="flex items-center gap-4">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-mint/10 text-emerald-300 hairline"><Check className="size-4" strokeWidth={3} /></span>
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold tracking-tight">Plip thinks with {engine!.label}</div>
          <div className="truncate text-[12px] text-white/45">{engine!.detail || engine!.via}</div>
        </div>
        <Button variant="quiet" onClick={() => setChoosing(true)}>Change</Button>
      </Card>
    )
  }

  const plans = PLANS.map(({ id, plan }) => ({ engine: state.engines.find((item) => item.id === id), plan }))
    .filter((item): item is { engine: Engine; plan: string } => Boolean(item.engine))
  return (
    <div className="space-y-3">
      {!compact && (
        <div>
          <div className="text-[14px] font-semibold tracking-tight">Connect an AI</div>
          <div className="mt-0.5 text-[12.5px] text-white/45">Plip needs an AI to think with. Use one you already pay for, or a free one from Google.</div>
        </div>
      )}
      <Card className="divide-y divide-white/[0.05] p-0">
        <div className="px-5 pb-2 pt-3.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-white/35">If you pay for one of these</div>
        {plans.map(({ engine: item, plan }) => <PlanRow key={item.id} engine={item} plan={plan} />)}
      </Card>
      <FreeGoogleKey state={state} />
      {ready && <Button variant="quiet" onClick={() => setChoosing(false)}>Keep {engine!.label}</Button>}
    </div>
  )
}

function PlanRow({ engine, plan }: { engine: Engine; plan: string }) {
  const glyph = ENGINE_GLYPH[engine.id] ?? { bg: 'bg-white/10', text: 'text-white', glyph: '•' }
  const connect = engine.connect
  const busy = connect?.state === 'installing' || connect?.state === 'signing-in'
  const usable = !busy && (engine.status === 'ready' || engine.status === 'unknown')
  const canConnect = !busy && (engine.status === 'not-installed' || engine.status === 'logged-out')
  return (
    <div className="px-5 py-3">
      <div className="flex items-center gap-4">
        <span className={cn('grid size-9 shrink-0 place-items-center rounded-xl text-[16px] font-bold shadow-lg', glyph.bg, glyph.text)}>{glyph.glyph}</span>
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-semibold tracking-tight">{engine.label}</div>
          <div className="truncate text-[12px] text-white/45">{plan}</div>
        </div>
        {engine.selected && usable ? (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-emerald-300"><Check className="size-4" strokeWidth={3} /> In use</span>
        ) : usable ? (
          <Button variant="ghost" onClick={() => send('select-engine', { id: engine.id })}>Use {engine.label}</Button>
        ) : canConnect ? (
          <Button variant="brand" onClick={() => send('engine-connect', { id: engine.id })}>
            {connect?.state === 'failed' ? <RotateCcw className="size-3.5" /> : <Plug className="size-3.5" />}
            {connect?.state === 'failed' ? 'Try again' : 'Connect'}
          </Button>
        ) : null}
      </div>
      {connect && (busy || connect.state === 'failed') && <div className="mt-3"><Connecting id={engine.id} progress={connect} /></div>}
    </div>
  )
}

/** No AI plan: a free Gemini key from Google, in three steps a first-timer can follow. */
export function FreeGoogleKey({ state }: { state: SettingsState }) {
  const gemini = state.engines.find((item) => item.id === 'gemini-api')
  const check = state.keyCheck && (state.keyCheck.name === 'GEMINI_API_KEY' || !state.keyCheck.name) ? state.keyCheck : null
  const [typing, setTyping] = useState(false)
  const [value, setValue] = useState('')
  if (!gemini) return null
  const ready = gemini.status === 'ready'

  return (
    <Card>
      <div className="flex items-start gap-4">
        <span className={cn('grid size-9 shrink-0 place-items-center rounded-xl text-[16px] font-bold shadow-lg', ENGINE_GLYPH.gemini.bg, ENGINE_GLYPH.gemini.text)}>{ENGINE_GLYPH.gemini.glyph}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13.5px] font-semibold tracking-tight">No AI plan? Use Google’s for free</span>
            <Pill tone="good" dot={false}>Free</Pill>
          </div>
          <div className="mt-0.5 text-[12px] leading-relaxed text-white/45">
            Gemini, from Google. All you need is a Google account, like the one you use for Gmail. Takes about a minute.
          </div>
        </div>
        {ready && gemini.selected && (
          <span className="inline-flex shrink-0 items-center gap-1.5 text-[12.5px] font-semibold text-emerald-300"><Check className="size-4" strokeWidth={3} /> In use</span>
        )}
        {ready && !gemini.selected && <Button variant="ghost" onClick={() => send('select-engine', { id: 'gemini-api' })}>Use Gemini</Button>}
      </div>
      {!ready && (
        <>
          <ol className="mt-4 space-y-2 text-[12.5px] text-white/65">
            <li className="flex gap-2.5"><Step n={1} /><span>Click <b className="font-semibold text-white/85">Get my free key</b>. Google’s page opens; sign in with your Google account.</span></li>
            <li className="flex gap-2.5"><Step n={2} /><span>Click <b className="font-semibold text-white/85">Create API key</b>, then copy the key it shows you.</span></li>
            <li className="flex gap-2.5"><Step n={3} /><span>Come back here and click <b className="font-semibold text-white/85">Paste key</b>.</span></li>
          </ol>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button variant="brand" onClick={() => send('open-url', { url: GOOGLE_KEY_PAGE })}>Get my free key <ExternalLink className="size-3.5" /></Button>
            <Button onClick={() => send('paste-key')}>Paste key</Button>
            {!typing && (
              <button className="ml-1 text-[12px] text-white/40 underline-offset-2 hover:text-white/70 hover:underline" onClick={() => setTyping(true)}>
                or type it in
              </button>
            )}
          </div>
          {typing && (
            <form
              className="mt-3 flex items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                if (!value.trim()) return
                send('set-key', { name: 'GEMINI_API_KEY', value: value.trim() })
                setValue('')
              }}
            >
              <Input type="password" value={value} onChange={setValue} placeholder="Your Google key (starts with AIza)" className="font-mono" />
              <Button disabled={!value.trim()}>Save</Button>
            </form>
          )}
        </>
      )}
      {check && (
        <div className={cn('mt-3 flex items-center gap-2 text-[12px]', check.state === 'bad' ? 'text-rose-200/90' : check.state === 'ok' ? 'text-emerald-300' : 'text-white/60')}>
          {check.state === 'checking' && <LoaderCircle className="size-3.5 animate-spin text-plip-300" />}
          {check.state === 'ok' && <Check className="size-3.5" strokeWidth={3} />}
          <span>{check.state === 'ok' ? 'Google took the key. You’re connected.' : check.message}</span>
        </div>
      )}
      {ready && (
        <div className="mt-3 text-[11.5px] text-white/35">The free version has a daily limit, plenty for everyday questions. It resets every day.</div>
      )}
    </Card>
  )
}

function Step({ n }: { n: number }) {
  return <span className="grid size-5 shrink-0 place-items-center rounded-full bg-white/[0.07] text-[10.5px] font-bold text-white/60">{n}</span>
}

/** Live progress while Plip installs a brain's app and waits for the browser sign-in. */
export function Connecting({ id, progress }: { id: string; progress: ConnectProgress }) {
  const [help, setHelp] = useState(false)
  const [code, setCode] = useState('')
  if (progress.state === 'failed')
    return <div className="rounded-xl bg-coral/[0.08] px-3 py-2 text-[12px] leading-relaxed text-rose-200/90">{progress.message}</div>
  return (
    <div className="rounded-xl bg-white/[0.04] px-3 py-2.5 hairline">
      <div className="flex items-center gap-2 text-[12.5px] text-white/80">
        <LoaderCircle className="size-3.5 shrink-0 animate-spin text-plip-300" />
        <span className="flex-1">{progress.message}</span>
        <Button variant="quiet" size="sm" onClick={() => send('engine-connect-cancel', { id })}>Cancel</Button>
      </div>
      {progress.state === 'signing-in' && progress.url && (
        <div className="mt-1.5 pl-5.5 text-[11.5px] text-white/40">
          {!help ? (
            <button className="underline decoration-white/20 underline-offset-2 hover:text-white/70" onClick={() => setHelp(true)}>
              Browser didn’t open?
            </button>
          ) : (
            <div className="space-y-2">
              <button className="font-semibold text-plip-200 hover:text-plip-100" onClick={() => send('open-url', { url: progress.url })}>
                Open the sign-in page
              </button>
              {progress.needsCode && (
                <form
                  className="flex items-center gap-2"
                  onSubmit={(event) => {
                    event.preventDefault()
                    if (code.trim()) send('engine-connect-code', { id, code: code.trim() })
                    setCode('')
                  }}
                >
                  <Input value={code} onChange={setCode} placeholder="If the page shows a code, paste it here" className="font-mono text-[11.5px]" />
                  <Button size="sm" disabled={!code.trim()}>Done</Button>
                </form>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
