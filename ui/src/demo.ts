import { defaultIsland, island, mascot, settings, type IslandState, type MascotState, type SettingsState } from './bridge'

export interface Frame {
  name: string
  hold: number
  island: Partial<IslandState>
  mascot?: Partial<MascotState> & { at?: 'notch' | 'cursor' | 'file' | 'export' }
}

const QUESTION = 'how do I export this as a PDF?'
const ANSWER_1 = "Easy. Open the File menu up top, it's right next to the app name."
const ANSWER_2 = ' Then pick Export, and choose PDF in the dialog that pops up.'

const ENGINE = { label: 'Claude', kind: 'subscription' as const, model: 'opus' }

export const FRAMES: Frame[] = [
  { name: 'idle', hold: 1600, island: { ...defaultIsland }, mascot: { mood: 'idle', at: 'notch' } },
  { name: 'listening-empty', hold: 700, island: { phase: 'listening', level: 0.2 }, mascot: { mood: 'listening', at: 'notch' } },
  { name: 'listening', hold: 1800, island: { phase: 'listening', level: 0.62, transcript: QUESTION }, mascot: { mood: 'listening', level: 0.6, at: 'notch' } },
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
    mascot: { mood: 'thinking', at: 'notch' },
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
  {
    name: 'plan',
    hold: 3200,
    island: {
      phase: 'answering',
      level: 0.5,
      engine: ENGINE,
      answer: 'Nice. Now click Password and authentication in the sidebar.',
      plan: ['Open your profile menu', 'Settings', 'Password and authentication', 'Enable two-factor', 'Save recovery codes'],
      planIndex: 2,
      walkthrough: { index: 2, total: 5, label: 'Password and authentication' },
      steps: [{ id: 'check', label: 'You opened Settings', status: 'done', detail: 'diff' }],
      latencyMs: 980,
    },
    mascot: { mood: 'pointing', label: 'Password', at: 'file', lean: -8, look: { x: -0.7, y: -0.7 } },
  },
  {
    name: 'results',
    hold: 2600,
    island: {
      phase: 'answering',
      level: 0.45,
      engine: ENGINE,
      answer: 'Found it. Your lease is in Documents, in the Apartment folder. Want me to open it?',
      results: [
        { title: 'Lease-2026.pdf', detail: '~/Documents/Apartment', path: '/Users/you/Documents/Apartment/Lease-2026.pdf' },
        { title: 'Lease renewal draft.pdf', detail: '~/Downloads', path: '/Users/you/Downloads/Lease renewal draft.pdf' },
      ],
      steps: [{ id: 'a1', label: 'Searching files for lease', status: 'done', detail: '2 found' }],
      latencyMs: 1610,
    },
    mascot: { mood: 'speaking', at: 'notch' },
  },
  {
    name: 'confirm',
    hold: 3000,
    island: {
      phase: 'answering',
      level: 0.3,
      done: true,
      engine: ENGINE,
      answer: 'I’ll text Sara that you’re running ten minutes late.',
      confirm: { title: 'Send to Sara', lines: ['“Running 10 minutes late, sorry!”', 'to +1 555 010 4444'], confirm: 'Send', name: 'send_message' },
      latencyMs: 1320,
    },
    mascot: { mood: 'thinking', at: 'notch' },
  },
  { name: 'mini', hold: 1400, island: { phase: 'answering', level: 0.5, answer: 'Opening Spotify.', engine: ENGINE, minimized: true }, mascot: { mood: 'idle', at: 'notch' } },
  { name: 'peek', hold: 1500, island: { ...defaultIsland }, mascot: { mood: 'happy', at: 'notch' } },
]


