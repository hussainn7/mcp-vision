import { Check, Sparkles } from 'lucide-react'
import { send, type SettingsState } from '../../bridge'
import { Button } from './ui'

/** One button to get a working AI: picks a ready one, or signs in, or installs Claude and signs in. */
export function ConnectAI({ state, compact = false }: { state: SettingsState; compact?: boolean }) {
  const engine = state.engines.find((item) => item.selected)
  const ready = engine?.status === 'ready'
  return (
    <div className={compact ? '' : 'card p-5'}>
      <div className="flex items-center gap-4">
        <div className="flex-1">
          <div className="text-[14px] font-semibold">{ready ? `Connected to ${engine!.label}` : 'Connect your AI'}</div>
          <div className="mt-0.5 text-[12px] leading-relaxed text-white/45">
            {ready
              ? engine!.detail || engine!.via
              : 'One click. Plip uses the Claude, ChatGPT, Cursor or Gemini plan you already have, or sets up Claude for you.'}
          </div>
        </div>
        {ready ? (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-emerald-300">
            <Check className="size-4" strokeWidth={3} /> Ready
          </span>
        ) : (
          <Button variant="brand" onClick={() => send('quick-connect')}>
            <Sparkles className="size-3.5" /> Connect AI
          </Button>
        )}
      </div>
      {state.connect && !ready && (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-xl bg-white/[0.04] px-3 py-2 text-[12px] text-white/60 hairline">
          <span>{state.connect}</span>
          <Button variant="quiet" size="sm" onClick={() => send('refresh')}>Check again</Button>
        </div>
      )}
    </div>
  )
}
