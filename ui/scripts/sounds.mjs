// Plip's UI sounds, synthesized from scratch (no samples), so they can be tuned here and re-rendered.
// They share the promo film's sonic logo: F major, a glassy FM bell, the wake chime's rising C6 -> F6.
//   node ui/scripts/sounds.mjs [out dir] [--alts]  -> src/mcp_vision/buddy/sounds/*.wav (48 kHz, stereo, 16-bit)
//
//   open-{1,2,3}  the notch pops open on hover: a water-drop "plip" landing on C6, a tiny bell on F6.
//                 Three takes a few cents apart so the most-heard sound doesn't machine-gun.
//   close         the island tucks back: one soft falling drop, quieter than open.
//   sent          keys let go, the question is tossed to the brain: an airy upward flick + a pip on A6.
//   done          a task really finished (sent, filled, moved, every step ticked): close's drop bouncing to
//                 rest, F5 A5 C6 F6, the tossed ball coming back.
// Levels are set by loudness (the loudest 100 ms), well under macOS's own Pop/Tink (about -21 to -25 dBFS):
// the more often a sound plays, the quieter it is.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const OUT = path.resolve(process.argv.slice(2).find((arg) => !arg.startsWith('--')) || path.join(ROOT, 'src/mcp_vision/buddy/sounds'))
const SR = 48000
const mtof = (m) => 440 * Math.pow(2, (m - 69) / 12)
const cents = (c) => Math.pow(2, c / 1200)

let seed = 7
const rnd = () => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return ((seed >>> 0) / 4294967296) * 2 - 1 }

const buf = (sec) => [new Float32Array(Math.ceil(sec * SR)), new Float32Array(Math.ceil(sec * SR))]

function add(out, t, mono, { gain = 1, pan = 0 } = {}) {
  const s0 = Math.round(t * SR)
  const pl = Math.cos(((pan + 1) * Math.PI) / 4) * Math.SQRT2, pr = Math.sin(((pan + 1) * Math.PI) / 4) * Math.SQRT2
  for (let i = 0; i < mono.length && s0 + i < out[0].length; i++) {
    out[0][s0 + i] += mono[i] * gain * pl
    out[1][s0 + i] += mono[i] * gain * pr
  }
}

// linear attack, exponential decay (tau = seconds to fall to 1/e), short fade at the very end
const len = (a, tau) => Math.ceil((a + tau * 7) * SR) // 7 tau = -60 dB, so nothing gets cut off audibly
const env = (n, a, tau) => (i) => {
  const t = i / SR
  const e = t < a ? t / a : Math.exp(-(t - a) / tau)
  const tail = Math.min(1, (n - i) / (SR * 0.004))
  return e * tail
}

// sine whose pitch glides f0 -> f1 over `glide` seconds (exponential curve, eased out), then holds
function drop({ f0, f1, glide, a = 0.0015, tau, h3 = 0 }) {
  const n = len(a, tau), out = new Float32Array(n), e = env(n, a, tau)
  let ph = 0
  for (let i = 0; i < n; i++) {
    const k = Math.min(1, i / SR / glide), eased = 1 - Math.pow(1 - k, 2)
    ph += (2 * Math.PI * f0 * Math.pow(f1 / f0, eased)) / SR
    out[i] = (Math.sin(ph) + h3 * Math.sin(3 * ph)) * e(i)
  }
  return out
}

// two-operator FM bell, the same voice as the film's wake and content chimes
function bell({ f, ratio = 3.5, index = 1, a = 0.002, tau }) {
  const n = len(a, tau), out = new Float32Array(n), e = env(n, a, tau)
  for (let i = 0; i < n; i++) {
    const t = i / SR
    const idx = index * Math.exp(-t / (tau * 0.45)) + 0.04
    out[i] = Math.sin(2 * Math.PI * f * t + idx * Math.sin(2 * Math.PI * f * ratio * t)) * e(i)
  }
  return out
}

