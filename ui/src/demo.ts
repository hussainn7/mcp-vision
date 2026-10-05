import { defaultIsland, island, mascot, settings, type IslandState, type MascotState, type SettingsState, type UsagePeriod, type UsageState } from './bridge'

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
      answer: 'Nice, the File menu is open. Now hover Export To.',
      plan: ['Open the File menu', 'Export To', 'Pick PDF', 'Choose image quality', 'Save to Desktop'],
      planIndex: 1,
      walkthrough: { index: 1, total: 5, label: 'Export To' },
      steps: [{ id: 'check', label: 'You opened File', status: 'done', detail: 'diff' }],
      latencyMs: 980,
    },
    mascot: { mood: 'pointing', label: 'Export To', at: 'export', lean: 6, look: { x: 0.3, y: 0.6 } },
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
      answer: 'I’ll sort your desktop into folders. Here’s the plan.',
      confirm: { title: 'Tidy 17 files', lines: ['Screenshots → 9', 'Documents → 5', 'Images → 3'], confirm: 'Tidy up', name: 'organize_desktop' },
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
      { id: 'f3', key: 'phone', label: 'Phone', value: '+1 555 010 2000', sensitive: false, sources: ['Contacts'] },
      { id: 'f4', key: 'address.street', label: 'Street', value: '1 Main St', sensitive: false, sources: ['Browser autofill'] },
      { id: 'f5', key: 'company', label: 'Company', value: 'Plip Labs', sensitive: false, sources: ['Contacts'] },
      { id: 'f6', key: 'note', label: 'Note', value: 'Prefers aisle seats on flights', sensitive: false, sources: ['You told Plip'] },
      { id: 'f7', key: 'note', label: 'Note', value: 'Diet: vegetarian', sensitive: false, sources: ['ChatGPT'] },
      { id: 'f8', key: 'note', label: 'Note', value: '•••• 4567', sensitive: true, sources: ['You told Plip'] },
    ],
    imports: {
      contacts: { count: 12, at: Date.now() / 1000 - 86400 },
      autofill: { count: 9, at: Date.now() / 1000 - 3600 * 5 },
      mail: { count: 0, at: Date.now() / 1000 - 600, error: 'Allow Automation for Mail in System Settings → Privacy & Security.' },
      chatgpt: { count: 23, at: Date.now() / 1000 - 3600 * 30 },
    },
    contacts: [{ handle: '+15550104444', count: 212, name: 'Sara' }],
    handles: ['+15550102000', 'hussain@icloud.com'],
  },
  history: [
    { question: 'where is the wifi menu', answer: "It's the fan-shaped icon in your menu bar, just left of the battery.", at: 1759370000, engine: 'Claude' },
    { question: 'what does this error mean', answer: 'Your build can’t find the module “sharp”. Run npm install in the project folder, then restart the dev server.', at: 1759371800, engine: 'Claude' },
    { question: 'how do I export this as a PDF?', answer: 'Open the File menu up top, then pick Export and choose PDF.', at: 1759373600, engine: 'Claude' },
  ],
}

// A believable month of use for the Usage tab: busier mid-week and late afternoon, mostly Claude Code.
function demoPeriod(days: number): UsagePeriod {
  const wave = (n: number) => (Math.sin(n * 12.9898) * 43758.5453) % 1
  const today = new Date(2026, 9, 2)
  const perDay = Array.from({ length: days }, (_, index) => {
    const date = new Date(today)
    date.setDate(today.getDate() - (days - 1 - index))
    const weekday = date.getDay()
    const requests = Math.max(0, Math.round((weekday === 0 || weekday === 6 ? 3 : 9) + Math.abs(wave(index + days)) * 9 + index * 0.15))
    const day = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    return { day, requests, cost: +(requests * 0.061).toFixed(4) }
  })
  const requests = perDay.reduce((sum, day) => sum + day.requests, 0)
  const shape = [1, 0, 0, 0, 0, 0, 0, 1, 2, 4, 6, 6, 4, 5, 6, 7, 9, 12, 8, 5, 3, 2, 2, 1]
  const weight = shape.reduce((sum, value) => sum + value, 0)
  const perHour = shape.map((value) => Math.round((value / weight) * requests))
  const cost = perDay.reduce((sum, day) => sum + day.cost, 0)
  const share = (part: number) => Math.round(requests * part)
  return {
    requests, turns: Math.round(requests * 2.3), actions: Math.round(requests * 1.9), tasks: share(0.31),
    tokensIn: requests * 9200, tokensOut: requests * 310, cacheRead: requests * 6100,
    cost: +cost.toFixed(2), estimatedShare: 0.04, perDay, perHour, busiestHour: 17,
    outcomes: { done: share(0.46), answered: share(0.33), unverified: share(0.04), paused: share(0.03), waiting: share(0.06),
                failed: share(0.05), stopped: share(0.03) },
    engines: [
      { label: 'Claude', model: 'claude-opus-5-5', requests: share(0.86), cost: +(cost * 0.9).toFixed(2), lane: 'Claude plan (Pro/Max)' },
      { label: 'ChatGPT', model: 'gpt-5', requests: share(0.11), cost: +(cost * 0.08).toFixed(2), lane: 'ChatGPT plan' },
      { label: 'Claude API', model: 'claude-sonnet-5-5', requests: share(0.03), cost: +(cost * 0.02).toFixed(2), lane: 'API key' },
    ],
    lanes: [{ lane: 'Claude plan (Pro/Max)', requests: share(0.86) }, { lane: 'ChatGPT plan', requests: share(0.11) },
            { lane: 'API key', requests: share(0.03) }],
    topActions: [{ name: 'search_files', count: share(0.42) }, { name: 'open_app', count: share(0.31) }, { name: 'open_url', count: share(0.24) },
                 { name: 'set_timer', count: share(0.12) }, { name: 'create_note', count: share(0.08) }],
  }
}

function demoUsage(): UsageState {
  const all = demoPeriod(42)
  return { periods: { '7': demoPeriod(7), '30': demoPeriod(30), all }, since: new Date(2026, 7, 22).getTime() / 1000,
           billed: +(all.cost * 0.02).toFixed(2) }
}

export function loadDemoSettings() {
  settings.set({ ...DEMO_SETTINGS, usage: demoUsage() })
}
