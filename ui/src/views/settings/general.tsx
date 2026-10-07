import { AnimatePresence, motion } from 'motion/react'
import { ArrowUpRight, Check, ChevronDown, LoaderCircle, Play } from 'lucide-react'
import { useEffect, useState } from 'react'
import { send, settings, type SettingsState } from '../../bridge'
import { cn } from '../../components/bits'
import { Button, Header, Row, Rows, Section, Segmented, Toggle } from './ui'

export const REPO_URL = 'https://github.com/hussainn7/plip-oss'

export type Composer = 'bug' | 'feature' | null

const LIVES: Record<SettingsState['companion'], string> = {
  notch: 'Plip drips out of the notch only to point at something, then floats back.',
  cursor: 'A small droplet trails your cursor and flies off to point.',
  hidden: 'No floating droplet. Answers and checklists stay in the notch.',
}

/** Everything that isn't a feature: how Plip behaves, the tour, support, and about. */
export function GeneralTab({ state, composer: initial = null }: { state: SettingsState; composer?: Composer }) {
  const [composer, setComposer] = useState<Composer>(initial)
  const toggle = (next: Exclude<Composer, null>) => {
    if (state.report) send('report-reset')
    setComposer(composer === next ? null : next)
  }
  useEffect(() => {
    if (initial && state.report) send('report-reset')          // the menu bar's "Report a bug…" starts fresh
  }, [])
  return (
    <div>
      <Header eyebrow="General" title="How Plip behaves on your Mac" />

      <Section title="Behavior">
        <Rows>
          <Row
            title="Where Plip lives"
            detail={LIVES[state.companion]}
            action={<Segmented value={state.companion}
              options={[{ value: 'notch', label: 'Notch' }, { value: 'cursor', label: 'Cursor' }, { value: 'hidden', label: 'Hidden' }]}
              onChange={(style) => send('set-companion', { style })} />}
          />
          <Row
            title="Guided walkthroughs"
            detail="A checklist in the notch. Plip waits for you and re-checks each step."
            action={<Toggle label="Guided walkthroughs" checked={state.walkthroughs} onChange={(enabled) => send('set-walkthroughs', { enabled })} />}
          />
        </Rows>
      </Section>

      <Section title="Welcome tour">
        <Rows>
          <Row
            title="Replay the welcome tour"
            detail="Go through setup again: permissions and your brain. Everything you’ve set up stays."
            onClick={() => send('tour-start')}
            action={<Play className="size-4 shrink-0 fill-white/40 text-white/40 transition group-hover:fill-white/80 group-hover:text-white/80" />}
          />
        </Rows>
      </Section>

      <Section title="Support">
        <Rows>
          <Row title="Request a feature" detail="Tell us what Plip should do for you next." onClick={() => toggle('feature')} open={composer === 'feature'}
            action={<Chevron open={composer === 'feature'} />} />
          <Expand open={composer === 'feature'}><Feedback kind="feature" state={state} /></Expand>
          <Row title="Report a bug" detail="Tell us what went wrong. It sends only what you write." onClick={() => toggle('bug')}
            open={composer === 'bug'} action={<Chevron open={composer === 'bug'} />} />
          <Expand open={composer === 'bug'}><Feedback kind="bug" state={state} /></Expand>
        </Rows>
      </Section>

      <Section title="About">
        <Rows>
          <Row
            title={<>Plip <span className="font-mono text-[12px] font-normal text-white/40">v{state.version}</span></>}
            detail={state.update.available
              ? `Version ${state.update.available.version} is out. Download it, quit Plip, then drag the new one into Applications.`
              : 'Open source and private by default. Your memory, history and keys never leave this Mac.'}
            action={state.update.available
              ? <Button variant="brand" onClick={() => send('update-download')}>Download {state.update.available.version}</Button>
              : <Button variant="ghost" onClick={() => send('open-url', { url: REPO_URL })}>GitHub <ArrowUpRight className="size-3.5" /></Button>}
          />
          <Row
            title="Tell me about new versions"
            detail="Once a day Plip asks GitHub whether a newer version is out. Nothing about you is sent."
            action={<Toggle label="Tell me about new versions" checked={state.update.enabled} onChange={(enabled) => send('set-update-check', { enabled })} />}
          />
          <Row title="Quit Plip" detail="Plip stops listening until you open it again." action={<Button variant="ghost" onClick={() => send('quit')}>Quit</Button>} />
        </Rows>
      </Section>
    </div>
  )
}

function Chevron({ open }: { open: boolean }) {
  return <ChevronDown className={cn('size-4 shrink-0 text-white/35 transition group-hover:text-white/70', open && 'rotate-180')} />
}

function Expand({ open, children }: { open: boolean; children: React.ReactNode }) {
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }} className="!border-t-0 overflow-hidden">
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  )
}

/** A bug report or a feature request: only what they type, plus the app and macOS version. */
function Feedback({ kind, state }: { kind: 'bug' | 'feature'; state: SettingsState }) {
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const bug = kind === 'bug'
  const status = state.report

  useEffect(() => {
    if (status) setSending(false)
  }, [status])

  if (status === 'sent')
    return (
      <div className="flex items-center gap-3 px-5 pb-4">
        <span className="grid size-8 place-items-center rounded-full bg-emerald-400/15 text-emerald-300"><Check className="size-4" strokeWidth={3} /></span>
        <div className="flex-1 text-[13px] text-white/70">Sent. Thank you, that really helps.</div>
        <Button size="sm" variant="quiet" onClick={() => { setMessage(''); send('report-reset') }}>{bug ? 'Report another' : 'Send another'}</Button>
      </div>
    )
  return (
    <div className="px-5 pb-4">
      <textarea
        aria-label={bug ? 'What went wrong' : 'What should Plip do'}
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        placeholder={bug ? 'What did you ask, what did Plip do, and what did you expect?' : 'What do you wish Plip could do? An example helps.'}
        className="h-20 w-full resize-none rounded-xl bg-black/40 p-3 text-[12.5px] leading-relaxed text-white outline-none hairline placeholder:text-white/25 focus:shadow-[inset_0_0_0_1px_rgba(156,194,250,0.6)]"
      />
      <div className="mt-3 flex items-center gap-2">
        {status === 'failed'
          ? <span className="text-[12px] text-rose-300/90">Couldn’t send it. Check your connection and try again.</span>
          : <span className="text-[11.5px] text-white/30">Sends what you write{bug ? ', which AI you use' : ''}, and your app and macOS version. No screenshots or files.</span>}
        <Button variant="brand" className="ml-auto" disabled={!message.trim() || sending}
          onClick={() => {
            setSending(true)
            settings.set({ report: '' })
            send(bug ? 'report-issue' : 'request-feature', { message })
          }}>
          {sending ? <><LoaderCircle className="size-3.5 animate-spin" /> Sending</> : bug ? 'Send report' : 'Send'}
        </Button>
      </div>
    </div>
  )
}
