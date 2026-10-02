import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { isNative, onMockCommand, send, settings } from './bridge'
import { loadDemoSettings } from './demo'
import './styles.css'
import { Island } from './views/Island'
import { MascotView } from './views/MascotView'
import { Settings } from './views/Settings'
import { Showcase } from './views/Showcase'

type Surface = 'island' | 'mascot' | 'settings' | 'showcase'

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
    if (command.cmd === 'set-depth') settings.set({ depth: command.depth as 'fast' | 'balanced' | 'deep' })
    if (command.cmd === 'set-walkthroughs') settings.set({ walkthroughs: Boolean(command.enabled) })
    if (command.cmd === 'set-voice') {
      settings.set((current) => ({ voice: { ...current.voice, ...(command.tts ? { tts: command.tts } : {}), ...(command.stt ? { stt: command.stt } : {}) } as typeof current.voice }))
    }
  })
}

const views: Record<Surface, React.ReactNode> = {
  island: <Island />,
  mascot: <MascotView />,
  settings: <Settings />,
  showcase: <Showcase />,
}

createRoot(document.getElementById('root')!).render(<StrictMode>{views[surface] ?? <Showcase />}</StrictMode>)

// The native host queues messages until the page says it can receive them.
requestAnimationFrame(() => send('ready', { surface }))
