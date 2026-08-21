/**
 * Headless Meal renderer.
 *
 * Boots the Vite dev server, loads the Motion Canvas project in headless
 * Chromium, drives `Renderer` through the page, streams each rendered frame
 * back out as a PNG, and pipes the sequence straight into FFmpeg together with
 * the narration track.
 *
 * Frames are piped rather than written to disk: a 40-second Meal is ~1200
 * frames at 1080x1920, which is a lot of pointless I/O.
 *
 *   node render.mjs ../meals/catalogue/meal_input_output.json
 *
 * Requires the timing sidecar from meals/narrate.py to exist alongside the
 * audio in meals/build/.
 */

import { chromium } from 'playwright'
import { createServer } from 'vite'
import { spawn } from 'node:child_process'
import { readFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, resolve, basename } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const MEALS_DIR = resolve(HERE, '..', 'meals')
const BUILD_DIR = resolve(MEALS_DIR, 'build')
const OUT_DIR = resolve(MEALS_DIR, 'out')

const mealPath = process.argv[2]
if (!mealPath) {
  console.error('usage: node render.mjs <meal.json>')
  process.exit(1)
}

const meal = JSON.parse(readFileSync(resolve(mealPath), 'utf8')).meal
const timingPath = resolve(BUILD_DIR, `${meal.id}.timing.json`)
const audioPath = resolve(BUILD_DIR, `${meal.id}.mp3`)

for (const [label, p] of [['timing', timingPath], ['audio', audioPath]]) {
  if (!existsSync(p)) {
    console.error(`Missing ${label}: ${p}\nRun:  python3 meals/narrate.py ${mealPath}`)
    process.exit(1)
  }
}

const timing = JSON.parse(readFileSync(timingPath, 'utf8'))
mkdirSync(OUT_DIR, { recursive: true })

const fps = meal.render?.fps ?? 30
const width = meal.render?.width ?? 1080
const height = meal.render?.height ?? 1920
const outPath = resolve(OUT_DIR, `${meal.id}.mp4`)

console.log(`${meal.id}: ${timing.duration}s, ${fps}fps, ${width}x${height}`)
console.log(`  alignment: ${timing.alignment}, ${Object.keys(timing.anchors).length} anchors`)

// ── FFmpeg: PNG sequence on stdin + the narration track -> MP4 ──────────
const ffmpeg = spawn('ffmpeg', [
  '-y',
  '-f', 'image2pipe',
  '-framerate', String(fps),
  '-i', 'pipe:0',
  '-i', audioPath,
  '-c:v', 'libx264',
  '-preset', 'medium',
  '-crf', '19',
  '-pix_fmt', 'yuv420p',
  '-c:a', 'aac',
  '-b:a', '192k',
  // The visual runs slightly past the narration so the last beat can land;
  // -shortest would clip that tail, so the audio is padded instead.
  '-af', 'apad',
  '-shortest',
  '-movflags', '+faststart',
  outPath,
], { stdio: ['pipe', 'ignore', 'pipe'] })

let ffmpegErr = ''
ffmpeg.stderr.on('data', (d) => { ffmpegErr += d.toString() })

const ffmpegDone = new Promise((res, rej) => {
  ffmpeg.on('close', (code) => (code === 0 ? res() : rej(new Error(`ffmpeg exited ${code}\n${ffmpegErr.slice(-2000)}`))))
})

// Backpressure: if FFmpeg's input buffer is full, wait before sending more.
function writeFrame(buf) {
  return new Promise((res) => {
    if (!ffmpeg.stdin.write(buf)) ffmpeg.stdin.once('drain', res)
    else res()
  })
}

// ── Vite + headless Chromium ────────────────────────────────────────────
// force:true busts Vite's dependency cache. Without it an edited scene can
// render from a stale module and the output silently does not change — which
// is very hard to spot when you are looking at a video.
const server = await createServer({
  root: HERE,
  server: { port: 9000 },
  optimizeDeps: { force: true },
  logLevel: 'warn',
})
await server.listen()
const port = server.config.server.port

const browser = await chromium.launch({
  args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'],
})
const page = await browser.newPage({
  viewport: { width: 600, height: 900 },
})

// The Motion Canvas project bootstraps a Player as soon as its module
// evaluates, which runs the scene generator immediately. So the Meal data has
// to exist BEFORE any module runs, not when renderMeal() is called.
await page.addInitScript(
  ([m, t]) => { window.__MEAL__ = { meal: m, timing: t } },
  [meal, timing],
)

const pageErrors = []
page.on('pageerror', (e) => pageErrors.push(e.message))
page.on('console', (m) => {
  if (m.type() === 'error') pageErrors.push(m.text())
})

let received = 0
let expected = 0

await page.exposeFunction('emitFrame', async (frame, dataUrl) => {
  const buf = Buffer.from(dataUrl.split(',')[1], 'base64')
  await writeFrame(buf)
  received++
  if (received % 60 === 0) {
    const pct = expected ? ` (${Math.round((received / expected) * 100)}%)` : ''
    process.stdout.write(`\r  frames: ${received}${pct}`)
  }
})

await page.exposeFunction('onRenderProgress', (_frame, total) => { expected = total })

// The Motion Canvas plugin serves its editor UI at `/`, so the headless
// render page lives at its own URL.
await page.goto(`http://localhost:${port}/render.html`, { waitUntil: 'networkidle' })
// Web fonts must be resolved before the first frame, or early frames render
// in a fallback face and the type visibly shifts mid-video.
await page.evaluate(() => document.fonts.ready)

const started = Date.now()
try {
  await page.waitForFunction(() => typeof window.renderMeal === 'function')
  await page.evaluate(
    ([m, t]) => window.renderMeal(m, t),
    [meal, timing],
  )
} catch (err) {
  console.error('\nrender failed inside the page:')
  console.error(err.message)
  if (pageErrors.length) console.error(pageErrors.slice(0, 5).join('\n'))
  await browser.close(); await server.close(); ffmpeg.stdin.end()
  process.exit(1)
}

process.stdout.write(`\r  frames: ${received}          \n`)
ffmpeg.stdin.end()
await browser.close()
await server.close()
await ffmpegDone

const secs = ((Date.now() - started) / 1000).toFixed(1)
console.log(`  rendered in ${secs}s -> meals/out/${basename(outPath)}`)

if (pageErrors.length) {
  console.log(`  note: ${pageErrors.length} console error(s) during render`)
  pageErrors.slice(0, 3).forEach((e) => console.log(`    ${e.slice(0, 140)}`))
}
