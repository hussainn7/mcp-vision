import { AnimatePresence, motion } from 'motion/react'
import {
  AudioLines, BookUser, BrainCircuit, Clock3, House, Info, Repeat2, ShieldCheck, WandSparkles,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { send, settings, useStore } from '../../bridge'
import { Mascot } from '../../components/Mascot'
import { Keycap, cn } from '../../components/bits'
import { AboutTab, HistoryTab, PermissionsTab, VoiceTab } from './basics'
import { BrainTab } from './brain'
import { HomeTab } from './home'
import { MemoryTab } from './memory'
import { RoutinesTab } from './routines'
import { SkillsTab } from './skills'

export type Tab = 'home' | 'brain' | 'skills' | 'memory' | 'routines' | 'voice' | 'permissions' | 'history' | 'about'

const GROUPS: { title: string; tabs: { id: Tab; label: string; icon: React.ComponentType<{ className?: string }> }[] }[] = [
  { title: '', tabs: [{ id: 'home', label: 'Home', icon: House }] },
  {
    title: 'Assistant',
    tabs: [
      { id: 'skills', label: 'Skills', icon: WandSparkles },
      { id: 'memory', label: 'Memory', icon: BookUser },
      { id: 'routines', label: 'Routines', icon: Repeat2 },
    ],
  },
  {
    title: 'Setup',
    tabs: [
      { id: 'brain', label: 'Brain', icon: BrainCircuit },
      { id: 'voice', label: 'Voice', icon: AudioLines },
      { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
    ],
  },
  {
    title: 'More',
    tabs: [
      { id: 'history', label: 'History', icon: Clock3 },
      { id: 'about', label: 'About', icon: Info },
    ],
  },
]

function tabFromHash(): Tab | null {
  return new URLSearchParams(location.hash.split('?')[1]).get('tab') as Tab | null
}

export function Settings() {
  const state = useStore(settings)
  const [tab, setTab] = useState<Tab>(() => tabFromHash() || 'home')

  useEffect(() => {
    const onHash = () => {
      const next = tabFromHash()
      if (next) setTab(next)
    }
    window.addEventListener('hashchange', onHash)
    send('settings-ready')
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const engine = state.engines.find((item) => item.selected)
  const ready = engine && (engine.status === 'ready' || engine.status === 'unknown')

  return (
    <div className="relative flex h-full overflow-hidden bg-ink text-white noise">
      <div className="pointer-events-none absolute -left-48 -top-64 size-[560px] rounded-full bg-plip-500/[0.14] blur-[130px] animate-aurora" />
      <div className="pointer-events-none absolute -right-56 top-56 size-[460px] rounded-full bg-sky-glow/[0.08] blur-[130px] animate-aurora [animation-delay:-7s]" />

      <aside className="relative z-10 flex w-[220px] shrink-0 flex-col border-r border-white/[0.06] bg-black/25 px-3 pb-4 pt-5 backdrop-blur-xl">
        <div className="mb-5 flex items-center gap-2.5 px-2">
          <Mascot size={30} mood="happy" glow={false} />
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">Plip</div>
            <div className="font-mono text-[10.5px] text-white/30">v{state.version}</div>
          </div>
        </div>
        <nav className="space-y-4 overflow-y-auto scrollbar-none">
          {GROUPS.map((group) => (
            <div key={group.title || 'top'}>
              {group.title && <div className="mb-1 px-2.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-white/25">{group.title}</div>}
              <div className="space-y-0.5">
                {group.tabs.map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    onClick={() => setTab(id)}
                    className={cn(
                      'relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13px] font-medium transition',
                      tab === id ? 'text-white' : 'text-white/50 hover:bg-white/[0.04] hover:text-white/80',
                    )}
                  >
                    {tab === id && (
                      <motion.span layoutId="nav" className="absolute inset-0 rounded-lg bg-white/[0.07] hairline" transition={{ type: 'spring', stiffness: 500, damping: 38 }}>
                        <span className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full brand-gradient" />
                      </motion.span>
                    )}
                    <Icon className="relative size-4" />
                    <span className="relative">{label}</span>
                    {id === 'routines' && state.suggestions.length > 0 && (
                      <span className="relative ml-auto rounded-full bg-plip-400/20 px-1.5 text-[10px] font-bold text-plip-200">{state.suggestions.length}</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className="mt-auto space-y-2.5 rounded-xl bg-white/[0.03] p-3 hairline">
          <div className="flex items-center gap-2 text-[11.5px]">
            <span className={cn('size-1.5 rounded-full', ready ? 'bg-mint shadow-[0_0_8px_rgba(52,211,153,0.8)]' : 'bg-sun')} />
            <span className="text-white/60">{ready ? `Ready · ${engine?.label}` : 'Needs a brain'}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Keycap className="h-6 min-w-6 text-[12px]">⌃</Keycap>
            <Keycap className="h-6 min-w-6 text-[12px]">⌥</Keycap>
            <span className="ml-1 text-[11px] text-white/35">hold to talk</span>
          </div>
        </div>
      </aside>

      <main className="relative z-10 flex-1 overflow-y-auto scrollbar-none px-9 pb-10 pt-8">
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 8, filter: 'blur(6px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -6, filter: 'blur(4px)' }}
            transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
            className="mx-auto max-w-[860px]"
          >
            {tab === 'home' && <HomeTab state={state} go={setTab} />}
            {tab === 'brain' && <BrainTab state={state} />}
            {tab === 'skills' && <SkillsTab state={state} />}
            {tab === 'memory' && <MemoryTab state={state} />}
            {tab === 'routines' && <RoutinesTab state={state} />}
            {tab === 'voice' && <VoiceTab state={state} />}
            {tab === 'permissions' && <PermissionsTab state={state} />}
            {tab === 'history' && <HistoryTab state={state} />}
            {tab === 'about' && <AboutTab state={state} />}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}
