import { motion } from 'motion/react'
import { CircleCheck, CircleHelp, CircleX, Hand, Square } from 'lucide-react'
import { useState } from 'react'
import { send, type Outcome, type SettingsState, type UsagePeriod } from '../../bridge'
import { cn } from '../../components/bits'
import { Button, Card, Header, Section, Segmented } from './ui'

type Period = '7' | '30' | 'all'

// How a request ended, in five groups. Every slice also carries an icon and a label, so color is never the only cue.
const ENDINGS: { id: string; label: string; color: string; icon: React.ComponentType<{ className?: string }>; from: Outcome[] }[] = [
  { id: 'finished', label: 'Finished', color: '#3987e5', icon: CircleCheck, from: ['done', 'answered'] },
  { id: 'needed', label: 'Needed you', color: '#c98500', icon: Hand, from: ['waiting', 'paused'] },
  { id: 'unsure', label: "Couldn't confirm", color: '#9085e9', icon: CircleHelp, from: ['unverified'] },
  { id: 'failed', label: "Didn't work", color: '#e66767', icon: CircleX, from: ['failed'] },
  { id: 'stopped', label: 'You stopped it', color: '#6b6f7a', icon: Square, from: ['stopped'] },
]
const ACCENT = '#6f9ef5'
const QUIET = 'rgba(255,255,255,0.14)'

