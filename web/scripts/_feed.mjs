import { chromium } from 'playwright'
const b = await chromium.launch()
for (const vp of [{n:'mobile',width:390,height:844},{n:'desktop',width:1280,height:900}]) {
  const p = await b.newPage({ viewport: {width:vp.width,height:vp.height}, deviceScaleFactor: 2 })
  const errs = []
  p.on('pageerror', e => errs.push('pageerror: '+e.message))
  p.on('console', m => { if (m.type()==='error') errs.push('console: '+m.text()) })
  await p.goto('http://localhost:5173/feed', { waitUntil:'networkidle' })
  await p.waitForTimeout(3000)
  const info = await p.evaluate(() => {
    const v = document.querySelector('video')
    return v ? { readyState:v.readyState, w:v.videoWidth, h:v.videoHeight,
                 dur:Math.round(v.duration*10)/10, paused:v.paused, muted:v.muted,
                 t:Math.round(v.currentTime*10)/10 } : 'no video'
  })
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  console.log(`${vp.n}:`, JSON.stringify(info), `| overflow=${overflow}px | errors=${errs.length}`)
  errs.slice(0,3).forEach(e=>console.log('   ', e.slice(0,120)))
  await p.screenshot({ path: `.smoke/feed-${vp.n}.png` })
  await p.close()
}
await b.close()
