import { expect, test } from '@playwright/test'

test.describe('marketing site', () => {
  test('landing page tells the story and the example conversation plays', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/WhatsApp sales assistant/i)
    await expect(page.getByRole('heading', { level: 1 })).toContainText(/never below it/i)
    // the looping example conversation: messages appear one by one, and the owner alert pops up at the end
    const chat = page.locator('#hero-chat .chat-msg.show')
    await expect(chat.first()).toBeVisible()
    await expect(async () => expect(await chat.count()).toBeGreaterThanOrEqual(3)).toPass({ timeout: 20_000 })
    await expect(page.locator('#hero-alert')).toHaveCSS('opacity', '1', { timeout: 40_000 })
  })

  test('the price-limits demo steps down but never below the lowest price', async ({ page }) => {
    await page.goto('/#control')
    const demo = page.locator('#ladder-demo')
    await demo.scrollIntoViewIfNeeded()
    await demo.locator('#ld-list').fill('10000')
    await demo.locator('#ld-floor').fill('9000')
    await demo.locator('#ld-steps').fill('3')
    await demo.locator('#ld-ask').fill('2000')
    const path = demo.locator('#ld-path')
    await expect(path).toContainText('₹10,000')
    await expect(path).toContainText('₹9,000')
    const prices = (await path.innerText()).match(/₹[\d,]+/g)!.map((p) => +p.replace(/[₹,]/g, ''))
    expect(Math.min(...prices)).toBeGreaterThanOrEqual(9000)
    await expect(demo.locator('#ld-reply')).toContainText('₹9,000')           // asked for ₹2,000: it still holds at the floor
    await demo.locator('#ld-steps').fill('1')                                    // one step: straight to the floor
    await expect(path).not.toContainText('₹9,5')
  })

  test('language and theme toggles work and are remembered', async ({ page }) => {
    await page.goto('/')
    await page.locator('#lang-toggle').click()
    await expect(page.getByRole('heading', { level: 1 })).toContainText('हर ग्राहक')
    await page.reload()
    await expect(page.getByRole('heading', { level: 1 })).toContainText('हर ग्राहक')
    await page.locator('#lang-toggle').click()
    await expect(page.getByRole('heading', { level: 1 })).toContainText(/never below it/i)
    await page.locator('#theme-toggle').click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', /dark|light/)
  })

  test('legal pages exist and are linked from the footer (CR-9)', async ({ page }) => {
    await page.goto('/')
    for (const [link, heading] of [['Privacy policy', 'Privacy policy'], ['Terms of service', 'Terms of service'], ['Data deletion', 'Data deletion']] as const) {
      await page.locator('footer').getByRole('link', { name: link }).click()
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(heading)
      await page.goBack()
    }
  })

  test('the login link leads into the app', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: 'Log in' }).first().click()
    await expect(page).toHaveURL(/\/app\/login/)
    await expect(page.getByPlaceholder('98765 43210')).toBeVisible()
  })

  test('FAQ answers open and the product is never described as chatting with AI (CR-1)', async ({ page }) => {
    await page.goto('/#faq')
    await page.getByText('Will it give discounts I didn’t allow?').click()
    await expect(page.getByText(/calculator that follows your rules/i)).toBeVisible()
    const text = await page.locator('main').innerText()
    expect(text).not.toMatch(/chat with (an )?ai/i)
  })
})
