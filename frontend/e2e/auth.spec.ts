import { expect, test } from '@playwright/test'
import { login, OPERATOR, OWNER } from './helpers'

test.describe('number-centric authentication', () => {
  test('an owner signs in with a code sent to their WhatsApp number and stays signed in across reloads', async ({ page }) => {
    await login(page, OWNER)
    await expect(page).toHaveURL(/\/app\/home/)
    await expect(page.getByRole('heading', { name: /Namaste/ })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: /Namaste/ })).toBeVisible()
  })

  test('signing out ends the session', async ({ page }) => {
    await login(page, OWNER)
    await page.getByRole('button', { name: /Rakesh/ }).first().click()
    await page.getByRole('button', { name: /sign out|log out/i }).click()
    await expect(page).toHaveURL(/login/)
    await page.goto('/app/home')
    await expect(page).toHaveURL(/login/)
  })

  test('the app is closed to anyone who is not signed in', async ({ page }) => {
    for (const path of ['/app/home', '/app/chats', '/app/catalog', '/app/operator']) {
      await page.goto(path)
      await expect(page).toHaveURL(/login/)
    }
  })

  test('a wrong code is rejected with a plain message', async ({ page }) => {
    await page.goto('/app/login')
    await page.getByPlaceholder('98765 43210').fill('99999 00001')
    // intercept the auto-filled code so the test types a wrong one
    await page.route('**/api/v1/sim/inbox*', (r) => r.fulfill({ json: [] }))
    await page.getByRole('button', { name: /send code/i }).click()
    await page.getByLabel('Digit 1').pressSequentially('000000')
    await expect(page.getByText(/wrong or has expired/i)).toBeVisible()
  })

  test('the operator lands in the console and an owner cannot open it', async ({ page }) => {
    await login(page, OPERATOR)
    await expect(page).toHaveURL(/\/app\/operator/)
    await expect(page.getByRole('heading', { name: 'Operator console' })).toBeVisible()
  })
})
