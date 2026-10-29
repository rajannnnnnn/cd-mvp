import { expect, test, type Page } from '@playwright/test'
import { freshPhone, login, OWNER, SHOP } from './helpers'

/** DEAL-118 (price fields) and DEAL-113 (mandatory fields are marked and flagged), written from the QA scenarios. */
test.describe('prices and mandatory fields', () => {
  test.beforeEach(async ({ page }) => { await login(page, OWNER) })

  const openNewProduct = async (page: Page) => {
    await page.goto(`${SHOP}/products`)
    await page.getByRole('button', { name: 'Add product' }).first().click()
    return page.getByRole('complementary', { name: 'Add a product' })
  }
  const star = (scope: ReturnType<Page['locator']>, label: string) => scope.locator('label', { hasText: label }).first()

  test('DEAL-118: the Price field rejects alphabets and accepts a valid number', async ({ page }) => {
    const panel = await openNewProduct(page)
    const price = panel.getByPlaceholder('e.g. 7650')
    await price.fill('abc')
    await expect(price).toHaveValue('')                                                       // Scenario: alphabets are rejected
    await price.pressSequentially('12abc')
    await expect(price).toHaveValue('12')
    await price.fill('₹1,200')                                                                // pasted the way people write it
    await expect(price).toHaveValue('1200')
    await price.fill('1200.555')
    await expect(price).toHaveValue('1200.55')
    await price.fill('0')
    await panel.getByLabel('Product name').fill('E2E price check')
    await panel.getByRole('button', { name: 'Add product' }).click()
    await expect(panel.getByText('Price must be more than 0')).toBeVisible()                  // an appropriate validation message, under the field
    await expect(price).toHaveAttribute('aria-invalid', 'true')
    await expect(price).toBeFocused()
    await price.fill('1500')                                                                  // Scenario: a valid price is accepted and saved
    await expect(price).not.toHaveAttribute('aria-invalid', 'true')
    await panel.getByRole('button', { name: 'Add product' }).click()
    await expect(page.getByText('Product added')).toBeVisible()
    await page.getByRole('button', { name: 'Delete E2E price check' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click()
    await expect(page.getByText('E2E price check')).toHaveCount(0)
  })

  test('DEAL-118: an offer percentage and amount take numbers only', async ({ page }) => {
    await page.goto(`${SHOP}/offers`)
    await page.getByRole('button', { name: 'New offer' }).first().click()
    const panel = page.getByRole('complementary', { name: 'New offer' })
    const percent = panel.getByLabel('Percent off')
    await percent.fill('abc'); await expect(percent).toHaveValue('')
    await percent.fill('150'); await expect(percent).toHaveValue('15')
    await panel.getByRole('button', { name: /Flat amount off/ }).click()
    const amount = panel.getByLabel('Amount (₹)')
    await amount.fill('2OO'); await expect(amount).toHaveValue('2')
    await amount.fill('')
    await panel.getByLabel('Offer name').fill('E2E amount check')
    await panel.getByRole('button', { name: 'Save offer' }).click()
    await expect(panel.getByText('Amount is required')).toBeVisible()
  })

  test('DEAL-113: mandatory fields in the product editor carry a * and are flagged when empty', async ({ page }) => {
    const panel = await openNewProduct(page)
    await expect(panel.getByText(/^\*\s*required$/)).toBeVisible()                  // the "* required" note
    await expect(star(panel, 'Product name')).toContainText('*')
    await expect(star(panel, 'Price (₹)')).toContainText('*')
    for (const optional of ['Category', 'Description', 'Stock count', 'Availability']) await expect(star(panel, optional)).not.toContainText('*')
    await panel.getByRole('button', { name: 'Add product' }).click()
    await expect(panel.getByText('Give the product a name')).toBeVisible()
    await expect(panel.getByText('Price is required')).toBeVisible()
    await expect(panel.getByLabel('Product name')).toBeFocused()                              // the first missing field gets the focus
    await expect(panel.getByText('Please fix the 2 highlighted fields.')).toBeVisible()
    await panel.getByRole('radio', { name: 'Price range' }).click()
    await expect(star(panel, 'Lowest in range')).toContainText('*')
    await expect(star(panel, 'Highest in range')).toContainText('*')
    await panel.getByRole('radio', { name: 'On request' }).click()
    await expect(panel.getByText('Price (₹)')).toHaveCount(0)                                 // nothing mandatory about a price that is never quoted
  })

  test('DEAL-113: the offer editor marks its mandatory fields', async ({ page }) => {
    await page.goto(`${SHOP}/offers`)
    await page.getByRole('button', { name: 'New offer' }).first().click()
    const panel = page.getByRole('complementary', { name: 'New offer' })
    await expect(star(panel, 'Offer name')).toContainText('*')
    await expect(star(panel, 'Percent off')).toContainText('*')
    await expect(star(panel, 'Starts')).not.toContainText('*')
    await panel.getByRole('button', { name: 'Save offer' }).click()
    await expect(panel.getByText('Give the offer a name')).toBeVisible()
    await expect(panel.getByText('Percent off is required')).toBeVisible()
    await panel.getByRole('button', { name: /Free gift/ }).click()
    await expect(star(panel, 'What’s free?')).toContainText('*')
    await expect(panel.getByLabel('Percent off')).toHaveCount(0)
    await panel.getByRole('button', { name: 'Save offer' }).click()
    await expect(panel.getByText('Say what is free')).toBeVisible()
  })

  test('DEAL-113: support, shop settings and team forms mark and flag what is mandatory', async ({ page }) => {
    await page.goto(`${SHOP}/settings?tab=support`)
    await expect(star(page.locator('main'), 'Title')).toContainText('*')
    await page.getByRole('button', { name: 'Open a ticket' }).click()
    await expect(page.getByText('Add a short title')).toBeVisible()
    await expect(page.getByText('Tell us what happened')).toBeVisible()

    await page.goto(`${SHOP}/settings`)
    await expect(star(page.locator('main'), 'Shop name')).toContainText('*')
    await page.getByLabel('Shop name').fill('')
    await page.getByRole('button', { name: 'Save changes' }).first().click()
    await expect(page.getByText('Enter your shop name')).toBeVisible()

    await page.getByRole('tab', { name: 'Team' }).click()
    await page.getByRole('button', { name: /Add (a )?team member|Add member|Add someone/ }).first().click()
    const dialog = page.getByRole('dialog')
    await expect(star(dialog, 'Name')).toContainText('*')
    await expect(star(dialog, 'WhatsApp number')).toContainText('*')
    await dialog.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(dialog.getByText('Enter their name')).toBeVisible()
    await expect(dialog.getByText(/Enter a WhatsApp number/)).toBeVisible()
  })
})

test('DEAL-113: the sign-up steps mark what is mandatory', async ({ page }) => {
  await login(page, freshPhone())
  await expect(page).toHaveURL(/onboarding/)
  await expect(page.locator('label', { hasText: 'Business name' })).toContainText('*')
  await expect(page.locator('label', { hasText: 'Your name' }).first()).toContainText('*')
  await expect(page.locator('label', { hasText: 'City' })).not.toContainText('*')
  await page.getByRole('button', { name: /Create my shop/ }).click()
  await expect(page.getByText('Enter your business name')).toBeVisible()
  await expect(page.getByText('Enter your name', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Business name')).toBeFocused()
})
