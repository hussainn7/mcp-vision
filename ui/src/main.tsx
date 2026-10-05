import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { isNative, onMockCommand, send, settings, type SettingsState } from './bridge'
import { loadDemoSettings } from './demo'
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
  onMockCommand((command) => {
    console.debug('[plip] command', command)
    if (command.cmd === 'select-engine') {
      settings.set((current) => ({
        engines: current.engines.map((engine) => ({ ...engine, selected: engine.id === command.id })),
      }))
    }
    if (command.cmd === 'finish-onboarding') settings.set({ onboarded: true })
    if (command.cmd === 'quick-connect') settings.set({ connect: 'A Terminal window opened to sign in. Finish there, then come back.' })
    if (command.cmd === 'report-issue') settings.set({ report: 'sent' })
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
