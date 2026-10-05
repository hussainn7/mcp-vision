// Click through Plip's UI in a real browser and check the commands it sends the app.
// Usage: npm run build && npm run e2e
import assert from 'node:assert/strict'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const bundle = pathToFileURL(resolve(here, '../../src/mcp_vision/buddy/web/index.html')).href
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
const errors = []
let passed = 0

async function open(hash, viewport = { width: 1000, height: 700 }) {
  const page = await browser.newPage({ viewport })
  const sent = []
  page.on('pageerror', (error) => errors.push(`${hash}: ${error.message}`))
  page.on('console', async (message) => {
    if (message.type() === 'error') errors.push(`${hash}: ${message.text()}`)
    if (message.text().startsWith('[plip] command')) sent.push(await message.args()[1].jsonValue())
  })
  await page.goto(`${bundle}#${hash}`)
  await page.waitForTimeout(250)
  const seen = {}
  // The newest `cmd` sent since the previous take(); waits for it (console capture is async).
  const take = async (cmd) => {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const matches = sent.filter((item) => item.cmd === cmd)
      if (matches.length > (seen[cmd] ?? 0)) {
        seen[cmd] = matches.length
        return matches.at(-1)
      }
      await page.waitForTimeout(25)
    }
    return undefined
  }
  return { page, sent, take, last: (cmd) => [...sent].reverse().find((item) => item.cmd === cmd) }
}

async function test(name, fn) {
  try {
    await fn()
    passed += 1
    console.log(`ok   ${name}`)
  } catch (error) {
    console.log(`FAIL ${name}\n     ${error.message}`)
    process.exitCode = 1
  }
}

const NOTCH = { width: 196, height: 34, hasNotch: true }
const setIsland = (page, state) => page.evaluate((state) => window.__plip({ type: 'island', state }), { notch: NOTCH, ...state })

await test('island: confirm card sends yes and no', async () => {
  const { page, take, sent } = await open('island', { width: 760, height: 420 })
  await setIsland(page, { phase: 'answering', answer: 'I’ll text Sara.', done: true,
    confirm: { title: 'Send to Sara', lines: ['“On my way”'], confirm: 'Send' } })
  await page.waitForTimeout(500)
  assert.equal(await page.getByText('Send to Sara').count(), 1)
  await page.getByRole('button', { name: 'Send' }).click()
  assert.deepEqual((await take('confirm-action')), { cmd: 'confirm-action', accept: true })
  await page.getByRole('button', { name: 'Cancel' }).click()
  assert.deepEqual((await take('confirm-action')), { cmd: 'confirm-action', accept: false })
  assert.equal(sent.filter((item) => item.cmd === 'confirm-action').length, 2)
  await page.close()
})

await test('island: minimize tucks the answer back into the notch, click reopens', async () => {
  const { page, last } = await open('island', { width: 760, height: 420 })
  await setIsland(page, { phase: 'answering', answer: 'Opening Spotify for you now.', engine: { label: 'Claude', kind: 'subscription' } })
  await page.waitForTimeout(600)
  assert.equal(last('island-rect').mode, 'answering')
  await page.getByRole('button', { name: 'Minimize' }).click()
  await page.mouse.move(5, 400)                         // away from the island so hover doesn't peek
  await page.waitForTimeout(600)
  assert.equal(last('island-rect').mode, 'mini')
  assert.equal(last('island-rect').width, NOTCH.width + 96)
  await setIsland(page, { phase: 'listening', transcript: '' })     // a new question opens it again
  await page.waitForTimeout(400)
  assert.equal(last('island-rect').mode, 'listening')
  await page.close()
})

await test('island: plan checklist and stop button', async () => {
  const { page, take } = await open('island', { width: 760, height: 420 })
  await setIsland(page, { phase: 'answering', answer: 'Now open Settings.', plan: ['Profile menu', 'Settings', 'Security'], planIndex: 1 })
  await page.waitForTimeout(600)
  assert.equal(await page.locator('ol li').count(), 3)
  assert.equal(await page.getByText('now', { exact: true }).count(), 1)
  await page.getByRole('button', { name: 'Stop' }).click()
  assert.ok((await take('stop')))
  await page.close()
})

