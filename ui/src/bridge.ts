/**
 * Two-way bridge between the UI and the Python engine.
 *
 * Native (macOS WKWebView): Python calls `window.__plip(message)`; the UI
 * calls `window.webkit.messageHandlers.plip.postMessage(command)`.
 * Browser preview: commands go to an in-page mock so every surface can be
 * developed, screenshotted, and demoed without a Mac.
 */
import { useSyncExternalStore } from 'react'

export type Phase = 'idle' | 'listening' | 'thinking' | 'answering' | 'error'
export type StepStatus = 'active' | 'done' | 'skipped' | 'error'

export interface Step {
  id: string
  label: string
  status: StepStatus
  detail?: string
}

export interface Walkthrough {
  index: number
  total: number
  label: string
}

export interface EngineBadge {
  label: string
  kind: 'subscription' | 'api' | 'local'
  model?: string
}

export interface ConfirmCard {
  title: string
  lines: string[]
  confirm: string
  name?: string
}

export interface ResultItem {
  title: string
  detail?: string
  path?: string
}

export interface IslandState {
  phase: Phase
  level: number
  transcript: string
  answer: string
  done: boolean
  speaking: boolean          // voice still playing (can outlast the text)
  finished: 'done' | 'bye' | null   // the request ended: a task done, or they said bye
  offer: string | null       // yes/no suggestion it ended on
  steps: Step[]
  walkthrough: Walkthrough | null
  engine: EngineBadge | null
  error: string
  fixable: boolean           // fixable in settings: shows "Fix setup"
  latencyMs: number | null
  notch: { width: number; height: number; hasNotch: boolean }
  idleVisible: boolean
  hovered: boolean
  plan: string[]
  planIndex: number
  confirm: ConfirmCard | null
  results: ResultItem[]
  minimized?: boolean       // demos only; the island minimizes itself from its chevron
}

export type Mood = 'idle' | 'listening' | 'thinking' | 'speaking' | 'pointing' | 'happy' | 'error'

export interface MascotState {
  mood: Mood
  level: number
  lean: number
  look: { x: number; y: number }
  label: string
}

export type EngineStatus = 'ready' | 'not-installed' | 'logged-out' | 'missing-key' | 'unknown' | 'unavailable'

export interface Engine {
  id: string
  label: string
  via: string
  kind: 'subscription' | 'api' | 'local'
  status: EngineStatus
  detail?: string
  install?: string
  login?: string
  keyName?: string
  selected?: boolean
  vision?: boolean
  connect?: ConnectProgress  // install + browser sign-in progress
}

export type TourStep = 'welcome' | 'permissions' | 'brain' | 'try' | 'done'

export interface LiveState {
  phase: 'idle' | 'listening' | 'thinking' | 'answering' | 'done' | 'error'
  transcript: string
  answer: string
  error: string
  at: number                 // epoch seconds this phase began
}

export interface KeyCheck {
  name: string               // e.g. GEMINI_API_KEY; '' if no key found
  state: 'checking' | 'ok' | 'bad'
  message: string
}

export interface ConnectProgress {
  state: 'installing' | 'signing-in' | 'ready' | 'failed'
  message: string
  url?: string               // sign-in page, if the browser didn't open
  needsCode?: boolean        // page shows a code to paste back
}

export interface HistoryItem {
  question: string
  answer: string
  at: number
  engine?: string
}

export interface FactCard {
  id: string
  key: string
  label: string
  value: string
  sensitive: boolean
  sources: string[]
}

export interface ImportStatus {
  count: number
  added?: number
  at: number
  error?: string
}

export interface MemoryPanel {
  profile: Record<string, string>
  facts: FactCard[]
  imports: Record<string, ImportStatus>
  contacts: { handle: string; count: number; name?: string }[]
  handles: string[]
}

/** The permission card docked to System Settings (see permission_guide.py). */
export interface GuideState {
  permission: string
  name: string
  app: string
  icon: string
  title: string
  hint: string
  draggable: boolean
  granted: boolean
  shown?: number
  panel: { width: number; height: number }
  row: { x: number; y: number; width: number; height: number }
}

export const defaultGuide: GuideState = {
  permission: 'accessibility',
  name: 'Accessibility',
  app: 'Plip',
  icon: '',
  title: 'Drag Plip into the list above',
  hint: 'That turns on Accessibility. Already listed? Just switch it on.',
  draggable: true,
  granted: false,
  panel: { width: 560, height: 150 },
  row: { x: 36, y: 56, width: 488, height: 44 },
}