export const ERROR_FRAME: Frame = {
  name: 'error',
  hold: 2000,
  island: { phase: 'error', error: "I can't reach Claude. Sign in to Claude Code, or pick another brain in Settings." },
  mascot: { mood: 'error', at: 'notch' },
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
    { id: 'codex', label: 'ChatGPT', via: 'ChatGPT Plus / Pro via Codex CLI', kind: 'subscription', status: 'logged-out', vision: true, detail: 'Run codex login once and choose Sign in with ChatGPT.', login: 'codex login' },
    { id: 'cursor', label: 'Cursor', via: 'Your Cursor plan via Cursor CLI', kind: 'subscription', status: 'not-installed', install: 'curl https://cursor.com/install -fsS | bash', detail: 'Text answers; Plip describes the screen for it.' },
    { id: 'gemini', label: 'Gemini', via: 'Google account via Gemini CLI', kind: 'subscription', status: 'not-installed', install: 'npm i -g @google/gemini-cli' },
    { id: 'anthropic', label: 'Claude API', via: 'Anthropic API key', kind: 'api', status: 'missing-key', keyName: 'ANTHROPIC_API_KEY', vision: true },
  ],
  permissions: { screen: true, accessibility: true, microphone: true, speech: null },
  voice: { tts: 'elevenlabs', stt: 'assemblyai', elevenlabs: true, assemblyai: false },
  jev: { configured: true, enabled: true, latencyMs: 91 },
  skills: { travel: true, messages: true },
  companion: 'notch',
  stats: { actionsWeek: 47, answers: 128, minutesSaved: 226 },
  memory: {
    profile: {
      'name.full': 'Hussain Syed', 'name.first': 'Hussain', 'name.last': 'Syed', email: 'hussain@plip.app', phone: '+1 555 010 2000',
      'address.street': '1 Main St', 'address.city': 'Brooklyn', 'address.state': 'NY', 'address.postal': '11201', company: 'Plip Labs',
      title: 'Founder', github: 'hussainn7', birthday: '1999-03-07',
    },
    facts: [
      { id: 'f1', key: 'name.full', label: 'Name', value: 'Hussain Syed', sensitive: false, sources: ['Contacts', 'ChatGPT'] },
      { id: 'f2', key: 'email', label: 'Email', value: 'hussain@plip.app', sensitive: false, sources: ['Contacts', 'Browser autofill'] },
      { id: 'f3', key: 'phone', label: 'Phone', value: '+1 555 010 2000', sensitive: false, sources: ['iMessage'] },
      { id: 'f4', key: 'address.street', label: 'Street', value: '1 Main St', sensitive: false, sources: ['Browser autofill'] },
      { id: 'f5', key: 'company', label: 'Company', value: 'Plip Labs', sensitive: false, sources: ['Contacts'] },
      { id: 'f6', key: 'note', label: 'Note', value: 'Prefers aisle seats on flights', sensitive: false, sources: ['You told Plip'] },
      { id: 'f7', key: 'note', label: 'Note', value: 'Diet: vegetarian', sensitive: false, sources: ['ChatGPT'] },
      { id: 'f8', key: 'note', label: 'Note', value: '•••• 4567', sensitive: true, sources: ['You told Plip'] },
    ],
    imports: {
      contacts: { count: 12, at: Date.now() / 1000 - 86400 },
      autofill: { count: 9, at: Date.now() / 1000 - 3600 * 5 },
      imessage: { count: 0, at: Date.now() / 1000 - 600, error: 'Needs Full Disk Access (System Settings → Privacy & Security).' },
      chatgpt: { count: 23, at: Date.now() / 1000 - 3600 * 30 },
    },
    contacts: [{ handle: '+15550104444', count: 212, name: 'Sara' }],
    handles: ['+15550102000', 'hussain@icloud.com'],
  },
  routines: [
    { id: 'r1', name: 'Focus time', phrase: 'focus time', steps: ['open Linear', 'set dark mode on', 'set volume 20'], runs: 14, source: 'taught' },
    { id: 'r2', name: 'Wind down', phrase: 'wind down', steps: ['set dark mode on', 'open Spotify'], runs: 6, source: 'suggested' },
  ],
  suggestions: [
    { key: 'open_app:Calendar|open_app:Slack|open_app:Spotify', name: 'Morning setup', phrase: 'start my day', labels: ['open Slack', 'open Calendar', 'open Spotify'], days: 6, around: '9:05 AM' },
  ],
  phone: { enabled: true, handles: [], prefix: '/plip', detected: ['+15550102000', 'hussain@icloud.com'], status: 'listening', lastCommand: 'find my lease pdf' },
  history: [
    { question: 'where is the wifi menu', answer: "It's the fan-shaped icon in your menu bar, just left of the battery.", at: 1759370000, engine: 'Claude' },
    { question: 'what does this error mean', answer: 'Your build can’t find the module “sharp”. Run npm install in the project folder, then restart the dev server.', at: 1759371800, engine: 'Claude' },
    { question: 'how do I export this as a PDF?', answer: 'Open the File menu up top, then pick Export and choose PDF.', at: 1759373600, engine: 'Claude' },
  ],
}

export function loadDemoSettings() {
  settings.set(DEMO_SETTINGS)
}
