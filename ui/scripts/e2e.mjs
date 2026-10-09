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

await test('island: stays open while Plip is still talking, opens on hover only after a beat', async () => {
  const { page, last, take } = await open('island', { width: 760, height: 420 })
  await page.mouse.move(5, 400)
  await setIsland(page, { phase: 'thinking', transcript: 'what is this' })
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: 'Stop' }).click()                    // stop while it's still thinking
  assert.deepEqual(await take('stop'), { cmd: 'stop' })
  await page.mouse.move(5, 400)                                               // off the island, or it stays for the hover
  await setIsland(page, { phase: 'answering', answer: 'A long answer.', done: true, speaking: true })
  await page.waitForTimeout(500)                                              // the thinking view finishes leaving
  await page.clock.install()
  await page.clock.runFor(12000)                                              // past the linger: still talking
  assert.equal(last('island-rect').mode, 'answering')
  assert.equal(await page.getByRole('button', { name: 'Stop' }).count(), 1)   // the voice can still be stopped
  await setIsland(page, { speaking: false })
  await page.clock.runFor(7500)
  await page.waitForTimeout(50)
  assert.notEqual(last('island-rect').mode, 'answering')                      // done talking: tucked away
  await setIsland(page, { phase: 'idle', hovered: true })
  await page.clock.runFor(150)
  assert.notEqual(last('island-rect').mode, 'peek')                           // passing by doesn't open it
  await page.clock.runFor(400)
  await page.waitForTimeout(50)
  assert.equal(last('island-rect').mode, 'peek')
  await page.close()
})

await test('island: a yes-or-no suggestion gets Yes and No thanks', async () => {
  const { page, take } = await open('island', { width: 760, height: 420 })
  await setIsland(page, { phase: 'answering', answer: 'Found three. Want me to add the cheapest to your cart?', done: true,
    offer: 'Want me to add the cheapest to your cart?' })
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: 'Yes' }).click()
  assert.deepEqual(await take('offer-answer'), { cmd: 'offer-answer', accept: true })
  assert.equal(await page.getByRole('button', { name: 'Yes' }).count(), 0)          // answered: the buttons go
  await setIsland(page, { offer: 'Want me to keep going?' })
  await page.getByRole('button', { name: 'No thanks' }).click()
  assert.deepEqual(await take('offer-answer'), { cmd: 'offer-answer', accept: false })
  await page.close()
})

await test('settings: one click connects a brain (no Terminal, nothing to copy)', async () => {
  const { page, take } = await open('settings?tab=brain')
  assert.equal(await page.getByText('Copy install command').count(), 0)
  await page.getByRole('button', { name: 'Connect ChatGPT' }).click()
  assert.equal((await take('engine-connect')).id, 'codex')
  await page.getByText('Finish signing in to ChatGPT in your browser').waitFor()
  await page.getByText('Browser didn’t open?').click()
  await page.getByText('Open the sign-in page').waitFor()
  await page.getByPlaceholder('If the page shows a code, paste it here').fill('abc#123')
  await page.getByRole('button', { name: 'Done' }).click()
  assert.deepEqual(await take('engine-connect-code'), { cmd: 'engine-connect-code', id: 'codex', code: 'abc#123' })
  await page.getByText('Finish signing in to ChatGPT in your browser').waitFor({ state: 'detached', timeout: 6000 })
  assert.equal(await page.getByRole('button', { name: 'Connect ChatGPT' }).count(), 0)
  await page.getByRole('button', { name: 'Connect Cursor' }).click()                // not installed: installs first
  await page.getByText('Installing Cursor’s app').waitFor()
  await page.getByRole('button', { name: 'Cancel' }).click()
  assert.deepEqual(await take('engine-connect-cancel'), { cmd: 'engine-connect-cancel', id: 'cursor' })
  await page.close()
})

