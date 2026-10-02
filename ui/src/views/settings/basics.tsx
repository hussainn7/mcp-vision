import { AudioLines, Contact, Hand, HardDrive, MessageSquareText, Mic, MonitorUp, ShieldCheck, WandSparkles } from 'lucide-react'
import { send, type SettingsState } from '../../bridge'
import { Mascot } from '../../components/Mascot'
import { Button, Card, Empty, Header, KeyField, Pill, Section, Segmented } from './ui'

export function VoiceTab({ state }: { state: SettingsState }) {
  return (
    <div>
      <Header eyebrow="Voice" title="How Plip sounds and listens" subtitle="Works out of the box with macOS voices and on-device recognition. Add keys for a more natural voice and faster streaming transcription." />
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
    { key: 'fulldisk', icon: HardDrive, title: 'Full Disk Access', why: 'Optional: reading Messages for Phone remote and iMessage import.' },
  ] as const
  return (
    <div>
      <Header eyebrow="Privacy first" title="Permissions" subtitle="macOS asks once for each. Plip only looks when you ask, and never sends anything you didn’t trigger." action={<Button variant="quiet" onClick={() => send('refresh')}>Check again</Button>} />
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
                {value === true ? <Pill tone="good">Granted</Pill> : (
                  <Button variant={value === false ? 'primary' : 'ghost'} onClick={() => send('grant', { permission: key })}>
                    {value === false ? 'Open Settings' : 'Allow'}
                  </Button>
                )}
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
              <Button variant="ghost" onClick={() => send('grant', { permission: key })}>Open Settings</Button>
            </div>
          ))}
        </Card>
      </Section>
    </div>
  )
}

export function HistoryTab({ state }: { state: SettingsState }) {
  return (
    <div>
      <Header eyebrow="History" title="Recent questions" subtitle="Kept on this Mac only. Clear it anytime." action={state.history.length ? <Button variant="quiet" onClick={() => send('clear-history')}>Clear history</Button> : undefined} />
      {!state.history.length ? (
        <Empty title="Nothing yet" text="Hold Control + Option and ask Plip something." />
      ) : (
        <div className="space-y-2">
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

export function AboutTab({ state }: { state: SettingsState }) {
  return (
    <div className="grid min-h-[520px] place-items-center text-center">
      <div>
        <div className="relative mx-auto mb-5 w-fit">
          <div className="absolute inset-0 -z-10 scale-150 rounded-full bg-plip-400/25 blur-3xl" />
          <Mascot size={96} mood="happy" />
        </div>
        <h1 className="text-gradient text-[30px] font-semibold tracking-[-0.03em]">Plip</h1>
        <div className="mt-1 font-mono text-[12px] text-white/35">version {state.version}</div>
        <p className="mx-auto mt-4 max-w-[400px] text-[13.5px] leading-relaxed text-white/50">
          A little droplet of personal staff for your Mac. It sees what you see, explains it step by step, and does the busywork:
          files, forms, messages, flights, routines. Open source, and private by default.
        </p>
        <div className="mx-auto mt-4 flex w-fit items-center gap-2 text-[11.5px] text-white/35">
          <ShieldCheck className="size-3.5 text-mint" /> Your memory, history and keys never leave this Mac.
        </div>
        <div className="mt-6 flex justify-center gap-2">
          <Button variant="ghost" onClick={() => send('open-url', { url: 'https://github.com/hussainn7/mcp-vision' })}>GitHub</Button>
          <Button variant="ghost" onClick={() => send('quit')}>Quit Plip</Button>
        </div>
      </div>
    </div>
  )
}