// band-passed noise whose centre sweeps fc0 -> fc1 over `sweep` seconds (a flick of air)
function air({ fc0, fc1, sweep, q = 2, a, tau }) {
  const n = len(a, tau), out = new Float32Array(n), e = env(n, a, tau)
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0
  for (let i = 0; i < n; i++) {
    const fc = fc0 * Math.pow(fc1 / fc0, Math.min(1, i / SR / sweep))
    const w = (2 * Math.PI * fc) / SR, al = Math.sin(w) / (2 * q), a0 = 1 + al
    const b0 = al / a0, b2 = -al / a0, a1 = (-2 * Math.cos(w)) / a0, a2 = (1 - al) / a0
    const x = rnd(), y = b0 * x + b2 * x2 - a1 * y1 - a2 * y2
    x2 = x1; x1 = x; y2 = y1; y1 = y
    out[i] = y * e(i)
  }
  return out
}

// a small, bright room (Freeverb-style combs + allpasses) so every sound sits in the same space
function room([L, R], { wet = 0.12, size = 0.72, damp = 0.35 } = {}) {
  const combs = [1116, 1188, 1277, 1356], aps = [556, 441]
  const run = (x, spread) => {
    const y = new Float32Array(x.length)
    for (const d0 of combs) {
      const d = Math.round((d0 + spread) * SR / 44100 * 0.55), line = new Float32Array(d)
      let j = 0, lp = 0
      for (let i = 0; i < x.length; i++) {
        const o = line[j]
        lp = o * (1 - damp) + lp * damp
        line[j] = x[i] * 0.25 + lp * size
        y[i] += o
        j = (j + 1) % d
      }
    }
    for (const d0 of aps) {
      const d = Math.round((d0 + spread) * SR / 44100 * 0.55), line = new Float32Array(d)
      let j = 0
      for (let i = 0; i < y.length; i++) {
        const b = line[j], o = -y[i] + b
        line[j] = y[i] + b * 0.5
        y[i] = o
        j = (j + 1) % d
      }
    }
    return y
  }
  const wl = run(L, 0), wr = run(R, 23)
  for (let i = 0; i < L.length; i++) { L[i] += wl[i] * wet; R[i] += wr[i] * wet }
  return [L, R]
}

// loudness = RMS of the loudest 100 ms window; scale so it lands on `target` dBFS, keep peaks under -3
function level([L, R], target) {
  const win = SR / 10
  let best = 0
  for (let s = 0; s + win <= L.length; s += SR / 200) {
    let sum = 0
    for (let i = s; i < s + win; i++) sum += (L[i] * L[i] + R[i] * R[i]) / 2
    best = Math.max(best, Math.sqrt(sum / win))
  }
  let g = Math.pow(10, target / 20) / best
  let peak = 0
  for (let i = 0; i < L.length; i++) peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i]))
  g = Math.min(g, Math.pow(10, -3 / 20) / peak)
  for (let i = 0; i < L.length; i++) { L[i] *= g; R[i] *= g }
  return [L, R]
}

// cut the silent tail (below -66 dBFS) so the files stay tiny
function trim([L, R]) {
  let end = L.length
  while (end > 1 && Math.max(Math.abs(L[end - 1]), Math.abs(R[end - 1])) < 5e-4) end--
  end = Math.min(L.length, end + 64)
  return [L.slice(0, end), R.slice(0, end)]
}

