import {
  AppWindow, BookUser, CalendarClock, FolderSearch, Plane, SlidersHorizontal, Type, MousePointerClick,
} from 'lucide-react'
import { send, type SettingsState } from '../../bridge'
import { cn } from '../../components/bits'
import { Card, Header, IconTile, Section, Segmented, Toggle } from './ui'

const SKILLS = [
  { id: 'apps', icon: AppWindow, name: 'Apps & web', text: 'Open apps, links and web searches.', example: 'Open Spotify', gradient: 'bg-gradient-to-br from-sky-200 to-cyan-400' },
  { id: 'control', icon: MousePointerClick, name: 'Hands', text: 'Clicks, scrolls, presses keys and drags for you. Anything that buys, sends or deletes asks first.', example: 'Play this song', gradient: 'bg-gradient-to-br from-blue-200 to-indigo-400', asks: 'Risky clicks ask' },
  { id: 'files', icon: FolderSearch, name: 'Files & desktop', text: 'Find anything with Spotlight. Tidy your desktop into folders (asks first, and you can undo it).', example: 'Find my lease PDF', gradient: 'bg-gradient-to-br from-teal-200 to-emerald-400', asks: 'Asks first' },
  { id: 'writing', icon: Type, name: 'Writing', text: 'Types for you, rewrites or translates the text you selected.', example: 'Make this email friendlier', gradient: 'bg-gradient-to-br from-violet-200 to-indigo-400' },
  { id: 'planning', icon: CalendarClock, name: 'Reminders & timers', text: 'Reminders, notes and timers that ping you when they’re done.', example: 'Remind me to call Mom at 6', gradient: 'bg-gradient-to-br from-rose-200 to-pink-400' },
  { id: 'travel', icon: Plane, name: 'Travel', text: 'Opens Google Flights, then reads you the best options.', example: 'Flights to Miami next Friday', gradient: 'bg-gradient-to-br from-indigo-200 to-sky-400' },
  { id: 'system', icon: SlidersHorizontal, name: 'Mac controls', text: 'Dark mode, volume, mute, and your Apple Shortcuts.', example: 'Turn on dark mode', gradient: 'bg-gradient-to-br from-slate-200 to-slate-400' },
  { id: 'memory', icon: BookUser, name: 'Memory', text: 'Remembers what you tell it and uses your details.', example: 'Remember I prefer aisle seats', gradient: 'bg-gradient-to-br from-cyan-200 to-sky-500' },
]

export function SkillsTab({ state }: { state: SettingsState }) {
  const on = (id: string) => state.skills[id] !== false
  return (
    <div>
      <Header
        eyebrow="Skills"
        title="What Plip can do for you"
        subtitle="Ask in plain words. Anything that moves your files shows you a preview in the notch first, and nothing happens until you say yes."
      />
      <Section title="How Plip shows up">
        <Card className="flex items-center justify-between gap-4">
          <div>
            <div className="text-[13.5px] font-semibold">
              {state.companion === 'notch' ? 'Lives in the notch' : state.companion === 'cursor' ? 'Follows your cursor' : 'Stays hidden'}
            </div>
            <div className="text-[12px] text-white/40">
              {state.companion === 'notch'
                ? 'Plip drips out of the notch only to point at something, then floats back.'
                : state.companion === 'cursor'
                  ? 'A small droplet trails your cursor and flies off to point.'
                  : 'No floating droplet. Answers and checklists stay in the notch.'}
            </div>
          </div>
          <Segmented
            value={state.companion}
            options={[{ value: 'notch', label: 'Notch' }, { value: 'cursor', label: 'Cursor' }, { value: 'hidden', label: 'Hidden' }]}
            onChange={(style) => send('set-companion', { style })}
          />
        </Card>
      </Section>
      <Section title="Skills">
        <div className="grid grid-cols-2 gap-3">
          {SKILLS.map((skill) => (
            <Card key={skill.id} className={cn('flex gap-3.5 transition', !on(skill.id) && 'opacity-55')}>
              <IconTile icon={skill.icon} gradient={skill.gradient} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[13.5px] font-semibold tracking-tight">{skill.name}</span>
                    {skill.asks && <span className="rounded-md bg-sun/12 px-1.5 py-0.5 text-[9.5px] font-bold uppercase tracking-wider text-amber-200">{skill.asks}</span>}
                  </div>
                  <Toggle label={skill.name} checked={on(skill.id)} onChange={(enabled) => send('set-skill', { skill: skill.id, enabled })} />
                </div>
                <div className="mt-1 text-[12px] leading-relaxed text-white/45">{skill.text}</div>
                <div className="mt-2 inline-flex rounded-full bg-white/[0.05] px-2 py-0.5 text-[11.5px] text-white/60 hairline">“{skill.example}”</div>
              </div>
            </Card>
          ))}
        </div>
      </Section>
    </div>
  )
}
