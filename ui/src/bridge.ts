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
  steps: Step[]
  walkthrough: Walkthrough | null
  engine: EngineBadge | null
  error: string
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

export type EngineStatus = 'ready' | 'not-installed' | 'logged-out' | 'missing-key' | 'unknown'

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

export interface RoutineCard {
  id: string
  name: string
  phrase: string
  steps: string[]
  runs: number
  source: 'taught' | 'suggested'
}

export interface Suggestion {
  key: string
  name: string
  phrase: string
  labels: string[]
  days: number
  around: string
}

export interface SettingsState {
  version: string
  engines: Engine[]
  depth: 'fast' | 'balanced' | 'deep'
  permissions: { screen: boolean | null; accessibility: boolean | null; microphone: boolean | null; speech: boolean | null }
  voice: { tts: 'elevenlabs' | 'say' | 'off'; stt: 'assemblyai' | 'apple'; elevenlabs: boolean; assemblyai: boolean }
  jev: { configured: boolean; enabled: boolean; latencyMs: number | null }
  keys: Record<string, boolean>
  history: HistoryItem[]
  walkthroughs: boolean
  memory: MemoryPanel | null
  skills: Record<string, boolean>
  companion: 'notch' | 'cursor' | 'hidden'
  routines: RoutineCard[]
  suggestions: Suggestion[]
  stats: { actionsWeek: number; answers: number; minutesSaved: number }
}

export const defaultIsland: IslandState = {
  phase: 'idle',
  level: 0,
  transcript: '',
  answer: '',
  done: false,
  steps: [],
  walkthrough: null,
  engine: null,
  error: '',
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
  version: '0.6.0',
  engines: [],
  depth: 'balanced',
  permissions: { screen: null, accessibility: null, microphone: null, speech: null },
  voice: { tts: 'say', stt: 'apple', elevenlabs: false, assemblyai: false },
  jev: { configured: false, enabled: true, latencyMs: null },
  keys: {},
  history: [],
  walkthroughs: true,
  memory: null,
  skills: {},
  companion: 'notch',
  routines: [],
  suggestions: [],
  stats: { actionsWeek: 0, answers: 0, minutesSaved: 0 },
}

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

export function useStore<T extends object>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get)
}

// -- messages from Python -----------------------------------------------------

export type Inbound =
  | { type: 'island'; state: Partial<IslandState> }
  | { type: 'mascot'; state: Partial<MascotState> }
  | { type: 'settings'; state: Partial<SettingsState> }
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