await test('settings: no AI plan? a free Google key in three steps, checked before it counts', async () => {
  const { page, take } = await open('settings?tab=brain')
  await page.evaluate(() => window.__plip({ type: 'settings', state: { engines: [
    { id: 'claude-code', label: 'Claude', via: 'Claude Pro / Max via Claude Code', kind: 'subscription', status: 'not-installed' },
    { id: 'codex', label: 'ChatGPT', via: 'ChatGPT Plus / Pro via Codex CLI', kind: 'subscription', status: 'not-installed' },
    { id: 'anthropic', label: 'Claude API', via: 'Anthropic API key', kind: 'api', status: 'missing-key', keyName: 'ANTHROPIC_API_KEY' },
    { id: 'gemini-api', label: 'Gemini', via: 'Free key from Google AI Studio', kind: 'api', status: 'missing-key', keyName: 'GEMINI_API_KEY' },
  ] } }))
  await page.getByText('If you pay for one of these').waitFor()
  await page.getByText('No AI plan? Use Google’s for free').waitFor()
  await page.getByRole('button', { name: 'Get my free key' }).click()
  assert.deepEqual(await take('open-url'), { cmd: 'open-url', url: 'https://aistudio.google.com/apikey' })
  await page.getByRole('button', { name: 'Paste key' }).click()
  assert.deepEqual(await take('paste-key'), { cmd: 'paste-key' })
  await page.getByText('Checking the key with Google').first().waitFor()
  await page.getByText('Plip thinks with Gemini').waitFor({ timeout: 4000 })        // checked, saved, in use
  await page.close()
})

await test('island: only a setup error offers Fix setup', async () => {
  const { page, take } = await open('island', { width: 760, height: 420 })
  await setIsland(page, { phase: 'error', error: 'I didn’t catch that. Hold ⌃⌥ and try again.', fixable: false })
  await page.waitForTimeout(400)
  assert.equal(await page.getByRole('button', { name: 'Fix setup' }).count(), 0)
  await setIsland(page, { phase: 'error', error: 'I need you to sign in to my brain first. Open my settings and pick a brain.', fixable: true })
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: 'Fix setup' }).click()
  assert.deepEqual(await take('open-settings'), { cmd: 'open-settings', tab: 'brain' })
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

await test('settings: general (companion style, walkthroughs, tour)', async () => {
  const { page, take } = await open('settings?tab=general')
  await page.getByRole('button', { name: 'Cursor', exact: true }).click()
  assert.deepEqual((await take('set-companion')), { cmd: 'set-companion', style: 'cursor' })
  await page.getByRole('switch', { name: 'Guided walkthroughs' }).click()
  assert.deepEqual((await take('set-walkthroughs')), { cmd: 'set-walkthroughs', enabled: false })
  await page.getByRole('button', { name: /Replay the welcome tour/ }).click()
  assert.ok(await take('tour-start'))
  await page.getByText('Hi, I’m Plip').waitFor()
  await page.close()
})

await test('onboarding: plain steps, a brain without a terminal, practice, kept across a restart', async () => {
  const { page, take } = await open('settings?tab=home')
  await page.evaluate(() => window.__plip({ type: 'settings', state: { onboarded: false, tour: { step: 'welcome' },
    permissions: { screen: true, accessibility: null, microphone: true, speech: null }, voice: { tts: 'say', stt: 'apple', elevenlabs: false, assemblyai: false },
    engines: [
      { id: 'claude-code', label: 'Claude', via: 'Claude Pro / Max via Claude Code', kind: 'subscription', status: 'not-installed' },
      { id: 'codex', label: 'ChatGPT', via: 'ChatGPT Plus / Pro via Codex CLI', kind: 'subscription', status: 'logged-out' },
      { id: 'gemini-api', label: 'Gemini', via: 'Free key from Google AI Studio', kind: 'api', status: 'missing-key', keyName: 'GEMINI_API_KEY' },
    ] } }))
  await page.getByText('Hi, I’m Plip').waitFor()
  await page.getByRole('button', { name: 'Set me up' }).click()
  assert.deepEqual(await take('tour-go'), { cmd: 'tour-go', step: 'permissions' })    // kept: a restart comes back here
  await page.getByText('Let Plip see and hear you').waitFor()
  await page.getByText('Microphone and Speech Recognition').waitFor()               // Apple's listening needs both
  await page.getByRole('button', { name: 'Allow', exact: true }).first().click()
  assert.deepEqual(await take('grant'), { cmd: 'grant', permission: 'accessibility' })
  await page.getByRole('button', { name: 'Skip for now' }).click()
  await page.getByText('Give Plip a brain').waitFor()
  assert.equal(await page.getByText(/terminal|npm i/i).count(), 0)                   // no commands to run, anywhere
  await page.getByRole('button', { name: 'Connect', exact: true }).first().click()
  assert.deepEqual(await take('engine-connect'), { cmd: 'engine-connect', id: 'claude-code' })
  await page.getByText('No AI plan? Use Google’s for free').waitFor()
  await page.getByRole('button', { name: 'Paste key' }).click()
  await page.getByText('Hold Control + Option and ask').waitFor({ timeout: 8000 })   // a working brain moves it on
  await page.evaluate(() => window.__plip({ type: 'settings', state: { live: { phase: 'done', transcript: 'what can you do',
    answer: 'I can see your screen and point at things.', error: '', at: Date.now() / 1000 + 5 } } }))
  await page.getByText('That’s all there is to it').waitFor()
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByText('You’re all set').waitFor()
  await page.getByRole('button', { name: 'Start using Plip' }).click()
  assert.ok(await take('finish-onboarding'))
  await page.close()
})

