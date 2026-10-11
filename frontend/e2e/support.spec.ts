import { expect, test } from '@playwright/test'
import { login, OPERATOR, OWNER, SHOP } from './helpers'

test.describe('support tickets', () => {
  test('a ticket opens the moment it is sent and support answers in the same thread', async ({ page, browser }) => {
    const title = `E2E ticket ${Date.now()}`
    await login(page, OWNER)
    await page.goto(`${SHOP}/settings`)
    await page.getByRole('tab', { name: 'Support' }).click()
    await page.getByRole('radio', { name: 'Something is not working' }).click()
    await page.getByLabel('Title').fill(title)
    await page.getByLabel('Tell us more').fill('Customers say nobody replies since the morning.')
    await page.getByRole('button', { name: 'Open a ticket' }).click()
    await expect(page.getByText(/Ticket #\d+ opened/)).toBeVisible()                                  // no confirmation step, no email
    await expect(page.getByRole('heading', { name: title })).toBeVisible()
    await expect(page.getByText('Waiting for support')).toBeVisible()

    // support side, in its own browser session
    const ops = await browser.newContext(); const op = await ops.newPage()
    await login(op, OPERATOR)
    await op.getByRole('tab', { name: /Support/ }).click()
    await op.getByRole('button', { name: new RegExp(title) }).click()
    await op.getByLabel('Your reply').fill('We have fixed it on our side. Please check now.')
    await op.getByRole('button', { name: 'Send' }).click()
    await expect(op.getByText('We have fixed it on our side. Please check now.')).toBeVisible()
    await ops.close()

    // the shop sees the answer in the same thread and can mark it sorted
    await page.reload()
    await page.getByRole('tab', { name: 'Support' }).click()
    await page.getByRole('button', { name: new RegExp(title) }).click()
    await expect(page.getByText('We have fixed it on our side. Please check now.')).toBeVisible()
    await expect(page.getByText('Support replied')).toBeVisible()
    await page.getByRole('button', { name: 'Mark as sorted' }).click()
    await expect(page.getByText('Sorted').first()).toBeVisible()
  })
})
