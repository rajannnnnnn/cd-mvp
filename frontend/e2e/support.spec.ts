import { expect, test } from '@playwright/test'
import { login, OWNER, SHOP } from './helpers'

test.describe('support tickets', () => {
  test('a ticket opens the moment it is sent and shows that it is waiting for support', async ({ page }) => {
    const title = `E2E ticket ${Date.now()}`
    await login(page, OWNER)
    await page.goto(`${SHOP}/settings`)
    await page.getByRole('tab', { name: 'Support' }).click()
    await page.getByRole('textbox', { name: 'Title' }).fill(title)
    await page.getByRole('textbox', { name: 'Tell us more' }).fill('Customers say nobody replies since the morning.')
    await page.getByRole('button', { name: 'Open a ticket' }).click()
    await expect(page.getByText(/Ticket #\d+ opened/)).toBeVisible()                                  // no confirmation step, no email
    await expect(page.getByRole('heading', { name: title })).toBeVisible()
    await expect(page.getByText('Waiting for support')).toBeVisible()
    // Support's reply is written in the founder console (a separate app with its own sign-in, ADR 0028), which this suite does not drive;
    // the reply path is covered by the backend support tests.
  })
})