function wav(file, [L, R]) {
  const n = L.length, b = Buffer.alloc(44 + n * 4)
  b.write('RIFF', 0); b.writeUInt32LE(36 + n * 4, 4); b.write('WAVE', 8)
  b.write('fmt ', 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(2, 22)
  b.writeUInt32LE(SR, 24); b.writeUInt32LE(SR * 4, 28); b.writeUInt16LE(4, 32); b.writeUInt16LE(16, 34)
  b.write('data', 36); b.writeUInt32LE(n * 4, 40)
  for (let i = 0; i < n; i++) {
    b.writeInt16LE(Math.round(Math.max(-1, Math.min(1, L[i])) * 32767), 44 + i * 4)
    b.writeInt16LE(Math.round(Math.max(-1, Math.min(1, R[i])) * 32767), 46 + i * 4)
  }
  fs.writeFileSync(path.join(OUT, file), b)
  return { file, ms: Math.round((n / SR) * 1000), kb: Math.round(b.length / 1024) }
}

const C6 = mtof(84), F6 = mtof(89), A6 = mtof(93), F5 = mtof(77)

function open(take) {
  const d = cents([0, 14, -12][take]), glide = [0.028, 0.024, 0.032][take], gap = [0.052, 0.046, 0.058][take]
  const out = buf(1.0)
  add(out, 0, drop({ f0: C6 * 0.55 * d, f1: C6 * d, glide, tau: 0.038, h3: 0.04 }))
  add(out, gap, bell({ f: F6 * d, ratio: 3.5, index: 0.9, tau: 0.11 }), { gain: 0.32, pan: 0.06 })
  return trim(level(room(out, { wet: 0.1 }), -31))
}

function close() {
  const out = buf(0.4)
  add(out, 0, drop({ f0: C6, f1: F5, glide: 0.05, a: 0.004, tau: 0.032 }))
  return trim(level(room(out, { wet: 0.09 }), -36))
}

function sent() {
  const out = buf(0.8)
  add(out, 0, air({ fc0: 520, fc1: 4200, sweep: 0.09, q: 3, a: 0.05, tau: 0.04 }), { gain: 0.9, pan: -0.05 })
  add(out, 0.08, bell({ f: A6, ratio: 3.5, index: 0.5, a: 0.0015, tau: 0.06 }), { gain: 0.22, pan: 0.05 })
  return trim(level(room(out, { wet: 0.12 }), -33))
}

// close's drop (a fifth falling onto its note), on any note: the one gesture done is built from
const fall = (land, { glide = 0.05, a = 0.004, tau = 0.032 } = {}) => drop({ f0: land * 1.5, f1: land, glide, a, tau })

// done: the tossed ball comes back. Close's drop lands, then bounces, each bounce quicker, quieter and higher,
// climbing F major (F5 A5 C6 F6) until it settles on F6, where every Plip sound comes home.
function done() {
  const out = buf(0.9)
  const bounces = [[0, F5, 1, 0.05, 0.034], [0.112, mtof(81), 0.62, 0.04, 0.03], [0.181, C6, 0.42, 0.032, 0.026],
    [0.224, F6, 0.3, 0.026, 0.05]]
  bounces.forEach(([t, land, gain, glide, tau], i) => add(out, t, fall(land, { glide, tau }), { gain, pan: (i - 1.5) * 0.06 }))
  return trim(level(room(out, { wet: 0.11 }), -30))
}

// two alternates, rendered with --alts for comparing (not shipped)
function donePair() {          // plip-plop: close's drop, then one a fifth up that lands a little louder
  const out = buf(0.6)
  add(out, 0, fall(F5))
  add(out, 0.095, fall(C6, { tau: 0.045 }), { gain: 1.15 })
  return trim(level(room(out, { wet: 0.1 }), -30))
}

function donePool() {          // a rounder drop into still water, then a ring of tiny droplets
  const out = buf(0.8)
  add(out, 0, fall(F5, { glide: 0.06, tau: 0.06 }))
  add(out, 0.004, drop({ f0: mtof(65) * 1.12, f1: mtof(65), glide: 0.03, a: 0.003, tau: 0.045 }), { gain: 0.3 })
  ;[[0.075, A6, 0.24, -0.25], [0.122, mtof(96), 0.16, 0.2], [0.161, F6 * 2, 0.1, -0.1]].forEach(([t, land, gain, pan]) =>
    add(out, t, fall(land, { glide: 0.022, a: 0.002, tau: 0.02 }), { gain, pan }))
  return trim(level(room(out, { wet: 0.16 }), -30))
}

fs.mkdirSync(OUT, { recursive: true })
const made = [
  wav('open-1.wav', open(0)), wav('open-2.wav', open(1)), wav('open-3.wav', open(2)),
  wav('close.wav', close()), wav('sent.wav', sent()), wav('done.wav', done()),
  ...(process.argv.includes('--alts') ? [wav('done-pair.wav', donePair()), wav('done-pool.wav', donePool())] : []),
]
for (const m of made) console.log(`${m.file.padEnd(12)} ${String(m.ms).padStart(5)} ms  ${m.kb} KB`)
console.log('->', path.relative(process.cwd(), OUT) || OUT)
