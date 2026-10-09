import { AnimatePresence, motion } from 'motion/react'
import {
  AudioLines, BookUser, Brain, ChartColumn, CircleUserRound, House, Settings as Gear, ShieldCheck,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { send, settings, useStore } from '../../bridge'
import { Mascot } from '../../components/Mascot'
import { Chord, cn } from '../../components/bits'
import { AccountTab, Avatar, SignIn } from './account'
import { HistoryPanel, PermissionsTab, VoiceTab } from './basics'
import { BrainTab } from './brain'
import { GeneralTab, type Composer } from './general'
import { HomeTab } from './home'
import { MemoryTab } from './memory'
import { Onboarding } from './onboarding'
import { Header, Segmented } from './ui'
import { UsagePanel } from './usage'

export type Tab = 'home' | 'account' | 'general' | 'brain' | 'voice' | 'permissions' | 'memory' | 'activity'
type Activity = 'usage' | 'history'
type Icon = React.ComponentType<{ className?: string }>

const GROUPS: { title: string; tabs: { id: Tab; label: string; icon: Icon }[] }[] = [
  {
    title: '',
    tabs: [
      { id: 'home', label: 'Home', icon: House },
      { id: 'account', label: 'Account', icon: CircleUserRound },
      { id: 'general', label: 'General', icon: Gear },
    ],
  },
  {
    title: 'Plip',
    tabs: [
      { id: 'brain', label: 'Brain', icon: Brain },
      { id: 'voice', label: 'Voice', icon: AudioLines },
      { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
    ],
  },
  {
    title: 'You',
    tabs: [
      { id: 'memory', label: 'Memory', icon: BookUser },
      { id: 'activity', label: 'Activity', icon: ChartColumn },
    ],
  },
]

const TABS = new Set<string>(GROUPS.flatMap((group) => group.tabs.map((item) => item.id)))

/** Where a link lands. Old names still work: the menu bar asks for "report", the notch for "history". */
interface Place { tab: Tab; activity?: Activity; composer?: Composer }

function placeFromHash(): Place | null {
  const name = new URLSearchParams(location.hash.split('?')[1]).get('tab')
  if (!name) return null
  if (name === 'about' || name === 'skills') return { tab: 'general' }
  if (name === 'report') return { tab: 'general', composer: 'bug' }
  if (name === 'usage' || name === 'history') return { tab: 'activity', activity: name }
  return TABS.has(name) ? { tab: name as Tab } : { tab: 'home' }
}

export function Settings() {
  const state = useStore(settings)
  const [place, setPlace] = useState<Place>(() => placeFromHash() || { tab: 'home' })
  const [activity, setActivity] = useState<Activity>(() => placeFromHash()?.activity || 'usage')
  const tab = place.tab
  const setTab = (next: Tab) => setPlace({ tab: next })
  const main = useRef<HTMLElement>(null)
  useEffect(() => {
    main.current?.scrollTo({ top: 0 })                 // a block: newer WebKit/Chrome return a promise from scrollTo
  }, [place])

  useEffect(() => {
    const onHash = () => {
      const next = placeFromHash()
      if (!next) return
      setPlace(next)
      if (next.activity) setActivity(next.activity)
    }
    window.addEventListener('hashchange', onHash)
    send('settings-ready')
    // Back from System Settings or a browser sign-in: check permissions and brains again (at most every 5 s).
    let checked = Date.now()
    const onFocus = () => {
      if (document.visibilityState === 'hidden' || Date.now() - checked < 5000) return
      checked = Date.now()
      send('refresh')
    }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      window.removeEventListener('hashchange', onHash)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [])

  const engine = state.engines.find((item) => item.selected)
  const ready = engine && (engine.status === 'ready' || engine.status === 'unknown')

  if (state.account.required) return <SignIn state={state} />
  const user = state.account.user

  return (
    <div className="relative flex h-full overflow-clip bg-ink text-white noise">
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
        <nav className="space-y-5 overflow-y-auto scrollbar-none">
          {GROUPS.map((group) => (
            <div key={group.title || 'top'}>
              {group.title && <div className="mb-1.5 px-2.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-white/25">{group.title}</div>}
              <div className="space-y-0.5">
                {group.tabs.filter(({ id }) => id !== 'account' || user).map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    onClick={() => setTab(id)}
                    className={cn(
                      'relative flex w-full items-center gap-3 rounded-[10px] px-2.5 py-2 text-[13.5px] font-medium transition',
                      tab === id ? 'text-white' : 'text-white/50 hover:bg-white/[0.04] hover:text-white/80',
                    )}
                  >
                    {tab === id && (
                      <motion.span layoutId="nav" className="absolute inset-0 rounded-[10px] bg-white/[0.08] hairline" transition={{ type: 'spring', stiffness: 500, damping: 38 }}>
                        <span className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full brand-gradient" />
                      </motion.span>
                    )}
                    {id === 'account' && user ? <Avatar user={user} size={18} className="relative" /> : <Icon className="relative size-[17px]" />}
                    <span className="relative">{label}</span>
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
            <Chord className="h-6 min-w-6 text-[12px]" />
            <span className="ml-1 text-[11px] text-white/35">hold to talk</span>
          </div>
        </div>
      </aside>

      <main ref={main} className="relative z-10 flex-1 overflow-y-auto scrollbar-none px-9 pb-10 pt-8">
        <AnimatePresence mode="wait">
          <motion.div
            key={tab + (place.composer ?? '')}
            initial={{ opacity: 0, y: 8, filter: 'blur(6px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -6, filter: 'blur(4px)' }}
            transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
            className="mx-auto max-w-[860px]"
          >
            {tab === 'home' && <HomeTab state={state} go={setTab} />}
            {tab === 'account' && <AccountTab state={state} />}
            {tab === 'general' && <GeneralTab state={state} composer={place.composer} />}
            {tab === 'brain' && <BrainTab state={state} />}
            {tab === 'voice' && <VoiceTab state={state} />}
            {tab === 'permissions' && <PermissionsTab state={state} />}
            {tab === 'memory' && <MemoryTab state={state} />}
            {tab === 'activity' && (
              <div>
                <Header eyebrow="Activity" title="What you asked, and what it took"
                  action={<Segmented value={activity} onChange={setActivity}
                    options={[{ value: 'usage', label: 'Usage' }, { value: 'history', label: 'History' }]} />} />
                {activity === 'usage' ? <UsagePanel state={state} /> : <HistoryPanel state={state} />}
              </div>
            )}
          </motion.div>
        </AnimatePresence>
      </main>
      {state.onboarded === false && <Onboarding state={state} />}
    </div>
  )
}
