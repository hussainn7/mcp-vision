import { defaultIsland, island, mascot, settings, type IslandState, type MascotState, type SettingsState } from './bridge'

export interface Frame {
  name: string
  hold: number
  island: Partial<IslandState>
  mascot?: Partial<MascotState> & { at?: 'cursor' | 'file' | 'export' }
}

const QUESTION = 'how do I export this as a PDF?'
const ANSWER_1 = "Easy. Open the File menu up top, it's right next to the app name."
const ANSWER_2 = ' Then pick Export, and choose PDF in the dialog that pops up.'

const ENGINE = { label: 'Claude', kind: 'subscription' as const, model: 'opus' }

export const FRAMES: Frame[] = [
  { name: 'idle', hold: 1600, island: { ...defaultIsland }, mascot: { mood: 'idle', at: 'cursor' } },
  { name: 'listening-empty', hold: 700, island: { phase: 'listening', level: 0.2 }, mascot: { mood: 'listening', at: 'cursor' } },
  { name: 'listening', hold: 1800, island: { phase: 'listening', level: 0.62, transcript: QUESTION }, mascot: { mood: 'listening', level: 0.6, at: 'cursor' } },
  {
    name: 'thinking',
    hold: 1700,
    island: {
      phase: 'thinking',
      level: 0,
      transcript: QUESTION,
      steps: [
        { id: 'look', label: 'Looked at 2 screens', status: 'done', detail: '84ms' },
        { id: 'route', label: 'Jev: needs screen', status: 'done', detail: '91ms' },
        { id: 'think', label: 'Claude is thinking', status: 'active' },
      ],
    },
    mascot: { mood: 'thinking', at: 'cursor' },
  },
  {
    name: 'answering',
    hold: 2600,
    island: {
      phase: 'answering',
      level: 0.55,
      answer: ANSWER_1,
      engine: ENGINE,
      walkthrough: { index: 0, total: 2, label: 'File menu' },
      steps: [
        { id: 'look', label: 'Looked at 2 screens', status: 'done', detail: '84ms' },
        { id: 'snap', label: 'Snapped to “File”', status: 'done', detail: 'AX' },
      ],
      latencyMs: 1240,
    },
    mascot: { mood: 'pointing', label: 'File menu', at: 'file', lean: -8, look: { x: -0.4, y: -1 } },
  },
  {
    name: 'walkthrough',
    hold: 3200,
    island: {
      phase: 'answering',
      level: 0.4,
      answer: ANSWER_1 + ANSWER_2,
      done: true,
      engine: ENGINE,
      walkthrough: { index: 1, total: 2, label: 'Export…' },
      steps: [
        { id: 'look', label: 'Looked at 2 screens', status: 'done', detail: '84ms' },
        { id: 'check', label: 'You opened File', status: 'done', detail: 'diff' },
      ],
      latencyMs: 1240,
    },
    mascot: { mood: 'pointing', label: 'Export…', at: 'export', lean: 6, look: { x: 0.3, y: 0.6 } },
  },
  { name: 'peek', hold: 1500, island: { ...defaultIsland }, mascot: { mood: 'happy', at: 'cursor' } },
]

export const ERROR_FRAME: Frame = {
  name: 'error',
  hold: 2000,
  island: { phase: 'error', error: "I can't reach Claude. Sign in to Claude Code, or pick another brain in Settings." },
  mascot: { mood: 'error', at: 'cursor' },
}

export function applyFrame(frame: Frame) {
  island.set({ ...defaultIsland, notch: island.get().notch, ...frame.island })
  if (frame.mascot) {
    const { at: _at, ...rest } = frame.mascot
    void _at
    mascot.set({ level: 0, lean: 0, look: { x: 0, y: 0 }, label: '', ...rest })
  }
}

export const DEMO_SETTINGS: Partial<SettingsState> = {
  engines: [
    { id: 'claude-code', label: 'Claude', via: 'Claude Pro / Max via Claude Code', kind: 'subscription', status: 'ready', selected: true, vision: true, detail: 'Signed in as you. Sees screenshots; streams answers.' },
    { id: 'codex', label: 'ChatGPT', via: 'ChatGPT Plus / Pro via Codex CLI', kind: 'subscription', status: 'logged-out', vision: true, detail: 'Run codex login once and pick “Sign in with ChatGPT”.' },
    { id: 'cursor', label: 'Cursor', via: 'Cursor plan via cursor-agent', kind: 'subscription', status: 'not-installed', install: 'curl https://cursor.com/install -fsS | bash', detail: 'Text answers; Blip describes the screen for it.' },
    { id: 'gemini', label: 'Gemini', via: 'Google account via Gemini CLI', kind: 'subscription', status: 'not-installed', install: 'npm i -g @google/gemini-cli' },
    { id: 'anthropic', label: 'Claude API', via: 'Anthropic API key', kind: 'api', status: 'missing-key', keyName: 'ANTHROPIC_API_KEY', vision: true },
  ],
  permissions: { screen: true, accessibility: true, microphone: true, speech: null },
  voice: { tts: 'elevenlabs', stt: 'assemblyai', elevenlabs: true, assemblyai: false },
  jev: { configured: true, enabled: true, latencyMs: 91 },
  history: [
    { question: 'where is the wifi menu', answer: "It's the fan-shaped icon in your menu bar, just left of the battery.", at: 1759370000, engine: 'Claude' },
    { question: 'what does this error mean', answer: 'Your build can’t find the module “sharp”. Run npm install in the project folder, then restart the dev server.', at: 1759371800, engine: 'Claude' },
    { question: 'how do I export this as a PDF?', answer: 'Open the File menu up top, then pick Export and choose PDF.', at: 1759373600, engine: 'Claude' },
  ],
}

export function loadDemoSettings() {
  settings.set(DEMO_SETTINGS)
}
