// Render every UI state from the built single-file bundle and save PNGs.
// Usage: npm run build && npm run shots [-- outDir]
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const bundle = pathToFileURL(resolve(here, '../../src/mcp_vision/buddy/web/index.html')).href
const out = resolve(process.argv[2] ?? resolve(here, '../shots'))
mkdirSync(out, { recursive: true })

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
const errors = []

async function shot(name, hash, { width, height, wait = 1400, setup } = {}) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 })
  page.on('pageerror', (error) => errors.push(`${name}: ${error.message}`))
  page.on('console', (message) => message.type() === 'error' && errors.push(`${name}: ${message.text()}`))
  await page.goto(`${bundle}#${hash}`)
  if (setup) await page.evaluate(setup)
  await page.waitForTimeout(wait)
  await page.screenshot({ path: resolve(out, `${name}.png`) })
  await page.close()
  console.log('saved', name)
}

for (const frame of ['idle', 'listening', 'thinking', 'answering', 'walkthrough', 'plan', 'results', 'confirm', 'mini', 'peek', 'error']) {
  await shot(`desktop-${frame}`, `showcase?frame=${frame}`, { width: 1440, height: 900, wait: 1800 })
}
for (const tab of ['home', 'skills', 'memory', 'routines', 'brain', 'voice', 'permissions', 'history', 'about']) {
  await shot(`settings-${tab}`, `settings?tab=${tab}`, { width: 980, height: 680 })
}

const islandStates = {
  listening: { phase: 'listening', level: 0.6, transcript: 'where is the export button' },
  thinking: { phase: 'thinking', transcript: 'where is the export button', steps: [
    { id: 'a', label: 'Looked at 1 screen', status: 'done', detail: '70ms' },
    { id: 'b', label: 'Asking Claude', status: 'active' }] },
  answering: { phase: 'answering', level: 0.5, answer: 'Top right of the toolbar, the square with an arrow. Click it and pick PDF.',
    engine: { label: 'ChatGPT', kind: 'subscription' }, latencyMs: 1810, done: false },
}
for (const [name, state] of Object.entries(islandStates)) {
  await shot(`island-${name}`, 'island', {
    width: 720, height: 300,
    setup: `document.body.style.background='#1b2140'; window.__plip({type:'island', state:${JSON.stringify({ ...state, notch: { width: 196, height: 34, hasNotch: true } })}})`,
  })
}
await shot('mascot-pointing', 'mascot', {
  width: 220, height: 80,
  setup: `document.body.style.background='#1b2140'; window.__plip({type:'mascot', state:{mood:'pointing', label:'Export button', look:{x:1,y:0.2}, lean:8}})`,
})

await browser.close()
if (errors.length) {
  console.error('page errors:\n' + errors.join('\n'))
  process.exit(1)
}
