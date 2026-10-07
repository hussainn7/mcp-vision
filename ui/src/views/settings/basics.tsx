import { motion } from 'motion/react'
import { AudioLines, Contact, Download, Hand, LoaderCircle, MessageSquareText, Mic, MonitorUp, RotateCw, WandSparkles } from 'lucide-react'
import { send, type ParakeetModel, type SettingsState } from '../../bridge'
import { Button, Card, Empty, Header, KeyField, Pill, Section, Segmented } from './ui'

/** One permission's button: Allow, then "finish in System Settings" while the card is up there, then Granted. */
export function PermissionAction({ id, value, guiding }: { id: string; value: boolean | null | undefined; guiding?: string }) {
  if (value === true) return <Pill tone="good">Granted</Pill>
  if (guiding === id)
    return (
      <span className="flex items-center gap-2">
        <span className="flex items-center gap-1.5 text-[12px] text-white/55"><LoaderCircle className="size-3.5 animate-spin text-plip-300" /> Finish in System Settings</span>
        <Button size="sm" variant="quiet" onClick={() => send('grant', { permission: id })}>Show me again</Button>
      </span>
    )
  return <Button variant="brand" onClick={() => send('grant', { permission: id })}>Allow</Button>
}

/** Screen Recording only reaches a fresh Plip: one click to restart. */
export function RestartBanner({ state }: { state: SettingsState }) {
  if (!state.permissions.restart) return null
  return (
    <Card className="mb-5 flex items-center gap-4 border-plip-400/30 bg-plip-400/[0.07]">
      <span className="grid size-9 place-items-center rounded-xl bg-plip-400/15 text-plip-200"><RotateCw className="size-4" /></span>
      <div className="flex-1">
        <div className="text-[13.5px] font-semibold">One restart to finish</div>
        <div className="text-[12px] text-white/50">macOS turns on Screen Recording for Plip the next time it starts.</div>
      </div>
      <Button variant="primary" onClick={() => send('restart-app')}>Restart Plip</Button>
    </Card>
  )
}

export function VoiceTab({ state }: { state: SettingsState }) {
  return (
    <div>
      <Header eyebrow="Voice" title="How Plip sounds and listens" subtitle="Works out of the box with macOS voices and on-device recognition. Add keys for a more natural voice, or download Parakeet for sharper listening that stays on your Mac." />
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
              options={[{ value: 'apple', label: 'Apple' }, { value: 'parakeet', label: 'Parakeet' }, { value: 'assemblyai', label: 'AssemblyAI' }]}
              onChange={(stt) => send('set-voice', { stt })}
            />
          </div>
          {state.voice.stt === 'assemblyai' && <KeyField name="ASSEMBLYAI_API_KEY" placeholder="AssemblyAI API key" saved={state.voice.assemblyai} />}
          {state.voice.stt === 'apple' && <p className="text-[12px] leading-relaxed text-white/45">Built into macOS, on your Mac. Nothing to download.</p>}
          {state.voice.stt === 'parakeet' && state.voice.parakeet && <ParakeetPanel model={state.voice.parakeet} />}
        </Card>
      </div>
    </div>
  )
}

const megabytes = (bytes: number) => `${Math.round(bytes / 1_000_000)} MB`

