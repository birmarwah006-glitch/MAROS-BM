import { chromium } from 'playwright'
const J = 'fcece514-f209-49df-8094-9a7787037683'
const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: 1400, height: 1000 } })
const errs = []
p.on('pageerror', e => errs.push('pageerror: ' + e.message))
p.on('console', m => { if (m.type()==='error' && !/Failed to load resource/.test(m.text())) errs.push('console: '+m.text()) })

await p.goto(`http://localhost:5173/lecture/${J}/module/3`, { waitUntil: 'networkidle' })

// ── Quiz flow ──
console.log('--- quiz ---')
await p.getByRole('button', { name: 'Start the quiz' }).click()
await p.waitForSelector('text=/QUESTIONS/', { timeout: 90000 })
console.log('quiz generated')
const qCount = await p.locator('[data-testid=quiz-question]').count()
console.log('questions rendered:', qCount)
// answer every question by clicking the first option of each
for (let i = 0; i < qCount; i++) {
  await p.locator('[data-testid=quiz-question]').nth(i).locator('button').first().click()
}
const submit = p.getByRole('button', { name: 'Submit answers' })
console.log('submit enabled:', await submit.isEnabled())
await submit.click()
await p.waitForSelector('[data-testid=quiz-score]', { timeout: 120000 })
console.log('graded:', await p.locator('[data-testid=quiz-score]').innerText())
console.log('explanations shown:', await p.locator('[data-testid=quiz-question]').locator('text=Why').count())

// ── Tutor flow ──
console.log('--- tutor ---')
await p.getByRole('button', { name: 'Give me an analogy' }).click()
await p.waitForSelector('[data-testid=tutor] >> text=MAROS', { timeout: 120000 })
await p.waitForTimeout(1500)
const tutor = await p.locator('[data-testid=tutor]').innerText()
console.log('tutor replied:', tutor.length > 300 ? 'yes ('+tutor.length+' chars)' : 'SHORT: '+tutor.slice(0,200))

console.log('--- errors ---')
console.log(errs.length ? errs.join('\n') : 'none')
await p.screenshot({ path: '.smoke/flow-quiz.png', fullPage: false })
await b.close()
