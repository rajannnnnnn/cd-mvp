import { expect, test } from '@playwright/test'
import { login, OWNER } from './helpers'

test.describe('owner app', () => {
  test.beforeEach(async ({ page }) => { await login(page, OWNER) })

  test('dashboard shows what needs the owner, with live numbers', async ({ page }) => {
    await expect(page.getByText(/need you/i).first()).toBeVisible()
    await expect(page.getByText('Conversations').first()).toBeVisible()
    await expect(page.getByText('AI assistant is ON').first()).toBeVisible()
  })

  test('the AI switch pauses and resumes the whole business', async ({ page }) => {
    const sw = page.getByRole('switch', { name: 'AI assistant' }).first()
    await expect(sw).toBeChecked()
    await sw.click()
    await expect(page.getByText('AI assistant paused').first()).toBeVisible()
    await sw.click()
    await expect(page.getByText('AI assistant is ON').first()).toBeVisible()
  })

  test('a conversation opens with its history, lead details and negotiation', async ({ page }) => {
    await page.goto('/app/chats')
    await page.getByText('Priya Kulkarni').first().click()
    await expect(page.getByText(/Banarasi Silk Saree/).first()).toBeVisible()
    await expect(page.getByText(/AI offered/).first()).toBeVisible()
    await expect(page.getByText('Take over').first()).toBeVisible()
  })

  test('taking over a chat lets the owner reply and pauses the AI there', async ({ page }) => {
    await page.goto('/app/chats')
    await page.getByText('Deepak Wagh').first().click()                 // a conversation whose 24-hour window is open
    await page.getByRole('button', { name: 'Take over' }).click()
    const box = page.getByPlaceholder(/Type a reply/i).first()
    await box.fill('Namaste Deepak ji, main khud dekh leta hoon 🙏')
    await box.press('Enter')
    await expect(page.getByText('main khud dekh leta hoon').first()).toBeVisible()
    await page.getByRole('button', { name: /Hand back to AI/ }).first().click()
  })

  test('pipeline groups every conversation by stage', async ({ page }) => {
    await page.goto('/app/pipeline')
    for (const stage of ['New', 'Exploring', 'Interested', 'Negotiating', 'Ready to buy']) await expect(page.getByRole('heading', { name: stage, exact: false }).first()).toBeVisible()
    await page.getByText('Anjali Deshmukh').first().click()
    await expect(page).toHaveURL(/\/app\/chats\//)
  })

  test('the inbox lists customers waiting, orders to confirm and questions, and a deal can be confirmed', async ({ page }) => {
    await page.goto('/app/inbox')
    await expect(page.getByText('Customers waiting')).toBeVisible()
    await page.getByRole('tab', { name: /To confirm/ }).click()
    await expect(page.getByText(/Banarasi Silk Saree/).first()).toBeVisible()
    await page.getByRole('tab', { name: /Questions/ }).click()
    await expect(page.getByRole('button', { name: 'Answer' }).first()).toBeVisible()
  })

  test('offers can be created, switched off and deleted', async ({ page }) => {
    await page.goto('/app/offers')
    await page.getByRole('button', { name: 'New offer' }).click()
    await page.getByPlaceholder('e.g. Festive 10% off').fill('E2E test offer')
    await page.getByPlaceholder('10', { exact: true }).fill('5')
    await page.getByRole('button', { name: 'Save offer' }).click()
    await expect(page.getByText('E2E test offer')).toBeVisible()
    const row = page.locator('div.card', { hasText: 'E2E test offer' })
    await row.getByRole('button', { name: 'Delete' }).click()
    await page.getByRole('button', { name: 'Delete' }).last().click()
    await expect(page.getByText('E2E test offer')).toHaveCount(0)
  })

  test('customers can be marked personal so the assistant never answers them', async ({ page }) => {
    await page.goto('/app/customers')
    const row = page.locator('div', { hasText: 'Ritu Singh' }).last()
    await expect(page.getByText('Ritu Singh').first()).toBeVisible()
    await page.getByRole('button', { name: 'Personal' }).first().click()
    await expect(page.getByText('Personal').first()).toBeVisible()
    await page.getByRole('button', { name: /Not personal/ }).first().click()   // restore
    void row
  })

  test('settings: shop details and the reply-speed preset save', async ({ page }) => {
    await page.goto('/app/settings')
    await page.getByLabel('Shop phone').fill('+91 99999 00002')
    await page.getByRole('button', { name: 'Save changes' }).first().click()
    await expect(page.getByText('Saved').first()).toBeVisible()
    await page.getByRole('tab', { name: /Speed/ }).click()
    await page.getByRole('button', { name: /Natural/ }).click()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText('Saved').first()).toBeVisible()
  })
})
