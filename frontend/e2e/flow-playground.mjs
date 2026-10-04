// A customer asks for a price and bargains in the playground; the AI must answer, with typing, and the owner sees the deal.
import { chromium } from '@playwright/test'
import { session, seedStorage, base, CHROME } from './lib.mjs'
const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
await ctx.addInitScript(seedStorage, await session('owner'))
const page = await ctx.newPage()
page.on('pageerror', (e) => console.log('PAGEERROR', e.message))
await page.goto(base + '/app/playground', { waitUntil: 'load' })
await page.getByTitle('New random customer').click()
const input = page.getByPlaceholder('Message')
for (const msg of ['Hi, do you have banarasi silk sarees?', 'price of the red one?', 'thoda kam karo na']) {
  await input.fill(msg); await input.press('Enter')
  await page.waitForTimeout(9000)
}
await page.screenshot({ path: '/tmp/shots/playground-flow.png' })
await browser.close()
console.log('done')
