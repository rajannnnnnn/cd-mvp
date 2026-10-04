import { expect, test } from '@playwright/test'
import { login, OWNER } from './helpers'

/** INV-1 in the browser: the lowest price is typed once and never comes back, in the UI or in any API response. */
test.describe('floor prices stay private', () => {
  test.beforeEach(async ({ page }) => { await login(page, OWNER) })

  test('the catalog editor never shows a stored floor and no API response carries one', async ({ page }) => {
    const bodies: string[] = []
    page.on('response', async (r) => { if (r.url().includes('/api/v1/') && r.headers()['content-type']?.includes('json')) bodies.push(await r.text().catch(() => '')) })
    await page.goto('/app/catalog')
    await page.getByText('Banarasi Silk Saree').first().click()
    await expect(page.getByText('Saved privately').first()).toBeVisible()
    await expect(page.getByText('Edit product')).toBeVisible()
    const dialog = page.locator('[role=dialog]')
    const visible = await dialog.innerText()
    const values = await dialog.locator('input, textarea, select').evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value).join(' '))
    expect(`${visible} ${values}`).not.toMatch(/7650|7,650/)    // the seeded floor is nowhere on screen or in any field
    expect(bodies.join('\n')).not.toMatch(/floor_price/)
  })

  test('typing a floor previews the engine ladder, ending exactly at the floor', async ({ page }) => {
    await page.goto('/app/catalog')
    await page.getByRole('button', { name: 'Add product' }).click()
    await page.getByPlaceholder('e.g. Banarasi Silk Saree').fill('E2E ladder item')
    await page.getByPlaceholder('e.g. 7650').fill('1000')
    await page.getByText('Customers may bargain').locator('..').locator('..').getByRole('switch').click()
    await page.getByText('Let the assistant negotiate').locator('..').locator('..').getByRole('switch').click()
    await page.getByPlaceholder('e.g. 6900').fill('800')
    const preview = page.getByText('How the assistant would come down').locator('..')
    await expect(preview).toContainText('₹1,000')
    await expect(preview).toContainText('₹800')
  })
})
