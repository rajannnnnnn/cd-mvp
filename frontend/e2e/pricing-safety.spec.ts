import { expect, test } from '@playwright/test'
import { login, OWNER, SHOP } from './helpers'

/** INV-1 in the browser: the lowest price is typed once and never comes back, in the UI or in any API response. */
test.describe('floor prices stay private', () => {
  test.beforeEach(async ({ page }) => { await login(page, OWNER) })

  test('the catalog editor never shows a stored floor and no API response carries one', async ({ page }) => {
    const bodies: string[] = []
    page.on('response', async (r) => { if (r.url().includes('/api/v1/') && r.headers()['content-type']?.includes('json')) bodies.push(await r.text().catch(() => '')) })
    await page.goto(`${SHOP}/products`)
    await page.getByText('Banarasi Silk Saree').first().click()
    await expect(page.getByText('Saved privately').first()).toBeVisible()
    await expect(page.getByText('Edit product')).toBeVisible()
    const dialog = page.getByRole('complementary', { name: 'Edit product' })
    const visible = await dialog.innerText()
    const values = await dialog.locator('input, textarea, select').evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value).join(' '))
    expect(`${visible} ${values}`).not.toMatch(/7650|7,650/)    // the seeded floor is nowhere on screen or in any field
    expect(bodies.join('\n')).not.toMatch(/floor_price/)
  })

  test('typing a floor previews the engine ladder, ending exactly at the floor', async ({ page }) => {
    await page.goto(`${SHOP}/products`)
    await page.getByRole('button', { name: 'Add product' }).click()
    await page.getByPlaceholder('Product name').fill('E2E ladder item')
    await page.getByPlaceholder('e.g. 450').fill('1000')
    await page.getByRole('radio', { name: 'Down to a lowest price' }).click()
    await page.getByPlaceholder('Lowest price in ₹', { exact: true }).fill('800')
    const preview = page.getByText('How the assistant would come down').locator('..')
    await expect(preview).toContainText('₹1,000')
    await expect(preview).toContainText('₹800')
  })

  test('the two questions: a typed price, and "a little" needs no number from the owner', async ({ page }) => {
    await page.goto(`${SHOP}/products`)
    await page.getByRole('button', { name: 'Add product' }).click()
    const panel = page.getByRole('complementary', { name: 'Add a product' })
    await panel.getByPlaceholder('Product name').fill(`E2E two questions ${Date.now()}`)
    const price = panel.getByPlaceholder('e.g. 450')
    await price.fill('from 450')
    await expect(panel.getByText('Shown as “starts from ₹450”.')).toBeVisible()
    await price.fill('450-600')
    await expect(panel.getByText('Shown as a range, ₹450 to ₹600.')).toBeVisible()
    await expect(panel.getByText('Can the assistant give a discount?').first()).toHaveCount(0)      // a range is quoted as it is; no bargaining question
    await price.fill('Rs 1,000/-')
    await expect(panel.getByText('Shown as a fixed price, ₹1,000.')).toBeVisible()
    await panel.getByRole('radio', { name: 'A little' }).click()
    await expect(panel.getByPlaceholder('Lowest price in ₹', { exact: true })).toHaveCount(0)      // the owner states no secret number
    await expect(panel.getByText('The assistant may come down a little, up to about 5%. You do not need to enter a number.')).toBeVisible()
    await price.fill('abc')
    await panel.getByRole('button', { name: 'Add product' }).click()
    await expect(panel.getByText('Type a price such as 450, from 450, or 450-600.')).toBeVisible()
  })
})
