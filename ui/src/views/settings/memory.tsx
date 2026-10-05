import { AnimatePresence, motion } from 'motion/react'
import {
  AtSign, Cake, Check, ClipboardPaste, Contact, Copy, Globe, Lock, Mail, MapPin, Phone, Plus, Sparkles, X,
} from 'lucide-react'
import { useState } from 'react'
import { send, type FactCard, type SettingsState } from '../../bridge'
import { cn } from '../../components/bits'
import { Button, Card, Empty, Header, Input, Pill, Section, Segmented, timeAgo } from './ui'

const SOURCES = [
  { id: 'contacts', name: 'Contacts', text: 'Your “My Card”', icon: Contact, tint: 'from-slate-200 to-slate-400' },
  { id: 'autofill', name: 'Browser autofill', text: 'Chrome, Arc, Brave, Edge', icon: Globe, tint: 'from-sky-200 to-blue-400' },
  { id: 'mail', name: 'Mail', text: 'Your accounts & addresses', icon: Mail, tint: 'from-cyan-200 to-sky-400' },
] as const

const AI = [
  { id: 'chatgpt', name: 'ChatGPT' },
  { id: 'claude', name: 'Claude' },
  { id: 'gemini', name: 'Gemini' },
] as const

const KEY_OPTIONS = [
  ['note', 'Something to remember'], ['email', 'Email'], ['phone', 'Phone'], ['address.street', 'Street'],
  ['address.city', 'City'], ['address.postal', 'ZIP / postal code'], ['company', 'Company'], ['title', 'Job title'],
  ['birthday', 'Birthday'], ['website', 'Website'],
] as const

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || '?'
}

function KnowledgePanel({ state }: { state: SettingsState }) {
  const profile = state.memory?.profile ?? {}
  const name = profile['name.full']
  const location = [profile['address.city'], profile['address.state']].filter(Boolean).join(', ')
  const rows = [
    { icon: AtSign, label: 'Email', value: profile.email },
    { icon: Phone, label: 'Phone', value: profile.phone },
    { icon: MapPin, label: 'Address', value: [profile['address.street'], profile['address.city'], profile['address.postal']].filter(Boolean).join(', ') },
    { icon: Cake, label: 'Birthday', value: profile.birthday },
    { icon: Globe, label: 'Links', value: [profile.website, profile.linkedin && `linkedin/${profile.linkedin}`, profile.github && `github/${profile.github}`].filter(Boolean).join(' · ') },
  ].filter((row) => row.value)

  if (!name && !rows.length)
    return (
      <Empty title="Plip doesn’t know you yet" text="Import from your Mac below. It takes a few seconds, stays on this Mac, and lets Plip fill forms and personalize answers." />
    )

  return (
    <Card className="overflow-hidden p-0">
      <div className="relative flex items-center gap-4 px-5 pb-4 pt-5">
        <div className="pointer-events-none absolute -right-16 -top-24 size-56 rounded-full bg-plip-400/15 blur-3xl" />
        <span className="brand-gradient grid size-14 place-items-center rounded-2xl text-[20px] font-bold text-slate-950 shadow-[0_10px_30px_-10px_rgba(95,142,244,0.8)]">
          {initials(name || '?')}
        </span>
        <div className="min-w-0">
          <div className="truncate text-[19px] font-semibold tracking-[-0.02em]">{name || 'You'}</div>
          <div className="truncate text-[12.5px] text-white/50">
            {[profile.title, profile.company].filter(Boolean).join(' at ') || 'Add your role to help Plip'}
            {location && <span className="text-white/30"> · {location}</span>}
          </div>
        </div>
        <span className="ml-auto"><Pill tone="info" dot={false}><Lock className="size-3" /> On this Mac</Pill></span>
      </div>
      <div className="divide-y divide-white/[0.05] border-t border-white/[0.05]">
        {rows.map(({ icon: Icon, label, value }) => (
          <div key={label} className="flex items-center gap-3 px-5 py-2.5">
            <Icon className="size-3.5 text-white/35" />
            <span className="w-20 text-[12px] text-white/40">{label}</span>
            <span className="truncate text-[13px] text-white/85">{value}</span>
          </div>
        ))}
      </div>
    </Card>
  )
}

function SourceCard({ source, status }: { source: (typeof SOURCES)[number]; status?: { count: number; at: number; error?: string; added?: number } }) {
  const Icon = source.icon
  return (
    <Card className="flex flex-col gap-3 p-3.5">
      <div className="flex items-center gap-2.5">
        <span className={cn('grid size-8 place-items-center rounded-lg bg-gradient-to-br text-slate-950', source.tint)}><Icon className="size-4" /></span>
        <div className="min-w-0">
          <div className="text-[13px] font-semibold">{source.name}</div>
          <div className="truncate text-[11px] text-white/40">{source.text}</div>
        </div>
      </div>
      <div className="flex items-center justify-between gap-2">
        {status?.error ? (
          <span className="line-clamp-2 text-[11px] leading-snug text-rose-300/90">{status.error}</span>
        ) : status ? (
          <span className="text-[11px] text-white/45"><span className="font-semibold text-emerald-300">{status.count}</span> found · {timeAgo(status.at)}</span>
        ) : (
          <span className="text-[11px] text-white/30">Not imported</span>
        )}
        <Button size="sm" variant={status && !status.error ? 'ghost' : 'primary'} onClick={() => send('memory-import', { source: source.id })}>
          {status && !status.error ? 'Refresh' : 'Import'}
        </Button>
      </div>
    </Card>
  )
}

