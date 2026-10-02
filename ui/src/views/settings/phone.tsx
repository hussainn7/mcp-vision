import { HardDrive, Lock, Plus, ShieldCheck, Smartphone, X } from 'lucide-react'
import { motion } from 'motion/react'
import { useState } from 'react'
import { send, type SettingsState } from '../../bridge'
import { cn } from '../../components/bits'
import type { Tab } from './index'
import { Button, Card, Header, Input, Pill, Section, Toggle } from './ui'

const CHAT = [
  { me: true, text: '/plip find my lease pdf' },
  { me: false, text: 'Plip: Found “Lease-2026.pdf” in Documents › Apartment. Want me to open it?' },
  { me: true, text: '/plip start my day' },
  { me: false, text: 'Plip: Done. Opened Slack, Calendar and Spotify.' },
  { me: true, text: '/plip text Sara running 10 min late' },
  { me: false, text: 'Plip: I’ll send Sara “Running 10 min late”. Reply /plip yes to send.' },
]

function PhoneMock({ prefix }: { prefix: string }) {
  return (
    <div className="relative mx-auto w-[248px] rounded-[38px] bg-gradient-to-b from-white/[0.14] to-white/[0.04] p-[7px] shadow-[0_30px_80px_-30px_rgba(34,211,238,0.35)]">
      <div className="overflow-hidden rounded-[32px] bg-[#0b0b0d]">
        <div className="flex flex-col items-center gap-1 border-b border-white/[0.06] pb-2.5 pt-3">
          <div className="mb-1 h-5 w-20 rounded-full bg-black" />
          <span className="grid size-8 place-items-center rounded-full brand-gradient text-[12px] font-bold text-slate-950">Me</span>
          <span className="text-[10px] text-white/45">You (note to self)</span>
        </div>
        <div className="space-y-1.5 px-2.5 py-3">
          {CHAT.map((line, index) => (
            <motion.div
              key={index}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15 + index * 0.12 }}
              className={cn('flex', line.me ? 'justify-end' : 'justify-start')}
            >
              <span
                className={cn(
                  'max-w-[82%] rounded-[16px] px-2.5 py-1.5 text-[10.5px] leading-snug',
                  line.me ? 'rounded-br-[5px] bg-[#0a84ff] text-white' : 'rounded-bl-[5px] bg-[#26262a] text-white/90',
                )}
              >
                {line.me ? line.text.replace('/plip', prefix) : line.text}
              </span>
            </motion.div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function PhoneTab({ state, go }: { state: SettingsState; go: (tab: Tab) => void }) {
  const phone = state.phone
  const [handle, setHandle] = useState('')
  const handles = phone.handles.length ? phone.handles : phone.detected
  const status = !phone.enabled ? ['Off', 'muted'] : phone.status === 'error' ? ['Needs attention', 'bad'] : phone.status === 'listening' ? ['Listening for texts', 'good'] : ['Starting…', 'muted']

  return (
    <div>
      <Header
        eyebrow="Phone"
        title="Text Plip from anywhere"
        subtitle="Send yourself an iMessage that starts with the magic word. Your Mac runs it and texts back. Handy from the couch, the train, or another room."
      />
      <div className="mb-7 grid grid-cols-[1fr_auto] items-center gap-8">
        <div className="space-y-3">
          <Card className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span className="grid size-10 place-items-center rounded-xl bg-plip-400/15 text-plip-200"><Smartphone className="size-5" /></span>
              <div>
                <div className="text-[13.5px] font-semibold">Remote control by iMessage</div>
                <div className="mt-0.5"><Pill tone={status[1] as 'good' | 'bad' | 'muted'}>{status[0]}</Pill></div>
              </div>
            </div>
            <Toggle label="Remote control" checked={phone.enabled} onChange={(enabled) => send('set-phone', { enabled })} />
          </Card>
          {phone.enabled && phone.status === 'error' && (
            <Card className="flex items-center gap-3 border border-coral/20">
              <HardDrive className="size-4 shrink-0 text-coral" />
              <div className="flex-1 text-[12.5px] text-rose-100/80">{phone.error || 'Plip can’t read Messages yet.'}</div>
              <Button size="sm" onClick={() => send('grant', { permission: 'fulldisk' })}>Open Settings</Button>
            </Card>
          )}
          <Card>
            <div className="mb-1 text-[13px] font-semibold">Your numbers</div>
            <div className="mb-3 text-[12px] text-white/40">Only texts in your own chat with these count. Nobody else can drive your Mac.</div>
            <div className="mb-3 flex flex-wrap gap-1.5">
              {handles.length === 0 && <span className="text-[12px] text-white/35">None yet. Import iMessage in Memory, or add one.</span>}
              {handles.map((item) => (
                <span key={item} className="inline-flex items-center gap-1 rounded-full bg-white/[0.06] py-0.5 pl-2.5 pr-1 font-mono text-[11.5px] text-white/80 hairline">
                  {item}
                  <button aria-label="Remove" onClick={() => send('set-phone', { handles: handles.filter((other) => other !== item) })} className="grid size-5 place-items-center rounded-full text-white/40 hover:bg-white/10 hover:text-white">
                    <X className="size-3" />
                  </button>
                </span>
              ))}
            </div>
            <form
              className="flex gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                if (!handle.trim()) return
                send('set-phone', { handles: [...handles, handle.trim()] })
                setHandle('')
              }}
            >
              <Input value={handle} onChange={setHandle} placeholder="+1 555 010 2000 or you@icloud.com" />
              <Button variant="ghost" disabled={!handle.trim()}><Plus className="size-3.5" /> Add</Button>
              {!phone.detected.length && <Button variant="quiet" onClick={() => go('memory')}>Import</Button>}
            </form>
          </Card>
          <div className="space-y-1.5 px-1 text-[12px] leading-relaxed text-white/40">
            <p className="flex items-start gap-2">
              <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-mint" />
              <span>Sending messages, moving files and filling forms still ask first. Reply <span className="font-mono text-white/70">{phone.prefix} yes</span> to go ahead.</span>
            </p>
            <p className="flex items-start gap-2">
              <Lock className="mt-0.5 size-3.5 shrink-0 text-white/30" />
              <span>Reading Messages needs Full Disk Access for Plip.</span>
            </p>
          </div>
        </div>
        <PhoneMock prefix={phone.prefix} />
      </div>
      {phone.lastCommand && (
        <Section title="Last text">
          <Card className="font-mono text-[12.5px] text-white/70">{phone.prefix} {phone.lastCommand}</Card>
        </Section>
      )}
    </div>
  )
}
