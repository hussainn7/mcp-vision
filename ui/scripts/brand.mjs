// Render the DMG window background (scripts/build_dmg.py) from HTML:
//   assets/brand/dmg/background.png      660x440, what Finder shows at 1x
//   assets/brand/dmg/background@2x.png   1320x880 for Retina (build_dmg joins both with tiffutil)
// Finder draws Plip at (180, 232) and Applications at (480, 232), 112 px icons, names
// underneath in dark text, so each name sits on a light frosted card.
// Usage: npm run brand
import { mkdirSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const out = resolve(here, '../../assets/brand/dmg')
mkdirSync(out, { recursive: true })

const geist = readFileSync(resolve(here, '../src/fonts/Geist-Variable.woff2')).toString('base64')
const icons = [180, 480]
const html = `<html><head><style>
@font-face { font-family: Geist; src: url(data:font/woff2;base64,${geist}) format('woff2'); font-weight: 100 900; }
* { margin: 0; box-sizing: border-box; }
body { width: 660px; height: 440px; overflow: hidden; position: relative; color: #fff;
  font-family: Geist, -apple-system, sans-serif; -webkit-font-smoothing: antialiased;
  background: radial-gradient(380px 260px at 330px 0px, rgba(95,142,244,0.22), transparent 70%),
              linear-gradient(180deg, #0f1428 0%, #070912 100%); }
.glow { position: absolute; top: 232px; width: 230px; height: 190px; transform: translate(-50%, -50%);
  background: radial-gradient(closest-side, rgba(95,142,244,0.42), rgba(95,142,244,0.10) 60%, transparent); }
.card { position: absolute; top: 289px; width: 128px; height: 26px; margin-left: -64px; border-radius: 9px;
  background: rgba(240,244,252,0.92); box-shadow: 0 8px 22px -10px rgba(0,0,0,0.8), inset 0 1px 0 #fff; }
h1 { position: absolute; top: 46px; width: 100%; text-align: center; font-size: 22px; font-weight: 620; letter-spacing: -0.02em; }
.arrow { position: absolute; left: 252px; top: 196px; }
.note { position: absolute; bottom: 26px; width: 100%; text-align: center; font-size: 11px; color: rgba(255,255,255,0.42); }
.note b { color: rgba(255,255,255,0.65); font-weight: 560; }
</style></head><body>
${icons.map((x) => `<div class="glow" style="left:${x}px"></div><div class="card" style="left:${x}px"></div>`).join('')}
<h1>Drag Plip into Applications</h1>
<svg class="arrow" width="156" height="64" viewBox="0 0 156 64">
  <defs><linearGradient id="a" x1="0" x2="1"><stop offset="0" stop-color="#8fb3fa" stop-opacity="0.3"/><stop offset="1" stop-color="#8fb3fa"/></linearGradient></defs>
  <path d="M8 44 C 48 8, 104 8, 140 38" fill="none" stroke="url(#a)" stroke-width="3.4" stroke-linecap="round"/>
  <path d="M126 28 L 141 39 L 123 44" fill="none" stroke="#8fb3fa" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
<div class="note">First open: if macOS says it can't check Plip, go to <b>System Settings → Privacy &amp; Security → Open Anyway</b>.</div>
</body></html>`

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
for (const [scale, name] of [[1, 'background.png'], [2, 'background@2x.png']]) {
  const page = await browser.newPage({ viewport: { width: 660, height: 440 }, deviceScaleFactor: scale })
  await page.setContent(html)
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: resolve(out, name) })
  await page.close()
  console.log('saved', name)
}
await browser.close()
