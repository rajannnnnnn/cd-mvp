import { expect, type Page } from '@playwright/test'

export const OWNER = '+919999900001'
/** The seeded demo shop's address. */
export const SHOP = '/app/sharma-sarees-fabrics'
export const OPERATOR = '+919999900000'

/** Sign in through the real login screen. In dev the simulated WhatsApp inbox fills the code in. */
export async function login(page: Page, phone: string) {
  await page.goto('/app/login')
  const local = phone.replace('+91', '')
  await page.getByPlaceholder('98765 43210').fill(local)
  await page.getByRole('button', { name: /continue with whatsapp/i }).click()
  // signed in: a shop's home, the setup wizard, or the operator console (the bare /app first hops to the shop address)
  await expect(page).toHaveURL(/\/app\/(?:[a-z0-9-]+\/(?:home|onboarding)|onboarding|operator)/, { timeout: 30_000 })
}

/** A phone number nobody has used before, so signing in with it starts the sign-up flow. */
export const freshPhone = () => `+919${String(Math.floor(Math.random() * 1e9)).padStart(9, '0')}`

export const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.scrollingElement!.scrollWidth <= window.innerWidth + 1)