function AiMemory({ state }: { state: SettingsState }) {
  const [source, setSource] = useState<'chatgpt' | 'claude' | 'gemini'>('chatgpt')
  const [text, setText] = useState('')
  const [copied, setCopied] = useState(false)
  const imported = state.memory?.imports[source]
  return (
    <Card className="overflow-hidden">
      <div className="pointer-events-none absolute -left-16 -bottom-24 size-56 rounded-full bg-sky-glow/10 blur-3xl" />
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[13.5px] font-semibold"><Sparkles className="size-4 text-plip-300" /> Bring your AI memory</div>
        <Segmented value={source} options={AI.map((item) => ({ value: item.id, label: item.name }))} onChange={setSource} />
      </div>
      <ol className="mb-3 space-y-2 text-[12.5px] text-white/55">
        <li className="flex items-center gap-2.5">
          <span className="grid size-5 place-items-center rounded-full bg-white/[0.08] text-[10.5px] font-bold text-white/70">1</span>
          Copy the prompt and paste it into {AI.find((item) => item.id === source)?.name}.
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={() => {
              send('memory-copy-prompt')
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1600)
            }}
          >
            {copied ? <Check className="size-3" /> : <Copy className="size-3" />} {copied ? 'Copied' : 'Copy prompt'}
          </Button>
        </li>
        <li className="flex items-center gap-2.5">
          <span className="grid size-5 place-items-center rounded-full bg-white/[0.08] text-[10.5px] font-bold text-white/70">2</span>
          Paste its answer here. Plip sorts it into your profile and notes.
        </li>
      </ol>
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={'Name: …\nEmail: …\nPrefers aisle seats…'}
        className="h-24 w-full resize-none rounded-xl bg-black/40 p-3 font-mono text-[12px] leading-relaxed text-white outline-none hairline placeholder:text-white/20 focus:shadow-[inset_0_0_0_1px_rgba(143,179,250,0.6)]"
      />
      <div className="mt-2.5 flex items-center justify-between">
        <span className="text-[11px] text-white/35">{imported ? `${imported.count} facts from ${source} · ${timeAgo(imported.at)}` : 'Nothing imported yet'}</span>
        <Button
          variant="brand"
          disabled={!text.trim()}
          onClick={() => {
            send('memory-paste', { source, text })
            setText('')
          }}
        >
          <ClipboardPaste className="size-3.5" /> Import memory
        </Button>
      </div>
    </Card>
  )
}

function FactRow({ fact }: { fact: FactCard }) {
  return (
    <motion.div layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, height: 0 }} className="group flex items-center gap-3 px-4 py-2.5">
      <span className="w-28 shrink-0 truncate text-[11.5px] text-white/40">{fact.label}</span>
      <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-[13px] text-white/85">
        {fact.sensitive && <Lock className="size-3 shrink-0 text-amber-300" />}
        <span className="truncate">{fact.value}</span>
      </span>
      <span className="flex shrink-0 gap-1">
        {fact.sources.slice(0, 2).map((source) => (
          <span key={source} className="rounded-full bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-white/45">{source}</span>
        ))}
      </span>
      <button
        aria-label="Forget"
        onClick={() => send('memory-delete', { id: fact.id })}
        className="grid size-6 place-items-center rounded-full text-white/0 transition group-hover:text-white/40 hover:!text-coral hover:bg-coral/10"
      >
        <X className="size-3.5" />
      </button>
    </motion.div>
  )
}

function AddFact() {
  const [key, setKey] = useState<string>('note')
  const [value, setValue] = useState('')
  return (
    <form
      className="flex items-center gap-2 px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (!value.trim()) return
        send('memory-add', { key, value: value.trim() })
        setValue('')
      }}
    >
      <select
        value={key}
        onChange={(event) => setKey(event.target.value)}
        className="h-9 rounded-full bg-black/40 px-3 text-[12px] text-white/80 outline-none hairline"
      >
        {KEY_OPTIONS.map(([option, label]) => <option key={option} value={option}>{label}</option>)}
      </select>
      <Input value={value} onChange={setValue} placeholder="e.g. vegetarian, prefers aisle seats" />
      <Button disabled={!value.trim()}><Plus className="size-3.5" /> Add</Button>
    </form>
  )
}

export function MemoryTab({ state }: { state: SettingsState }) {
  const facts = state.memory?.facts ?? []
  return (
    <div>
      <Header
        eyebrow="Memory"
        title="What Plip knows about you"
        subtitle="Used to fill forms, message the right people, and answer like someone who knows you. Stored only on this Mac. Passport and card numbers stay private and are never sent to the model."
      />
      <Section title="You">
        <KnowledgePanel state={state} />
      </Section>
      <Section title="Import from your Mac">
        <div className="grid grid-cols-2 gap-2.5">
          {SOURCES.map((source) => <SourceCard key={source.id} source={source} status={state.memory?.imports[source.id]} />)}
        </div>
      </Section>
      <Section title="From other AI assistants">
        <AiMemory state={state} />
      </Section>
      <Section title={`Everything Plip knows (${facts.length})`}>
        <Card className="divide-y divide-white/[0.05] p-0">
          <AnimatePresence initial={false}>
            {facts.map((fact) => <FactRow key={fact.id} fact={fact} />)}
          </AnimatePresence>
          <AddFact />
        </Card>
      </Section>
    </div>
  )
}