/** Parakeet: what it is, the one-time download (with progress), and where it stands. */
function ParakeetPanel({ model }: { model: ParakeetModel }) {
  const share = model.total ? model.done / model.total : 0
  return (
    <div className="space-y-2.5">
      <p className="text-[12px] leading-relaxed text-white/45">
        NVIDIA&apos;s Parakeet Unified 0.6B, running on your Mac: catches more of what you say than Apple&apos;s, with punctuation, and
        nothing leaves your Mac.
      </p>
      {!model.runtime ? (
        <div className="rounded-xl bg-sun/[0.08] px-3 py-2 text-[12px] text-amber-100/90">Parakeet needs sherpa-onnx: reinstall Plip</div>
      ) : model.state === 'ready' ? (
        <div className="flex items-center justify-between">
          <Pill tone="good">Listening with Parakeet</Pill>
          <Button size="sm" variant="danger" onClick={() => send('parakeet-remove')}>Remove ({megabytes(model.total)})</Button>
        </div>
      ) : model.state === 'downloading' ? (
        <div className="space-y-1.5">
          <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.08]">
            <motion.div className="h-full rounded-full brand-gradient" animate={{ width: `${Math.max(2, share * 100)}%` }} transition={{ type: 'spring', stiffness: 120, damping: 20 }} />
          </div>
          <div className="flex items-center justify-between text-[11.5px] text-white/45">
            <span>{megabytes(model.done)} of {megabytes(model.total)} · Apple&apos;s listens until it&apos;s ready</span>
            <Button size="sm" variant="quiet" onClick={() => send('parakeet-cancel')}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {model.state === 'failed' && <div className="rounded-xl bg-coral/[0.08] px-3 py-2 text-[12px] leading-relaxed text-rose-200/90">{model.error}</div>}
          <div className="flex items-center justify-between gap-3">
            <span className="text-[11.5px] text-white/40">One-time download. Apple&apos;s listens until it&apos;s ready.</span>
            <Button size="sm" variant="brand" onClick={() => send('parakeet-download')}>
              <Download className="size-3.5" /> {model.state === 'failed' ? 'Try again' : `Download ${megabytes(model.total)}`}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

export function PermissionsTab({ state }: { state: SettingsState }) {
  const core = [
    { key: 'screen', icon: MonitorUp, title: 'Screen Recording', why: 'So Plip can see what you’re asking about.' },
    { key: 'accessibility', icon: Hand, title: 'Accessibility', why: 'The ⌃⌥ shortcut, precise pointing, typing and form filling.' },
    { key: 'microphone', icon: Mic, title: 'Microphone', why: 'Only while you hold the shortcut. Audio is never saved.' },
    { key: 'speech', icon: MessageSquareText, title: 'Speech Recognition', why: 'On-device transcription when AssemblyAI is off.' },
  ] as const
  const extra = [
    { key: 'contacts', icon: Contact, title: 'Contacts', why: 'Your card for Memory, and finding who to message.' },
    { key: 'automation', icon: WandSparkles, title: 'Automation', why: 'Lets Plip ask Messages, Reminders, Notes and Mail to do things.' },
  ] as const
  return (
    <div>
      <Header eyebrow="Privacy first" title="Permissions" subtitle="One click each: Plip opens the right page in System Settings and shows you exactly what to switch on. It only looks when you ask." action={<Button variant="quiet" onClick={() => send('refresh')}>Check again</Button>} />
      <RestartBanner state={state} />
      <Section title="Needed">
        <Card className="divide-y divide-white/[0.05] p-0">
          {core.map(({ key, icon: Icon, title, why }) => {
            const value = state.permissions[key]
            return (
              <div key={key} className="flex items-center gap-4 px-5 py-3.5">
                <span className="grid size-9 place-items-center rounded-xl bg-white/[0.06] hairline"><Icon className="size-4 text-white/75" /></span>
                <div className="flex-1">
                  <div className="text-[13.5px] font-semibold">{title}</div>
                  <div className="text-[12px] text-white/40">{why}</div>
                </div>
                <PermissionAction id={key} value={value} guiding={state.permissions.guiding} />
              </div>
            )
          })}
        </Card>
      </Section>
      <Section title="For skills">
        <Card className="divide-y divide-white/[0.05] p-0">
          {extra.map(({ key, icon: Icon, title, why }) => (
            <div key={key} className="flex items-center gap-4 px-5 py-3.5">
              <span className="grid size-9 place-items-center rounded-xl bg-white/[0.06] hairline"><Icon className="size-4 text-white/75" /></span>
              <div className="flex-1">
                <div className="text-[13.5px] font-semibold">{title}</div>
                <div className="text-[12px] text-white/40">{why}</div>
              </div>
              {state.permissions.guiding === key ? <PermissionAction id={key} value={null} guiding={key} /> : (
                <Button variant="ghost" onClick={() => send('grant', { permission: key })}>Set up</Button>
              )}
            </div>
          ))}
        </Card>
      </Section>
    </div>
  )
}

/** Activity → History: recent questions and answers. */
export function HistoryPanel({ state }: { state: SettingsState }) {
  return (
    <div>
      {!state.history.length ? (
        <Empty title="Nothing yet" text="Hold Control + Option and ask Plip something." />
      ) : (
        <div className="space-y-2">
          <div className="mb-3 flex items-center justify-between gap-6">
            <p className="text-[12.5px] text-white/45">Your recent questions. Kept on this Mac only.</p>
            <Button size="sm" variant="quiet" onClick={() => send('clear-history')}>Clear history</Button>
          </div>
          {state.history.slice().reverse().map((item) => (
            <Card key={item.at + item.question} className="p-4">
              <div className="mb-1 flex items-center justify-between gap-3">
                <div className="text-[13.5px] font-semibold tracking-tight">{item.question}</div>
                <div className="flex shrink-0 items-center gap-2">
                  {item.engine && <span className="rounded-full bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-white/40">{item.engine}</span>}
                  <span className="font-mono text-[10.5px] text-white/30">{new Date(item.at * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span>
                </div>
              </div>
              <div className="line-clamp-2 text-[12.5px] leading-relaxed text-white/50">{item.answer}</div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