await test('general: the talk shortcut is picked here, and every "hold" hint follows it', async () => {
  const { page, take } = await open('settings?tab=general')
  await page.getByText('Hold Control + Option, talk, then let go.').waitFor()
  assert.equal(await page.getByRole('alert').count(), 0)
  await page.getByRole('button', { name: 'Option + Command' }).click()
  assert.deepEqual(await take('set-hotkey'), { cmd: 'set-hotkey', id: 'option+command' })
  // What Python pushes back: the new shortcut, and that the terminal Plip runs in can't see keys yet.
  await page.evaluate(() => window.__plip({ type: 'settings', state: { hotkey: {
    id: 'option+command', keys: ['⌥', '⌘'], label: 'Option + Command', works: false, owner: 'Terminal',
    choices: [{ id: 'control+option', keys: ['⌃', '⌥'], label: 'Control + Option' }, { id: 'option+command', keys: ['⌥', '⌘'], label: 'Option + Command' }] } } }))
  await page.getByText('Hold Option + Command, talk, then let go.').waitFor()
  const alert = page.getByRole('alert')
  assert.match(await alert.innerText(), /macOS isn’t passing your keys to Terminal, so ⌥⌘ does nothing yet/)
  await alert.getByRole('button', { name: 'Open Accessibility' }).click()
  assert.deepEqual(await take('grant'), { cmd: 'grant', permission: 'accessibility' })     // the command the app handles
  assert.deepEqual(await page.locator('aside kbd').allInnerTexts(), ['⌥', '⌘'])        // the sidebar's hint
  await page.close()
  const island = await open('island', { width: 760, height: 420 })
  await island.page.evaluate(() => window.__plip({ type: 'shortcut', state: { id: 'control+shift', keys: ['⌃', '⇧'], label: 'Control + Shift' } }))
  await setIsland(island.page, { phase: 'idle', hovered: true })
  await island.page.getByText('and ask, or tell me to do something').waitFor()
  assert.deepEqual(await island.page.locator('kbd').allInnerTexts(), ['⌃', '⇧'])
  await island.page.close()
})

await test('general: report a bug and request a feature, one open at a time', async () => {
  const { page, take } = await open('settings?tab=report')                 // the menu bar's "Report a bug…"
  await page.getByLabel('What went wrong').fill('It pointed at the wrong button')
  await page.getByRole('button', { name: 'Send report' }).click()
  assert.deepEqual(await take('report-issue'), { cmd: 'report-issue', message: 'It pointed at the wrong button' })
  await page.getByText('Sent. Thank you').waitFor()
  await page.getByRole('button', { name: /Request a feature/ }).click()
  assert.ok(await take('report-reset'))
  await page.getByRole('button', { name: 'Report another' }).waitFor({ state: 'detached' })   // one open at a time
  await page.getByLabel('What should Plip do').fill('Read my calendar out loud every morning')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  assert.deepEqual(await take('request-feature'), { cmd: 'request-feature', message: 'Read my calendar out loud every morning' })
  await page.getByText('Sent. Thank you').waitFor()
  await page.close()
})

