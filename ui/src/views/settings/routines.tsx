import { Mic, Repeat2, Sparkles, X } from 'lucide-react'
import { useState } from 'react'
import { send, type SettingsState, type Suggestion } from '../../bridge'
import { Button, Card, Empty, Header, Input, Pill, Section } from './ui'

function SuggestionCard({ suggestion }: { suggestion: Suggestion }) {
  const [phrase, setPhrase] = useState(suggestion.phrase)
  return (
    <Card className="overflow-hidden glow-ring">
      <div className="pointer-events-none absolute -right-14 -top-20 size-52 rounded-full bg-plip-400/15 blur-3xl" />
      <div className="mb-3 flex items-start gap-3">
        <span className="grid size-9 place-items-center rounded-xl bg-plip-400/15 text-plip-200"><Sparkles className="size-4" /></span>
        <div className="flex-1">
          <div className="text-[13.5px] font-semibold">{suggestion.name}</div>
          <div className="text-[12.5px] text-white/50">
            You did these together on {suggestion.days} different days, usually around {suggestion.around}.
          </div>
        </div>
        <button aria-label="Not now" onClick={() => send('routine-dismiss', { key: suggestion.key })} className="grid size-7 place-items-center rounded-full text-white/35 hover:bg-white/10 hover:text-white">
          <X className="size-3.5" />
        </button>
      </div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {suggestion.labels.map((label, index) => (
          <span key={label} className="inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-2.5 py-1 text-[12px] text-white/80 hairline">
            <span className="font-mono text-[10px] text-plip-300">{index + 1}</span> {label}
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-[12px] text-white/45">When I say</span>
        <Input value={phrase} onChange={setPhrase} className="max-w-[220px]" />
        <Button variant="brand" disabled={!phrase.trim()} onClick={() => send('routine-accept', { key: suggestion.key, phrase })}>
          Save routine
        </Button>
      </div>
    </Card>
  )
}

export function RoutinesTab({ state }: { state: SettingsState }) {
  return (
    <div>
      <Header
        eyebrow="Routines"
        title="One phrase, many steps"
        subtitle="Teach Plip by voice, or let it notice what you already do. Saying the phrase runs it instantly, no thinking needed."
      />
      {state.suggestions.length > 0 && (
        <Section title="Plip noticed">
          <div className="space-y-3">{state.suggestions.map((suggestion) => <SuggestionCard key={suggestion.key} suggestion={suggestion} />)}</div>
        </Section>
      )}
      <Section title="Your routines">
        {state.routines.length === 0 ? (
          <Empty title="No routines yet" text="Hold ⌃⌥ and say something like: “When I say focus time, open Linear and turn on dark mode.”" />
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {state.routines.map((routine) => (
              <Card key={routine.id} className="group flex flex-col gap-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-[16px] font-semibold tracking-[-0.02em] text-white">“{routine.phrase}”</div>
                    <div className="text-[11.5px] text-white/40">{routine.name}</div>
                  </div>
                  <button aria-label="Delete routine" onClick={() => send('routine-delete', { id: routine.id })} className="grid size-7 place-items-center rounded-full text-white/0 transition group-hover:text-white/35 hover:!text-coral hover:bg-coral/10">
                    <X className="size-3.5" />
                  </button>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {routine.steps.map((step) => (
                    <span key={step} className="rounded-full bg-white/[0.05] px-2 py-0.5 text-[11.5px] text-white/70 hairline">{step}</span>
                  ))}
                </div>
                <div className="mt-auto flex items-center gap-2 pt-1">
                  <Pill tone={routine.source === 'suggested' ? 'info' : 'muted'} dot={false}>
                    {routine.source === 'suggested' ? <Sparkles className="size-3" /> : <Mic className="size-3" />}
                    {routine.source === 'suggested' ? 'Learned' : 'Taught'}
                  </Pill>
                  <span className="text-[11px] text-white/35"><Repeat2 className="mr-1 inline size-3" />ran {routine.runs}×</span>
                </div>
              </Card>
            ))}
          </div>
        )}
      </Section>
      <Section title="Teach Plip">
        <Card className="flex items-center gap-4">
          <span className="grid size-10 place-items-center rounded-xl bg-white/[0.06] text-white/70 hairline"><Mic className="size-4" /></span>
          <div className="text-[12.5px] leading-relaxed text-white/55">
            Say <span className="text-white/90">“Plip, when I say <b>wind down</b>, turn on dark mode, set the volume to 20 and open Spotify.”</span> Routines only
            run safe steps on their own: apps, links, Shortcuts, settings, timers and notes.
          </div>
        </Card>
      </Section>
    </div>
  )
}