/** NVIDIA Parakeet Unified 0.6B: an opt-in on-device recognizer, downloaded once. */
export interface ParakeetModel {
  state: 'missing' | 'downloading' | 'ready' | 'failed'
  done: number               // bytes so far, while downloading
  total: number              // bytes in all
  error: string
  runtime: boolean           // sherpa-onnx is installed
}

export type Outcome = 'done' | 'answered' | 'unverified' | 'paused' | 'waiting' | 'failed' | 'stopped'

export interface UsagePeriod {
  requests: number
  turns: number
  actions: number
  tasks: number
  tokensIn: number
  tokensOut: number
  cacheRead: number
  cost: number
  estimatedShare: number
  perDay: { day: string; requests: number; cost: number }[]
  perHour: number[]
  busiestHour: number | null
  outcomes: Record<Outcome, number>
  engines: { label: string; model: string; requests: number; cost: number; lane: string }[]
  lanes: { lane: string; requests: number }[]
  topActions: { name: string; count: number }[]
}

export interface UsageState {
  periods: Record<'7' | '30' | 'all', UsagePeriod>
  since: number | null
  billed: number
}

/**
 * The account (through Supabase): a guest after the walkthrough (a name like Quiet Nomad), Google when they're
 * ready. Never between the person and the hotkey. Builds without a Supabase project have no accounts at all.
 */
export interface AccountState {
  available: boolean         // this build has accounts
  identified: boolean        // signed in with Google: an email
  anonymous: boolean         // a guest: an account, no sign-in yet
  prompt: boolean            // Home shows the Google card (after the 1st and 3rd task, and once for an update)
  status?: '' | 'waiting' | 'failed'
  error?: string
  url?: string               // the sign-in page, while waiting
  user?: { name: string; email: string; provider: string; since: number | null; picture?: string } | null  // picture: their Google one, as a data: url
}

/** Hold-to-talk shortcut: keys (⌃⌥) and words. */
export interface Shortcut {
  id: string
  keys: string[]
  label: string
  works?: boolean            // false without Accessibility
}

/** General's shortcut picker: choice, options, whether it works. */
export interface HotkeyState extends Shortcut {
  works: boolean
  owner: string              // app needing Accessibility: Plip or its terminal
  choices: Shortcut[]
}

/** A newer Plip on GitHub (asked once a day, unless they turned it off). */
export interface UpdateState {
  enabled: boolean
  current: string
  available: { version: string; url: string; page: string } | null
}

export interface SettingsState {
  version: string
  engines: Engine[]
  depth: 'fast' | 'balanced' | 'deep'
  permissions: {
    screen: boolean | null; accessibility: boolean | null; microphone: boolean | null; speech: boolean | null
    restart?: boolean        // Screen Recording came on: macOS applies it after a restart
    guiding?: string         // the permission whose System Settings card is up
  }
  voice: {
    tts: 'elevenlabs' | 'say' | 'off'; stt: 'assemblyai' | 'apple' | 'parakeet'; elevenlabs: boolean; assemblyai: boolean
    parakeet?: ParakeetModel | null
  }
  jev: { configured: boolean; enabled: boolean; latencyMs: number | null }
  keys: Record<string, boolean>
  history: HistoryItem[]
  walkthroughs: boolean
  sounds: boolean
  memory: MemoryPanel | null
  companion: 'notch' | 'cursor' | 'hidden'
  stats: { actionsWeek: number; answers: number; minutesSaved: number }
  usage: UsageState | null
  onboarded: boolean
  tour: { step: TourStep }   // walkthrough step, survives restarts
  live: LiveState | null     // live request, for the "try it" step
  connect: string
  keyCheck?: KeyCheck | null  // pasted key's check with its provider
  report: '' | 'sent' | 'failed'     // a bug report or feature request, after Send
  account: AccountState
  update: UpdateState
  hotkey: HotkeyState
}

export const defaultIsland: IslandState = {
  phase: 'idle',
  level: 0,
  transcript: '',
  answer: '',
  done: false,
  speaking: false,
  offer: null,
  finished: null,
  steps: [],
  walkthrough: null,
  engine: null,
  error: '',
  fixable: false,
  latencyMs: null,
  notch: { width: 200, height: 32, hasNotch: true },
  idleVisible: true,
  hovered: false,
  plan: [],
  planIndex: 0,
  confirm: null,
  results: [],
}

export const defaultMascot: MascotState = { mood: 'idle', level: 0, lean: 0, look: { x: 0, y: 0 }, label: '' }

