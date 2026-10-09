import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { isNative, onMockCommand, send, settings, type Engine, type SettingsState } from './bridge'
import { DEMO_USER, loadDemoSettings } from './demo'
import './styles.css'
import { Guide } from './views/Guide'
import { Island } from './views/Island'
import { MascotView } from './views/MascotView'
import { Settings } from './views/settings'
import { Showcase } from './views/Showcase'

type Surface = 'island' | 'mascot' | 'settings' | 'showcase' | 'guide'

const surface = (window.__PLIP_SURFACE__ || location.hash.slice(1).split('?')[0] || (isNative() ? 'island' : 'showcase')) as Surface
document.body.dataset.surface = surface

if (!isNative()) {
  // Browser preview: show realistic data and log commands the app would send.
  loadDemoSettings()
  // #settings?signin opens the sign-in screen.
  if (new URLSearchParams(location.hash.split('?')[1]).has('signin'))
    settings.set((current) => ({ account: { ...current.account, required: true, user: null } }))
  onMockCommand((command) => {
    console.debug('[plip] command', command)
    if (command.cmd === 'select-engine') {
      settings.set((current) => ({
        engines: current.engines.map((engine) => ({ ...engine, selected: engine.id === command.id })),
      }))
    }
    if (command.cmd === 'finish-onboarding') settings.set({ onboarded: true })
    if (command.cmd === 'tour-start') settings.set({ onboarded: false, tour: { step: 'welcome' } })
    if (command.cmd === 'quick-connect') settings.set({ connect: 'Pick the AI you use below, or get a free one from Google.' })
    if (command.cmd === 'engine-connect') {
      // Browser preview: walk through what the app does (install, browser sign-in, connected).
      const id = String(command.id)
      const step = (connect: Engine['connect'], patch: Partial<Engine> = {}) =>
        settings.set((current) => ({ engines: current.engines.map((engine) => (engine.id === id ? { ...engine, ...patch, connect } : engine)) }))
      const label = settings.get().engines.find((engine) => engine.id === id)?.label ?? 'it'
      const installed = settings.get().engines.find((engine) => engine.id === id)?.status !== 'not-installed'
      step({ state: installed ? 'signing-in' : 'installing', message: installed ? `Finish signing in to ${label} in your browser…` : `Installing ${label}’s app… (about a minute)` })
      window.setTimeout(() => step({ state: 'signing-in', message: `Finish signing in to ${label} in your browser…`, url: 'https://claude.com/cai/oauth/authorize', needsCode: true }), installed ? 0 : 1600)
      window.setTimeout(() => settings.set((current) => ({
        engines: current.engines.map((engine) => (engine.id === id ? { ...engine, status: 'ready', selected: true, connect: undefined, detail: 'Signed in as you' } : { ...engine, selected: false })),
      })), installed ? 2600 : 4200)
    }
    if (command.cmd === 'paste-key' || (command.cmd === 'set-key' && command.name === 'GEMINI_API_KEY')) {
      // Browser preview: Google "takes" the key after a beat; with no other brain ready, Gemini becomes the brain.
      settings.set({ keyCheck: { name: 'GEMINI_API_KEY', state: 'checking', message: 'Checking the key with Google…' } })
      window.setTimeout(() => settings.set((current) => {
        const other = current.engines.some((engine) => engine.status === 'ready' && engine.id !== 'gemini-api')
        return {
          keyCheck: { name: 'GEMINI_API_KEY', state: 'ok', message: '' },
          engines: current.engines.map((engine) => engine.id === 'gemini-api' ? { ...engine, status: 'ready', selected: !other }
            : other ? engine : { ...engine, selected: false }),
        }
      }), 900)
    }
    if (command.cmd === 'engine-connect-cancel')
      settings.set((current) => ({ engines: current.engines.map((engine) => (engine.id === command.id ? { ...engine, connect: undefined } : engine)) }))
    if (command.cmd === 'report-issue' || command.cmd === 'request-feature') settings.set({ report: 'sent' })
    if (command.cmd === 'report-reset') settings.set({ report: '' })
    // Browser preview: the browser "comes back" from Google after a moment.
    const account = (patch: Partial<SettingsState['account']>) => settings.set((current) => ({ account: { ...current.account, ...patch } }))
    if (command.cmd === 'account-sign-in') {
      account({ status: 'waiting', error: '', url: 'https://example.supabase.co/auth/v1/authorize?provider=google' })
      window.setTimeout(() => {
        if (settings.get().account.status === 'waiting') account({ status: '', url: '', required: false, user: DEMO_USER })
      }, 1600)
    }
    if (command.cmd === 'set-update-check')
      settings.set((current) => ({ update: { ...current.update, enabled: Boolean(command.enabled), available: command.enabled ? current.update.available : null } }))
    if (command.cmd === 'account-cancel') account({ status: '', url: '' })
    if (command.cmd === 'account-sign-out') account({ status: '', required: true, user: null })
    if (command.cmd === 'set-depth') settings.set({ depth: command.depth as 'fast' | 'balanced' | 'deep' })
    if (command.cmd === 'set-walkthroughs') settings.set({ walkthroughs: Boolean(command.enabled) })
    if (command.cmd === 'set-voice') {
      settings.set((current) => ({ voice: { ...current.voice, ...(command.tts ? { tts: command.tts } : {}), ...(command.stt ? { stt: command.stt } : {}) } as typeof current.voice }))
    }
    // Browser preview: Parakeet's download fills up over a few seconds, then it's listening.
    const parakeet = (patch: Partial<NonNullable<SettingsState['voice']['parakeet']>>) =>
      settings.set((current) => ({ voice: { ...current.voice, parakeet: { ...current.voice.parakeet!, ...patch } } }))
    if (command.cmd === 'parakeet-download' && settings.get().voice.parakeet) {
      settings.set((current) => ({ voice: { ...current.voice, stt: 'parakeet' } }))
      const total = settings.get().voice.parakeet!.total
      parakeet({ state: 'downloading', done: 0, error: '' })
      const timer = window.setInterval(() => {
        const now = settings.get().voice.parakeet!
        if (now.state !== 'downloading') return window.clearInterval(timer)
        const done = Math.min(total, now.done + total / 12)
        parakeet(done >= total ? { state: 'ready', done: 0 } : { done })
        if (done >= total) window.clearInterval(timer)
      }, 350)
    }
    if (command.cmd === 'parakeet-cancel') parakeet({ state: 'missing', done: 0 })
    if (command.cmd === 'parakeet-remove') {
      parakeet({ state: 'missing', done: 0 })
      settings.set((current) => ({ voice: { ...current.voice, stt: 'apple' } }))
    }
  })
}

const views: Record<Surface, React.ReactNode> = {
  island: <Island />,
  mascot: <MascotView />,
  settings: <Settings />,
  showcase: <Showcase />,
  guide: <Guide />,
}

createRoot(document.getElementById('root')!).render(<StrictMode>{views[surface] ?? <Showcase />}</StrictMode>)

// The native host queues messages until the page says it can receive them.
requestAnimationFrame(() => send('ready', { surface }))
