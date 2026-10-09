import { motion } from 'motion/react'
import { KeyRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { send } from '../../bridge'
import { cn } from '../../components/bits'

export function Header({ title, subtitle, eyebrow, action }: { title: string; subtitle?: string; eyebrow?: string; action?: React.ReactNode }) {
  return (
    <div className="mb-6 flex items-end justify-between gap-6">
      <div>
        {eyebrow && <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-plip-300">{eyebrow}</div>}
        <h1 className="text-[24px] font-semibold tracking-[-0.03em] text-white">{title}</h1>
        {subtitle && <p className="mt-1.5 max-w-[560px] text-[13.5px] leading-relaxed text-white/50">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

export function Section({ title, children, action, className }: { title: string; children: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <section className={cn('mb-7', className)}>
      <div className="mb-2.5 flex items-center justify-between">
        <h2 className="text-[11.5px] font-semibold uppercase tracking-[0.12em] text-white/35">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )
}

export function Card({ children, className, active, onClick }: { children: React.ReactNode; className?: string; active?: boolean; onClick?: () => void }) {
  return (
    <div onClick={onClick} className={cn('card relative p-4', onClick && 'card-hover cursor-pointer', active && 'glow-ring', className)}>
      {children}
    </div>
  )
}

export function Button({ children, onClick, variant = 'primary', className, disabled, size = 'md' }: {
  children: React.ReactNode
  onClick?: () => void
  variant?: 'primary' | 'brand' | 'ghost' | 'quiet' | 'danger'
  className?: string
  disabled?: boolean
  size?: 'sm' | 'md'
}) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full font-semibold tracking-tight transition active:scale-[0.97] disabled:pointer-events-none disabled:opacity-40',
        size === 'md' ? 'px-3.5 py-1.5 text-[12.5px]' : 'px-2.5 py-1 text-[11.5px]',
        variant === 'primary' && 'bg-white text-slate-950 shadow-[0_6px_20px_-8px_rgba(255,255,255,0.45)] hover:bg-plip-50',
        variant === 'brand' && 'brand-gradient text-slate-950 shadow-[inset_0_1px_0_rgba(255,255,255,0.45),0_8px_22px_-8px_rgba(95,142,244,0.75)] hover:brightness-110',
        variant === 'ghost' && 'bg-white/[0.07] text-white/85 hairline hover:bg-white/[0.11]',
        variant === 'quiet' && 'text-white/50 hover:text-white',
        variant === 'danger' && 'text-coral/80 hover:bg-coral/10 hover:text-coral',
        className,
      )}
    >
      {children}
    </button>
  )
}

/** A settings list: rows in one card, split by hairlines. */
/** A button for something that can't be undone: the first click asks, the second does it. */
export function ConfirmButton({ children, onConfirm, confirm = 'Clear', size = 'sm', variant = 'quiet' }: {
  children: React.ReactNode
  onConfirm: () => void
  confirm?: string
  size?: 'sm' | 'md'
  variant?: 'primary' | 'brand' | 'ghost' | 'quiet' | 'danger'
}) {
  const [asking, setAsking] = useState(false)
  if (!asking) return <Button size={size} variant={variant} onClick={() => setAsking(true)}>{children}</Button>
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-[12px] text-white/55">This can’t be undone.</span>
      <Button size={size} variant="danger" onClick={() => { setAsking(false); onConfirm() }}>{confirm}</Button>
      <Button size={size} variant="quiet" onClick={() => setAsking(false)}>Cancel</Button>
    </span>
  )
}

export function Rows({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('card relative divide-y divide-white/[0.05] overflow-hidden', className)}>{children}</div>
}

/** One setting: what it is, a line on what it does, and its control (or the whole row is the button). */
export function Row({ title, detail, action, onClick, open }: {
  title: React.ReactNode
  detail?: React.ReactNode
  action?: React.ReactNode
  onClick?: () => void
  open?: boolean
}) {
  const body = (
    <>
      <div className="min-w-0 flex-1">
        <div className="text-[13.5px] font-semibold tracking-tight text-white">{title}</div>
        {detail && <div className="mt-0.5 text-[12px] leading-relaxed text-white/40">{detail}</div>}
      </div>
      {action}
    </>
  )
  if (!onClick) return <div className="flex items-center gap-4 px-5 py-3.5">{body}</div>
  return (
    <button onClick={onClick} aria-expanded={open} className="group flex w-full items-center gap-4 px-5 py-3.5 text-left transition hover:bg-white/[0.03]">
      {body}
    </button>
  )
}

export type Tone = 'good' | 'warn' | 'bad' | 'muted' | 'info'