export function compact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`
  if (value >= 10_000) return `${Math.round(value / 1000)}K`
  if (value >= 1000) return `${(value / 1000).toFixed(1)}K`
  return `${Math.round(value)}`
}

export function money(value: number): string {
  if (value <= 0) return '$0'
  if (value < 0.01) return '<$0.01'
  return value >= 100 ? `$${Math.round(value).toLocaleString()}` : `$${value.toFixed(2)}`
}

function dayLabel(day: string): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function hourLabel(hour: number): string {
  return hour === 0 ? '12am' : hour < 12 ? `${hour}am` : hour === 12 ? '12pm' : `${hour - 12}pm`
}

export function UsageTab({ state }: { state: SettingsState }) {
  const [period, setPeriod] = useState<Period>('30')
  const usage = state.usage
  const data = usage?.periods[period]
  return (
    <div>
      <Header
        eyebrow="More"
        title="Usage"
        subtitle="Everything Plip sent to your brain from this Mac, and what it would cost at pay-per-use API prices. It stays on this Mac."
        action={<Segmented value={period} onChange={setPeriod}
          options={[{ value: '7', label: '7 days' }, { value: '30', label: '30 days' }, { value: 'all', label: 'All time' }]} />}
      />
      {!usage || !data || usage.periods.all.requests === 0 ? <Empty /> : <Body data={data} state={state} />}
    </div>
  )
}

function Empty() {
  return (
    <Card className="py-10 text-center">
      <div className="text-[14px] font-semibold text-white/80">Nothing yet</div>
      <p className="mx-auto mt-1.5 max-w-[380px] text-[12.5px] leading-relaxed text-white/45">
        Hold ⌃⌥ and ask Plip something. Every request shows up here with the tokens it used and what that would cost.
      </p>
    </Card>
  )
}

function Body({ data, state }: { data: UsagePeriod; state: SettingsState }) {
  const usage = state.usage!
  const ever = usage.periods.all
  const finished = data.outcomes.done + data.outcomes.answered
  const onPlan = usage.billed < ever.cost
  return (
    <>
      <div className="mb-7 grid grid-cols-4 gap-3">
        <Stat label="Requests" value={compact(data.requests)} hint={data.tasks ? `${data.tasks} were multi-step tasks` : undefined} />
        <Stat label="Model calls" value={compact(data.turns)} hint={data.requests ? `${(data.turns / data.requests).toFixed(1)} per request` : undefined} />
        <Stat label="Things done" value={compact(data.actions)} hint="opened, searched, noted…" />
        <Stat label="Finished" value={data.requests ? `${Math.round((finished / data.requests) * 100)}%` : '–'} hint="of requests" />
      </div>

      <Section title="Requests per day">
        <Card>
          <Columns
            values={data.perDay.map((day) => day.requests)}
            tip={(index) => {
              const day = data.perDay[index]
              return `${dayLabel(day.day)} · ${day.requests} request${day.requests === 1 ? '' : 's'}${day.cost ? ` · ${money(day.cost)}` : ''}`
            }}
            ticks={[dayLabel(data.perDay[0]?.day ?? ''), dayLabel(data.perDay[data.perDay.length - 1]?.day ?? '')]}
            label="Requests per day"
          />
        </Card>
      </Section>

      <div className="grid grid-cols-[1.35fr_1fr] gap-4">
        <Section title="When you ask">
          <Card className="h-[188px]">
            <Columns
              values={data.perHour}
              highlight={data.busiestHour}
              height={112}
              tip={(hour) => `${hourLabel(hour)} · ${data.perHour[hour]} request${data.perHour[hour] === 1 ? '' : 's'}`}
              ticks={['12am', '12pm', '11pm']}
              label="Requests by hour of day"
            />
            {data.busiestHour !== null && (
              <div className="mt-2 text-[11.5px] text-white/40">Busiest around {hourLabel(data.busiestHour)}.</div>
            )}
          </Card>
        </Section>
        <Section title="How it ended">
          <Card className="h-[188px]">
            <Endings data={data} />
          </Card>
        </Section>
      </div>

      <Section title="What your plan is worth">
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Tokens in" value={compact(data.tokensIn)} hint={data.cacheRead ? `${compact(data.cacheRead)} read from cache` : undefined} />
          <Stat label="Tokens out" value={compact(data.tokensOut)} />
          <Stat label={onPlan ? 'What your plan is worth' : 'Spent on your API key'} value={money(data.cost)} accent
            hint={onPlan ? 'at API prices, not billed: your plan covers it' : 'billed per token'} />
        </div>
        <p className="mt-2.5 text-[11.5px] leading-relaxed text-white/35">
          Claude Code, Codex and the API report exact counts; the rest are estimated
          {data.estimatedShare > 0 ? ` (${Math.round(data.estimatedShare * 100)}% of these requests)` : ''}. Since you
          started: {compact(ever.tokensIn)} in, {compact(ever.tokensOut)} out, {money(ever.cost)}
          {usage.billed > 0 ? `, of which ${money(usage.billed)} went to your API key` : ' worth of API calls, none of it billed'}.
        </p>
      </Section>

      <div className="grid grid-cols-2 gap-4">
        <Section title="Brains">
          <Card>
            <Bars rows={data.engines.map((engine) => ({
              label: engine.label, detail: engine.model ? engine.model.replace(/^claude-/, '') : engine.lane,
              value: engine.requests, suffix: engine.cost ? money(engine.cost) : undefined,
            }))} unit="requests" />
          </Card>
        </Section>
        <Section title="Most used actions">
          <Card>
            {data.topActions.length ? (
              <Bars rows={data.topActions.map((action) => ({ label: action.name.replace(/_/g, ' '), value: action.count }))} unit="times" />
            ) : (
              <div className="py-6 text-center text-[12px] text-white/35">No actions in this period.</div>
            )}
          </Card>
        </Section>
      </div>

      {data.lanes.length > 0 && (
        <Section title="Which plan paid">
          <Card>
            <Bars rows={data.lanes.map((lane) => ({ label: lane.lane, value: lane.requests }))} unit="requests" />
          </Card>
        </Section>
      )}

      <div className="flex justify-end">
        <Button variant="danger" size="sm" onClick={() => send('clear-usage')}>Clear usage</Button>
      </div>
    </>
  )
}

function Stat({ label, value, hint, accent }: { label: string; value: string; hint?: string; accent?: boolean }) {
  return (
    <Card className="py-3.5">
      <div className={cn('text-[26px] font-semibold leading-none tracking-[-0.03em]', accent ? 'text-gradient' : 'text-white')}>{value}</div>
      <div className="mt-2 text-[12px] font-medium text-white/55">{label}</div>
      {hint && <div className="mt-0.5 truncate text-[11px] text-white/30">{hint}</div>}
    </Card>
  )
}

/** One series of columns from a single baseline; hover any column for its value. */
function Columns({ values, tip, ticks, highlight = null, height = 120, label }: {
  values: number[]
  tip: (index: number) => string
  ticks: string[]
  highlight?: number | null
  height?: number
  label: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...values)
  const gap = values.length > 40 ? 1 : 2
  return (
    <div className="relative" role="img" aria-label={label}>
      <div className="relative flex items-end" style={{ height, gap }} onMouseLeave={() => setHover(null)}>
        <div className="absolute inset-x-0 bottom-0 h-px bg-white/[0.08]" />
        {values.map((value, index) => {
          const color = highlight === null || index === highlight ? ACCENT : QUIET
          const h = value ? Math.max(3, (value / max) * (height - 6)) : 0
          return (
            <div key={index} className="relative flex h-full flex-1 items-end justify-center" onMouseEnter={() => setHover(index)}>
              <motion.div
                initial={{ height: 0 }}
                animate={{ height: h }}
                transition={{ duration: 0.5, delay: index * 0.008, ease: [0.32, 0.72, 0, 1] }}
                className="w-full max-w-[24px] rounded-t-[4px]"
                style={{ background: color, opacity: hover === null || hover === index ? 1 : 0.55 }}
              />
            </div>
          )
        })}
        {hover !== null && (
          <div
            className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg bg-[#1b1f29] px-2 py-1 text-[11px] font-medium text-white/85 shadow-[0_8px_24px_-8px_rgba(0,0,0,0.8)] hairline"
            style={{ left: `${((hover + 0.5) / values.length) * 100}%` }}
          >
            {tip(hover)}
          </div>
        )}
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-[10px] text-white/30">
        {ticks.map((tick, index) => <span key={index}>{tick}</span>)}
      </div>
    </div>
  )
}

function Endings({ data }: { data: UsagePeriod }) {
  const slices = ENDINGS.map((ending) => ({ ...ending, value: ending.from.reduce((sum, key) => sum + (data.outcomes[key] || 0), 0) }))
  const total = slices.reduce((sum, slice) => sum + slice.value, 0)
  const finished = slices[0].value
  const radius = 44
  const circumference = 2 * Math.PI * radius
  const gap = total > slices[0].value ? 2.5 : 0          // a gap between slices, none for a full ring
  let offset = 0
  return (
    <div className="flex h-full items-center gap-5">
      <div className="relative shrink-0">
        <svg width="120" height="120" viewBox="0 0 120 120" role="img" aria-label="How requests ended">
          <circle cx="60" cy="60" r={radius} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="13" />
          {total > 0 && slices.filter((slice) => slice.value > 0).map((slice) => {
            const length = (slice.value / total) * circumference
            const dash = Math.max(0.5, length - gap)
            const element = (
              <circle key={slice.id} cx="60" cy="60" r={radius} fill="none" stroke={slice.color} strokeWidth="13"
                strokeDasharray={`${dash} ${circumference - dash}`} strokeDashoffset={-offset}
                transform="rotate(-90 60 60)">
                <title>{`${slice.label}: ${slice.value}`}</title>
              </circle>
            )
            offset += length
            return element
          })}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-[20px] font-semibold tracking-tight text-white">{total ? Math.round((finished / total) * 100) : 0}%</div>
          <div className="text-[10px] text-white/40">finished</div>
        </div>
      </div>
      <ul className="min-w-0 flex-1 space-y-1.5">
        {slices.map((slice) => (
          <li key={slice.id} className={cn('flex items-center gap-2 text-[12px]', slice.value ? 'text-white/75' : 'text-white/30')}>
            <span className="size-2.5 shrink-0 rounded-[3px]" style={{ background: slice.color, opacity: slice.value ? 1 : 0.35 }} />
            <slice.icon className="size-3.5 shrink-0 text-white/40" />
            <span className="truncate">{slice.label}</span>
            <span className="ml-auto font-mono text-[11px] tabular-nums text-white/45">{slice.value}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Horizontal bars, longest first, value at the tip. */
function Bars({ rows, unit }: { rows: { label: string; detail?: string; value: number; suffix?: string }[]; unit: string }) {
  const max = Math.max(1, ...rows.map((row) => row.value))
  return (
    <ul className="space-y-3">
      {rows.map((row, index) => (
        <li key={`${row.label}-${index}`}>
          <div className="mb-1 flex items-baseline gap-2 text-[12.5px]">
            <span className="truncate font-medium text-white/85">{row.label}</span>
            {row.detail && <span className="truncate font-mono text-[10.5px] text-white/30">{row.detail}</span>}
            <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-white/45">
              {row.value.toLocaleString()} {unit}{row.suffix ? ` · ${row.suffix}` : ''}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-white/[0.06]">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${(row.value / max) * 100}%` }}
              transition={{ duration: 0.55, delay: index * 0.04, ease: [0.32, 0.72, 0, 1] }}
              className="h-full rounded-full"
              style={{ background: ACCENT }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}