await test('island: file results open the file', async () => {
  const { page, take } = await open('island', { width: 760, height: 420 })
  await setIsland(page, { phase: 'answering', answer: 'Found it.', done: true,
    results: [{ title: 'Lease-2026.pdf', detail: '~/Documents', path: '/Users/you/Documents/Lease-2026.pdf' }] })
  await page.waitForTimeout(500)
  await page.getByText('Lease-2026.pdf').click()
  assert.deepEqual((await take('open-path')), { cmd: 'open-path', path: '/Users/you/Documents/Lease-2026.pdf' })
  await page.close()
})

await test('settings: memory paste, copy prompt, add and forget facts, import', async () => {
  const { page, take } = await open('settings?tab=memory')
  await page.getByRole('button', { name: 'Copy prompt' }).click()
  assert.ok((await take('memory-copy-prompt')))
  await page.getByRole('button', { name: 'Claude' }).click()
  await page.locator('textarea').fill('Name: Ada Lovelace\nLikes tea')
  await page.getByRole('button', { name: 'Import memory' }).click()
  assert.deepEqual((await take('memory-paste')), { cmd: 'memory-paste', source: 'claude', text: 'Name: Ada Lovelace\nLikes tea' })
  await page.getByPlaceholder('e.g. vegetarian, prefers aisle seats').fill('allergic to peanuts')
  await page.getByRole('button', { name: 'Add' }).click()
  assert.deepEqual((await take('memory-add')), { cmd: 'memory-add', key: 'note', value: 'allergic to peanuts' })
  await page.getByRole('button', { name: 'Forget' }).first().click({ force: true })
  assert.equal((await take('memory-delete')).id, 'f1')
  await page.getByRole('button', { name: 'Import' }).first().click()
  assert.equal((await take('memory-import')).source, 'mail')                // the first source not yet imported
  await page.close()
})

await test('settings: skills and companion style', async () => {
  const skills = await open('settings?tab=skills')
  await skills.page.getByRole('switch', { name: 'Travel' }).click()
  assert.deepEqual((await skills.take('set-skill')), { cmd: 'set-skill', skill: 'travel', enabled: false })
  await skills.page.getByRole('button', { name: 'Cursor', exact: true }).click()
  assert.deepEqual((await skills.take('set-companion')), { cmd: 'set-companion', style: 'cursor' })
  await skills.page.close()


})

await test('settings: usage tab switches periods and clears', async () => {
  const { page, take } = await open('settings?tab=usage')
  await page.getByText('How it ended').waitFor()
  assert.ok(await page.getByText('Requests per day').isVisible())
  assert.ok(await page.getByText('What your plan is worth').first().isVisible())
  const month = await page.locator('text=Requests').first().locator('xpath=..').textContent()
  await page.getByRole('button', { name: '7 days' }).click()
  assert.notEqual(await page.locator('text=Requests').first().locator('xpath=..').textContent(), month)
  for (const label of ['Finished', 'Needed you', "Couldn't confirm", "Didn't work", 'You stopped it']) {
    assert.ok(await page.locator('li', { hasText: label }).first().isVisible(), label)       // the legend rows
  }
  await page.getByRole('button', { name: 'Clear usage' }).click()
  assert.ok(await take('clear-usage'))
  await page.close()
})

await test('settings: voice picks parakeet, downloads with progress, cancels, removes', async () => {
  const { page, take } = await open('settings?tab=voice')
  await page.getByRole('button', { name: 'Parakeet' }).click()
  assert.deepEqual(await take('set-voice'), { cmd: 'set-voice', stt: 'parakeet' })
  await page.getByRole('button', { name: /Download 663 MB/ }).click()
  assert.ok(await take('parakeet-download'))
  await page.getByText(/MB of 663 MB/).waitFor()
  await page.getByRole('button', { name: 'Cancel' }).click()
  assert.ok(await take('parakeet-cancel'))
  await page.getByRole('button', { name: /Download 663 MB/ }).click()
  await page.getByText('Listening with Parakeet').waitFor({ timeout: 8000 })
  await page.getByRole('button', { name: /Remove/ }).click()
  assert.ok(await take('parakeet-remove'))
  await page.close()
})

await test('settings: every tab renders without errors', async () => {
  for (const tab of ['home', 'skills', 'memory', 'brain', 'voice', 'permissions', 'usage', 'history', 'about']) {
    const { page } = await open(`settings?tab=${tab}`)
    assert.ok((await page.locator('h1').first().textContent()).length > 3, tab)
    await page.close()
  }
})

await browser.close()
if (errors.length) {
  console.log('page errors:\n' + errors.join('\n'))
  process.exitCode = 1
}
console.log(`${passed} passed`)