export const defaultSettings: SettingsState = {
  version: '0.9.0',
  engines: [],
  depth: 'balanced',
  permissions: { screen: null, accessibility: null, microphone: null, speech: null },
  voice: { tts: 'say', stt: 'apple', elevenlabs: false, assemblyai: false },
  jev: { configured: false, enabled: true, latencyMs: null },
  keys: {},
  history: [],
  walkthroughs: true,
  sounds: true,
  memory: null,
  companion: 'notch',
  stats: { actionsWeek: 0, answers: 0, minutesSaved: 0 },
  usage: null,
  onboarded: true,
  tour: { step: 'welcome' },
  live: null,
  connect: '',
  report: '',
  account: { available: false, identified: false, anonymous: false, prompt: false },
  update: { enabled: true, current: '0.9.0', available: null },
  hotkey: {
    id: 'control+option', keys: ['⌃', '⌥'], label: 'Control + Option', works: true, owner: 'Plip',
    choices: [
      { id: 'control+option', keys: ['⌃', '⌥'], label: 'Control + Option' },
      { id: 'option+command', keys: ['⌥', '⌘'], label: 'Option + Command' },
      { id: 'control+shift', keys: ['⌃', '⇧'], label: 'Control + Shift' },
      { id: 'control+command', keys: ['⌃', '⌘'], label: 'Control + Command' },
    ],
  },
}

export const defaultShortcut: Shortcut = { id: 'control+option', keys: ['⌃', '⌥'], label: 'Control + Option' }

// -- tiny external store ------------------------------------------------------

type Listener = () => void

export class Store<T extends object> {
  private value: T
  private listeners = new Set<Listener>()

  constructor(initial: T) {
    this.value = initial
  }

  get = () => this.value

  set = (patch: Partial<T> | ((current: T) => Partial<T>)) => {
    const next = typeof patch === 'function' ? patch(this.value) : patch
    this.value = { ...this.value, ...next }
    this.listeners.forEach((listener) => listener())
  }

  subscribe = (listener: Listener) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
}

export const island = new Store<IslandState>(defaultIsland)
export const mascot = new Store<MascotState>(defaultMascot)
export const settings = new Store<SettingsState>(defaultSettings)
export const guide = new Store<GuideState>(defaultGuide)
export const shortcut = new Store<Shortcut>(defaultShortcut)   // every "Hold ⌃⌥" follows it

export function useStore<T extends object>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get)
}

// -- messages from Python -----------------------------------------------------

export type Inbound =
  | { type: 'island'; state: Partial<IslandState> }
  | { type: 'mascot'; state: Partial<MascotState> }
  | { type: 'settings'; state: Partial<SettingsState> }
  | { type: 'guide'; state: Partial<GuideState> }
  | { type: 'shortcut'; state: Shortcut }
  | { type: 'append'; field: 'answer' | 'transcript'; text: string }
  | { type: 'step'; step: Step }
  | { type: 'reset' }

export function receive(message: Inbound) {
  switch (message.type) {
    case 'island':
      island.set(message.state)
      break
    case 'mascot':
      mascot.set(message.state)
      break
    case 'settings':
      settings.set(message.state)
      if (message.state.hotkey) {
        const { id, keys, label } = message.state.hotkey
        shortcut.set({ id, keys, label })
      }
      break
    case 'shortcut':
      shortcut.set(message.state)
      break
    case 'guide':
      guide.set(message.state)
      break
    case 'append':
      island.set((current) => ({ [message.field]: current[message.field] + message.text }))
      break
    case 'step':
      island.set((current) => {
        const steps = current.steps.filter((step) => step.id !== message.step.id)
        const index = current.steps.findIndex((step) => step.id === message.step.id)
        if (index >= 0) steps.splice(index, 0, message.step)
        else steps.push(message.step)
        return { steps }
      })
      break
    case 'reset':
      island.set({ ...defaultIsland, notch: island.get().notch, idleVisible: island.get().idleVisible, hovered: island.get().hovered })
      break
  }
}

declare global {
  interface Window {
    __plip?: (message: Inbound | Inbound[]) => void
    __PLIP_SURFACE__?: string
    webkit?: { messageHandlers?: { plip?: { postMessage: (body: unknown) => void } } }
  }
}

window.__plip = (message) => {
  for (const item of Array.isArray(message) ? message : [message]) receive(item)
}

export const isNative = () => Boolean(window.webkit?.messageHandlers?.plip)

type Command = { cmd: string; [key: string]: unknown }
const mockHandlers: Array<(command: Command) => void> = []

export function onMockCommand(handler: (command: Command) => void) {
  mockHandlers.push(handler)
}

export function send(cmd: string, payload: Record<string, unknown> = {}) {
  const body = { cmd, ...payload }
  const handler = window.webkit?.messageHandlers?.plip
  // A JSON string crosses the WebKit bridge as a plain NSString: no dictionary conversion surprises.
  if (handler) handler.postMessage(JSON.stringify(body))
  else mockHandlers.forEach((mock) => mock(body))
}
