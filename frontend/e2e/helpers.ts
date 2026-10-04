import { expect, type Page } from '@playwright/test'

export const OWNER = '+919999900001'
export const OPERATOR = '+919999900000'

/** Sign in through the real login screen. In dev the simulated WhatsApp inbox fills the code in. */
export async function login(page: Page, phone: string) {
  await page.goto('/app/login')
  const local = phone.replace('+91', '')
  await page.getByPlaceholder('98765 43210').fill(local)
  await page.getByRole('button', { name: /send code/i }).click()
  await expect(page).not.toHaveURL(/login/, { timeout: 30_000 })
}

export const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.scrollingElement!.scrollWidth <= window.innerWidth + 1)
