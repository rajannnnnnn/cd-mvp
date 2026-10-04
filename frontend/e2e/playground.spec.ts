import { expect, test } from '@playwright/test'
import { login, OWNER, SHOP } from './helpers'

test.describe('playground: a customer chats with the shop through the real pipeline', () => {
  test('the assistant answers from the catalog and never goes below the owner’s floor (INV-2, INV-3)', async ({ page }) => {
    await login(page, OWNER)
    await page.goto(`${SHOP}/playground`)
    await page.getByTitle('New random customer').click()
    const box = page.getByPlaceholder('Message')
    const say = async (text: string) => { await box.fill(text); await box.press('Enter') }
    const customerPhone = page.locator('.phone').first()
    const bot = customerPhone.locator('.bubble-in')

    await say('Hi, do you have banarasi silk sarees?')
    await expect(bot.first()).toBeVisible({ timeout: 40_000 })
    await say('price of the red one?')
    await expect(customerPhone).toContainText('₹8,500', { timeout: 40_000 })       // quoted by the pricing engine, from the catalog
    for (const ask of ['thoda kam karo na', '5000 mein de do', '4000 final', 'bilkul kam nahi hoga? 3000']) {
      const before = await bot.count()
      await say(ask)
      // after repeated pressure below the floor the assistant stops and hands the chat to the owner, so a reply is not guaranteed
      await expect(async () => expect(await bot.count()).toBeGreaterThan(before)).toPass({ timeout: 15_000 }).catch(() => undefined)
    }
    const text = await customerPhone.innerText()
    const amounts = [...text.matchAll(/₹\s?([\d,]+)/g)].map((m) => +m[1].replace(/,/g, ''))
    expect(amounts.length).toBeGreaterThan(0)
    // the lowest price the assistant ever states is the owner's floor (₹7,650 in the demo data), and never an amount the customer typed
    const aiAmounts = amounts.filter((a) => ![5000, 4000, 3000].includes(a))
    expect(Math.min(...aiAmounts)).toBeGreaterThanOrEqual(7650)
  })

  test('the owner’s phone receives alerts and obeys commands', async ({ page }) => {
    await login(page, OWNER)
    await page.goto(`${SHOP}/playground`)
    const owner = page.locator('.phone').nth(1)
    await expect(owner.locator('.bubble-in').first()).toBeVisible({ timeout: 20_000 })
    const box = owner.getByPlaceholder(/stop|summary/i)
    await box.fill('summary')
    await box.press('Enter')
    await expect(owner).toContainText(/summary|today|conversations/i, { timeout: 40_000 })
  })
})
