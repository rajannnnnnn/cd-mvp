import { expect, test } from '@playwright/test'
import { login, noHorizontalScroll, OWNER } from './helpers'

test.describe('mobile first: owners work from phones', () => {
  test('marketing pages never scroll sideways', async ({ page }) => {
    for (const path of ['/', '/privacy.html', '/terms.html', '/data-deletion.html', '/app/login']) {
      await page.goto(path)
      expect(await noHorizontalScroll(page), path).toBe(true)
    }
  })

  test('the app has a bottom navigation and fits the screen', async ({ page }) => {
    await login(page, OWNER)
    await expect(page.locator('nav.fixed')).toBeVisible()
    for (const path of ['/app/home', '/app/chats', '/app/pipeline', '/app/catalog', '/app/inbox', '/app/settings', '/app/playground']) {
      await page.goto(path)
      await expect(page.locator('main')).toBeVisible()
      expect(await noHorizontalScroll(page), path).toBe(true)
    }
  })

  test('a conversation fills the screen on a phone and goes back to the list', async ({ page }) => {
    await login(page, OWNER)
    await page.goto('/app/chats')
    await page.getByText('Priya Kulkarni').first().click()
    await expect(page.getByText(/Banarasi Silk Saree/).first()).toBeVisible()
    await page.getByRole('button', { name: /back/i }).first().click()
    await expect(page.getByText('Kavita Nair').first()).toBeVisible()
  })
})