export function Pill({ tone, children, dot = true }: { tone: Tone; children: React.ReactNode; dot?: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold',
        tone === 'good' && 'bg-mint/12 text-emerald-300',
        tone === 'warn' && 'bg-sun/12 text-amber-200',
        tone === 'bad' && 'bg-coral/12 text-rose-300',
        tone === 'muted' && 'bg-white/[0.06] text-white/45',
        tone === 'info' && 'bg-plip-400/12 text-plip-200',
      )}
    >
      {dot && (
        <span className={cn('size-1.5 rounded-full', tone === 'good' ? 'bg-mint' : tone === 'warn' ? 'bg-sun' : tone === 'bad' ? 'bg-coral' : tone === 'info' ? 'bg-plip-300' : 'bg-white/30')} />
      )}
      {children}
    </span>
  )
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string; hint?: string }[]; onChange: (value: T) => void }) {
  const group = options.map((option) => option.value).join()
  return (
    <div className="inline-flex shrink-0 whitespace-nowrap rounded-full bg-black/40 p-1 hairline">
      {options.map((option) => (
        <button
          key={option.value}
          onClick={() => onChange(option.value)}
          title={option.hint}
          aria-label={option.hint}
          className={cn('relative rounded-full px-3 py-1 text-[12px] font-semibold transition', value === option.value ? 'text-slate-950' : 'text-white/50 hover:text-white/80')}
        >
          {value === option.value && (
            <motion.span layoutId={`seg-${group}`} className="absolute inset-0 rounded-full bg-white" transition={{ type: 'spring', stiffness: 500, damping: 36 }} />
          )}
          <span className="relative">{option.label}</span>
        </button>
      ))}
    </div>
  )
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label?: string }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation()
        onChange(!checked)
      }}
      className={cn('relative h-[22px] w-[38px] shrink-0 rounded-full transition', checked ? 'bg-plip-500 shadow-[0_0_14px_rgba(6,182,212,0.45)]' : 'bg-white/[0.12]')}
    >
      <motion.span
        layout
        transition={{ type: 'spring', stiffness: 600, damping: 32 }}
        className={cn('absolute top-[3px] size-4 rounded-full bg-white shadow', checked ? 'right-[3px]' : 'left-[3px]')}
      />
    </button>
  )
}

export function KeyField({ name, placeholder, saved }: { name: string; placeholder: string; saved: boolean }) {
  const [value, setValue] = useState('')
  const [editing, setEditing] = useState(!saved)
  useEffect(() => setEditing(!saved), [saved])
  if (!editing)
    return (
      <div className="flex items-center gap-2">
        <Pill tone="good">Key saved</Pill>
        <Button variant="quiet" size="sm" onClick={() => setEditing(true)}>Replace</Button>
      </div>
    )
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (!value.trim()) return
        send('set-key', { name, value: value.trim() })
        setValue('')
      }}
    >
      <div className="relative flex-1">
        <KeyRound className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-white/30" />
        <Input type="password" value={value} onChange={setValue} placeholder={placeholder} className="pl-8 font-mono" />
      </div>
      <Button disabled={!value.trim()}>Save</Button>
    </form>
  )
}

export function Input({ value, onChange, placeholder, className, type = 'text' }: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  type?: string
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      className={cn(
        'h-9 w-full rounded-full bg-black/40 px-3.5 text-[12.5px] text-white outline-none hairline placeholder:font-sans placeholder:text-white/25 focus:shadow-[inset_0_0_0_1px_rgba(143,179,250,0.6)]',
        className,
      )}
    />
  )
}

export function IconTile({ icon: Icon, gradient, size = 36 }: { icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>; gradient: string; size?: number }) {
  return (
    <span
      className={cn('grid shrink-0 place-items-center rounded-xl text-slate-950 shadow-[inset_0_1px_0_rgba(255,255,255,0.45),0_8px_18px_-10px_rgba(0,0,0,0.9)]', gradient)}
      style={{ width: size, height: size }}
    >
      <Icon className="size-[45%]" />
    </span>
  )
}

export function Stat({ icon: Icon, value, label, tone = 'text-plip-300' }: { icon: React.ComponentType<{ className?: string }>; value: string; label: string; tone?: string }) {
  return (
    <Card className="p-4">
      <Icon className={cn('mb-3 size-4', tone)} />
      <div className="text-[24px] font-semibold tracking-[-0.03em] text-white">{value}</div>
      <div className="text-[12px] text-white/40">{label}</div>
    </Card>
  )
}

export function Empty({ title, text, children }: { title: string; text: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-white/10 px-6 py-8 text-center">
      <div className="text-[14px] font-semibold text-white/85">{title}</div>
      <div className="mx-auto mt-1 max-w-[380px] text-[12.5px] leading-relaxed text-white/40">{text}</div>
      {children && <div className="mt-4 flex justify-center">{children}</div>}
    </div>
  )
}

export function timeAgo(seconds: number): string {
  const delta = Date.now() / 1000 - seconds
  if (delta < 90) return 'just now'
  if (delta < 3600) return `${Math.round(delta / 60)} min ago`
  if (delta < 86400) return `${Math.round(delta / 3600)} h ago`
  return `${Math.round(delta / 86400)} d ago`
}
