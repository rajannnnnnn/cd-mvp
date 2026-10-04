import { expect, test } from '@playwright/test'
import { freshPhone, login, OWNER, SHOP } from './helpers'

test.describe('marketing to a live shop', () => {
  test('home and pricing pages lead to sign-up, with the plans from the billing catalogue', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('link', { name: 'Start your free trial' }).first()).toHaveAttribute('href', /\/app\/login/)
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Pricing' }).click()
    await expect(page).toHaveURL(/\/pricing/)
    for (const plan of ['Starter', 'Growth', 'Scale']) await expect(page.getByRole('heading', { name: plan })).toBeVisible()
    await expect(page.getByText('₹2,499').first()).toBeVisible()
    await page.getByRole('button', { name: /Yearly/ }).click()
    await expect(page.getByText('Billed ₹24,990 a year. Two months free.')).toBeVisible()
    await page.goto('/start')
    await expect(page.getByRole('link', { name: 'Start free for 14 days' }).first()).toBeVisible()
    await page.goto('/contact')
    await expect(page.getByRole('heading', { name: 'Talk to us', level: 1 })).toBeVisible()
  })

  test('a brand-new number signs up, sets up a shop, and lands on its own address', async ({ page }) => {
    const phone = freshPhone()
    const tag = String(Date.now() % 1e7)                                         // unique per run: addresses are unique
    const shop = `Meera Fabrics ${tag}`
    const slug = `meera-fabrics-${tag}`
    await page.goto('/?utm_source=e2e&utm_campaign=spring')                      // a campaign visit is remembered through sign-up
    await page.getByRole('link', { name: 'Start your free trial' }).first().click()
    await login(page, phone)
    await expect(page).toHaveURL(/\/app\/onboarding/)
    await expect(page.getByRole('heading', { name: 'Tell us about your business' })).toBeVisible()

    await page.getByLabel('Business name').fill(shop)
    await page.getByLabel('Your name').fill('Meera')
    await page.getByLabel('City').fill('Pune')
    await expect(page.getByText('Available')).toBeVisible()
    await page.getByRole('button', { name: 'Create my shop' }).click()
    await expect(page).toHaveURL(new RegExp(`/app/${slug}/onboarding\\?step=details`))       // the shop's own address from here on

    await page.getByLabel('Address').fill('12 MG Road, Pune 411001')
    await page.getByLabel('Opening hours').fill('Mon-Sat 10am-8pm')
    await page.getByRole('button', { name: 'UPI' }).click()
    await page.getByRole('button', { name: 'Continue' }).click()

    await expect(page.getByRole('heading', { name: 'What do you sell?' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()                    // at least one product is required
    await page.getByLabel('Product or service name').fill('Banarasi Silk Saree')
    await page.getByLabel('Price (₹)').fill('1999')
    await page.getByRole('button', { name: 'Add product' }).click()
    await expect(page.getByText('Banarasi Silk Saree').first()).toBeVisible()
    await page.getByRole('button', { name: 'Continue' }).click()

    await expect(page.getByRole('heading', { name: 'Connect your WhatsApp number' })).toBeVisible()
    await page.getByRole('button', { name: /Use a test number/ }).click()
    await expect(page.getByText('Test number').first()).toBeVisible()
    await page.getByRole('button', { name: 'Continue' }).click()

    await expect(page.getByRole('heading', { name: 'How should your assistant behave?' })).toBeVisible()
    await page.getByRole('button', { name: 'Continue' }).click()

    await expect(page.getByRole('heading', { name: 'Ready when you are' })).toBeVisible()
    await page.getByRole('button', { name: 'Go live' }).last().click()                // (the first is the step in the side list)
    await expect(page).toHaveURL(new RegExp(`/app/${slug}/home`))
    await expect(page.getByRole('link', { name: /Billing/ }).first()).toBeVisible()

    // the free trial is running, and analytics (a Growth feature) is open during it
    await page.getByRole('link', { name: 'Billing' }).first().click()
    await expect(page.getByText(/days left in your free trial/).first()).toBeVisible()
    await page.getByRole('link', { name: 'Analytics' }).first().click()
    await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible()
    await expect(page.getByText('Customer conversations')).toBeVisible()
  })

  test('a shop address works as a short link and other shops are not reachable', async ({ page }) => {
    await login(page, OWNER)
    await expect(page).toHaveURL(/\/app\/sharma-sarees-fabrics\/home/)
    await page.goto('/sharma-sarees-fabrics')                                       // the short link: /<slug> lands on the shop
    await expect(page).toHaveURL(/\/app\/sharma-sarees-fabrics/)
    await page.goto('/app/gupta-mobile-point/home')                                 // someone else's shop
    await expect(page.getByRole('heading', { name: 'This address isn’t yours' })).toBeVisible()
    await page.goto('/app/chats', { waitUntil: 'commit' })                         // an old-style link is moved to the shop address
    await expect(page).toHaveURL(/\/app\/sharma-sarees-fabrics\/chats/)
  })
})

test.describe('billing and reports for an existing shop', () => {
  test.beforeEach(async ({ page }) => { await login(page, OWNER) })

  test('the billing screen shows the plan and usage (pilot shops have no charge)', async ({ page }) => {
    await page.goto(`${SHOP}/billing`)
    await expect(page.getByRole('heading', { name: 'Billing' })).toBeVisible()
    await expect(page.getByText('No charge during the pilot').first()).toBeVisible()
    await expect(page.getByText('AI conversations this period')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Choose a plan' })).toBeVisible()
  })

  test('analytics shows the key figures and reports can be downloaded', async ({ page }) => {
    await page.goto(`${SHOP}/analytics`)
    await expect(page.getByText('Customer conversations')).toBeVisible()
    await expect(page.getByText('Where conversations ended up')).toBeVisible()
    await page.getByRole('tab', { name: '7 days' }).click()
    await page.goto(`${SHOP}/reports`)
    await expect(page.getByRole('heading', { name: 'Reports' })).toBeVisible()
    const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Customers' }).click()])
    expect(dl.suggestedFilename()).toMatch(/^customers-.*\.csv$/)
  })
})

test.describe('paying for a plan', () => {
  test('a new shop on its trial chooses a plan and pays in test mode', async ({ page }) => {
    await login(page, freshPhone())
    const tag = String(Date.now() % 1e7)
    await page.getByLabel('Business name').fill(`Pay Test ${tag}`)
    await page.getByLabel('Your name').fill('Payer')
    await page.getByRole('button', { name: 'Create my shop' }).click()
    await expect(page).toHaveURL(new RegExp(`/app/pay-test-${tag}/onboarding`))
    await page.goto(`/app/pay-test-${tag}/billing`)
    await expect(page.getByText(/days left in your free trial/).first()).toBeVisible()
    await page.getByRole('button', { name: 'Choose Starter' }).click()
    await expect(page.getByText('TEST MODE')).toBeVisible()
    await expect(page.getByText('₹1,178.82').first()).toBeVisible()                   // ₹999 + 18% GST
    await page.getByRole('button', { name: /Pay ₹1,178.82/ }).click()
    await expect(page.getByText('Your plan is active and the assistant is switched on.')).toBeVisible()
    await page.getByRole('button', { name: 'Done' }).click()
    await expect(page.getByText('Starter').first()).toBeVisible()
    await expect(page.getByText('paid', { exact: true }).first()).toBeVisible()
    await page.goto(`/app/pay-test-${tag}/analytics`)                                // Starter has no analytics: an upgrade note, not an error
    await expect(page.getByRole('heading', { name: 'This is part of the Growth plan' })).toBeVisible()
  })
})
