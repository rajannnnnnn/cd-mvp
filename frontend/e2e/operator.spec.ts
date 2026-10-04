import { expect, test } from '@playwright/test'
import { login, OPERATOR } from './helpers'

test.describe('operator console', () => {
  test.beforeEach(async ({ page }) => { await login(page, OPERATOR) })

  test('lists every tenant with health, volume and cost, and the plumbing', async ({ page }) => {
    await expect(page.getByText('Sharma Sarees & Fabrics')).toBeVisible()
    await expect(page.getByText('Gupta Mobile Point')).toBeVisible()
    for (const tab of ['Alerts', 'Queues', 'Webhooks', 'AI turns', 'Costs']) {
      await page.getByRole('tab', { name: tab }).click()
    }
    await page.getByRole('tab', { name: 'Queues' }).click()
    await expect(page.getByText('conversation.turns')).toBeVisible()
  })

  test('onboards a business, opens it as the owner, comes back, and deletes it only after typing its name', async ({ page }) => {
    const name = `E2E Shop ${Date.now() % 100000}`
    const phone = `9${String(Date.now()).slice(-9)}`
    await page.getByRole('button', { name: 'Onboard business' }).click()
    await page.getByLabel('Business name').fill(name)
    await page.getByLabel('Owner’s name').fill('Test Owner')
    await page.getByLabel(/Owner’s WhatsApp number/).fill(`+91${phone}`)
    await page.getByRole('button', { name: 'Create business' }).click()
    await expect(page.locator('tr', { hasText: name })).toBeVisible()

    const row = page.locator('tr', { hasText: name })
    await row.getByRole('button', { name: /Open as owner/ }).click()
    await expect(page).toHaveURL(/\/app\/home/)
    await expect(page.getByText(/Viewing as owner/)).toBeVisible()
    await page.getByText(/Back to console/).click()
    await expect(page).toHaveURL(/\/app\/operator/)

    await page.locator('tr', { hasText: name }).getByTitle('Delete tenant').click()
    const del = page.getByRole('button', { name: 'Delete everything' })
    await expect(del).toBeDisabled()
    await page.getByRole('dialog').getByRole('textbox').fill(name)
    await del.click()
    await expect(page.locator('tr', { hasText: name })).toHaveCount(0)
  })
})