await test('settings: old links land on the new tabs', async () => {
  for (const [tab, heading] of [['about', 'How Plip behaves on your Mac'], ['skills', 'How Plip behaves on your Mac'],
    ['usage', 'What you asked, and what it took'], ['history', 'What you asked, and what it took']]) {
    const { page } = await open(`settings?tab=${tab}`)
    assert.equal(await page.locator('h1').first().textContent(), heading, tab)
    if (tab === 'history') await page.getByText('Your recent questions').waitFor()
    await page.close()
  }
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

await test('guide: the permission card says what to drag, closes, and shows the check', async () => {
  const { page, take } = await open('guide', { width: 560, height: 150 })
  await page.getByText('Drag Plip into the list above').waitFor()
  assert.ok(await page.getByText('drag me up').isVisible())
  await page.getByRole('button', { name: 'Close' }).click()
  assert.ok(await take('guide-close'))
  await page.evaluate(() => window.__plip({ type: 'guide', state: { granted: true } }))
  await page.getByText('Accessibility is on').waitFor()
  await page.close()
})

await test('sign in: google comes first, then plip; sign out brings it back', async () => {
  const { page, take } = await open('settings?signin')
  assert.equal(await page.locator('nav').count(), 0)                     // nothing else until they sign in
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  assert.deepEqual(await take('account-sign-in'), { cmd: 'account-sign-in', provider: 'google' })
  await page.getByText('Finish signing in in your browser').waitFor()
  await page.getByRole('button', { name: 'Open the page again' }).click()
  assert.ok(await take('account-open'))
  await page.getByRole('button', { name: 'Cancel' }).click()
  assert.ok(await take('account-cancel'))
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  const account = page.locator('nav').getByRole('button', { name: /Account$/ })
  await account.waitFor({ timeout: 4000 })                                // the preview "comes back" signed in
  await account.click()
  await page.getByText('hussain@plip.dev').waitFor()
  await page.getByRole('button', { name: 'Sign out' }).click()
  assert.ok(await take('account-sign-out'))
  await page.getByRole('button', { name: 'Continue with Google' }).waitFor()
  await page.close()
})

await test('sign in: a failed sign-in says why, and a build without sign-in never shows it', async () => {
  const { page } = await open('settings?signin')
  await page.evaluate(() => window.__plip({ type: 'settings', state: { account: { available: true, required: true, status: 'failed', error: 'You said no' } } }))
  await page.getByRole('alert').getByText('You said no').waitFor()
  await page.getByRole('button', { name: 'Try again with Google' }).waitFor()
  await page.evaluate(() => window.__plip({ type: 'settings', state: { account: { available: false, required: false } } }))
  await page.locator('nav').waitFor()
  assert.equal(await page.locator('nav').getByRole('button', { name: /Account$/ }).count(), 0)
  await page.close()
})

await test('update: a newer plip shows on home and general, download and the switch send their commands', async () => {
  const { page, take } = await open('settings?tab=home')
  assert.equal(await page.getByText(/is out$/).count(), 0)                // nothing new: no banner
  await page.evaluate(() => window.__plip({ type: 'settings', state: { update: { enabled: true, current: '0.8.0',
    available: { version: '0.9.0', url: 'https://github.com/hussainn7/plip-oss/releases/download/v0.9.0/Plip-0.9.0.dmg',
      page: 'https://github.com/hussainn7/plip-oss/releases/tag/v0.9.0' } } } }))
  await page.getByText('Plip 0.9.0 is out').waitFor()
  await page.getByRole('button', { name: 'Download', exact: true }).click()
  assert.ok(await take('update-download'))
  await page.getByRole('button', { name: 'What’s new' }).click()
  assert.deepEqual(await take('open-url'), { cmd: 'open-url', url: 'https://github.com/hussainn7/plip-oss/releases/tag/v0.9.0' })
  await page.locator('nav').getByRole('button', { name: 'General' }).click()
  await page.getByRole('button', { name: 'Download 0.9.0' }).click()
  assert.ok(await take('update-download'))
  await page.getByRole('switch', { name: 'Tell me about new versions' }).click()
  assert.deepEqual(await take('set-update-check'), { cmd: 'set-update-check', enabled: false })
  await page.getByRole('button', { name: /GitHub/ }).waitFor()               // off: back to the plain about row
  await page.close()
})

await test('settings: every tab renders without errors', async () => {
  for (const tab of ['home', 'account', 'general', 'brain', 'voice', 'permissions', 'memory', 'activity']) {
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
