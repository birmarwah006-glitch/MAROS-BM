/**
 * Browser smoke check.
 *
 * Loads each route at three viewports, captures console errors, page errors
 * and failed requests, and screenshots. Run against the dev server:
 *   node scripts/smoke.mjs [baseUrl]
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] ?? 'http://localhost:5173'
const OUT = process.env.SHOT_DIR ?? './.smoke'

const VIEWPORTS = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'tablet', width: 820, height: 1180 },
  { name: 'desktop', width: 1440, height: 900 },
]

const ROUTES = process.env.ROUTES
  ? process.env.ROUTES.split(',')
  : ['/', '/explore', '/add', '/learning', '/ask', '/profile']

mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
let failures = 0

for (const vp of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: 2,
  })

  for (const route of ROUTES) {
    const page = await context.newPage()
    const problems = []

    page.on('console', (msg) => {
      // A 404 on /jobs/* is documented control flow, not a defect: the backend
      // job store is in-memory, so both /jobs/{id} and /jobs/{id}/manifest 404
      // after a restart and the client falls through to the disk-backed
      // /modules/{id}. The browser logs every failed fetch regardless.
      if (/Failed to load resource/.test(msg.text()) && /\/jobs\//.test(page.url() + msg.location().url)) return
      if (msg.type() === 'error') problems.push(`console.error: ${msg.text()}`)
      if (msg.type() === 'warning' && /React|key|prop/i.test(msg.text()))
        problems.push(`console.warn: ${msg.text()}`)
    })
    page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`))
    page.on('requestfailed', (req) => {
      // Fonts from Google can fail in a sandbox; that is environmental.
      if (req.url().includes('fonts.g')) return
      // A <video> aborts its own range request whenever it pauses or is torn
      // down. Verified separately that these load (readyState 4), so an
      // ERR_ABORTED on a media URL is expected, not a defect.
      const err = req.failure()?.errorText ?? ''
      if (err.includes('ERR_ABORTED') && /\/(video|audio)$/.test(new URL(req.url()).pathname)) return
      problems.push(`requestfailed: ${req.url()} — ${err}`)
    })

    const slug = route === '/' ? 'root' : route.replace(/\//g, '_')
    try {
      const res = await page.goto(BASE + route, {
        waitUntil: 'networkidle',
        timeout: 20000,
      })
      if (!res || !res.ok()) problems.push(`HTTP ${res?.status()}`)
      await page.waitForTimeout(600)

      // Horizontal overflow is the classic responsive bug; catch it directly.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      if (overflow > 1) problems.push(`horizontal overflow: ${overflow}px`)

      await page.screenshot({
        path: `${OUT}/${vp.name}${slug}.png`,
        fullPage: vp.name === 'desktop',
      })
    } catch (err) {
      problems.push(`navigation: ${err.message}`)
    }

    const tag = `${vp.name.padEnd(7)} ${route.padEnd(12)}`
    if (problems.length) {
      failures += problems.length
      console.log(`FAIL ${tag}`)
      for (const p of problems) console.log(`       ${p}`)
    } else {
      console.log(`ok   ${tag}`)
    }
    await page.close()
  }
  await context.close()
}

await browser.close()
console.log(failures ? `\n${failures} problem(s)` : '\nclean')
process.exit(failures ? 1 : 0)
