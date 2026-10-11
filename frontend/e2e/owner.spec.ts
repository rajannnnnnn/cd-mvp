import { expect, test } from '@playwright/test'
import { login, OWNER, SHOP } from './helpers'

test.describe('owner app', () => {
  test.beforeEach(async ({ page }) => { await login(page, OWNER) })

  test('dashboard shows what needs the owner, with live numbers', async ({ page }) => {
    await expect(page.getByText(/need you/i).first()).toBeVisible()
    await expect(page.getByText('Conversations').first()).toBeVisible()
    await expect(page.getByRole('switch', { name: 'AI assistant' })).toHaveCount(0)            // the master switch lives in Settings only
  })

  test('the AI switch lives in Settings and needs a code sent to the owner to pause or resume', async ({ page }) => {
    await page.goto(`${SHOP}/settings`)
    await page.getByRole('tab', { name: 'Assistant' }).click()
    const sw = page.getByRole('switch', { name: 'AI assistant' })
    await expect(sw).toHaveAttribute('aria-checked', 'true')

    await sw.click()                                                                              // asking sends the code …
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('heading', { name: 'Switch the assistant off?' })).toBeVisible()
    const code = dialog.getByLabel('Confirmation code')
    await code.fill('000000')                                                                     // … a wrong one changes nothing
    await dialog.getByRole('button', { name: 'Confirm and switch off' }).click()
    await expect(dialog.getByRole('alert')).toBeVisible()
    await expect(sw).toHaveAttribute('aria-checked', 'true')
    await page.waitForTimeout(1500)                                                               // codes are rate-spaced (OTP_MIN_INTERVAL_S)
    await dialog.getByRole('button', { name: 'Send the code again' }).click()
    await expect(code).toHaveValue(/\d{6}/, { timeout: 15_000 })                                  // dev: the simulated WhatsApp fills the new code in
    await dialog.getByRole('button', { name: 'Confirm and switch off' }).click()
    await expect(sw).toHaveAttribute('aria-checked', 'false')
    await page.goto(`${SHOP}/home`)
    await expect(page.getByText('The AI assistant is paused')).toBeVisible()                     // home only says so, and points back to Settings

    await page.goto(`${SHOP}/settings`)
    await page.getByRole('tab', { name: 'Assistant' }).click()
    await page.getByRole('switch', { name: 'AI assistant' }).click()
    await expect(code).toHaveValue(/\d{6}/, { timeout: 15_000 })
    await page.getByRole('dialog').getByRole('button', { name: 'Confirm and switch on' }).click()
    await expect(page.getByRole('switch', { name: 'AI assistant' })).toHaveAttribute('aria-checked', 'true')
  })

  test('a conversation opens with its history, lead details and negotiation', async ({ page }) => {
    await page.goto(`${SHOP}/chats`)
    await page.getByText('Priya Kulkarni').first().click()
    await expect(page.getByText(/Banarasi Silk Saree/).first()).toBeVisible()
    await expect(page.getByText(/AI offered/).first()).toBeVisible()
    await expect(page.getByText('Take over').first()).toBeVisible()
  })

  test('taking over a chat lets the owner reply and pauses the AI there', async ({ page }) => {
    await page.goto(`${SHOP}/chats`)
    await page.getByText('Deepak Wagh').first().click()                 // a conversation whose 24-hour window is open
    await page.getByRole('button', { name: 'Take over' }).click()
    const box = page.getByPlaceholder(/Type a reply/i).first()
    await box.fill('Namaste Deepak ji, main khud dekh leta hoon 🙏')
    await box.press('Enter')
    await expect(page.getByText('main khud dekh leta hoon').first()).toBeVisible()
    await page.getByRole('button', { name: /Hand back to AI/ }).first().click()
  })

  test('pipeline groups every conversation by stage', async ({ page }) => {
    await page.goto(`${SHOP}/pipeline`)
    for (const stage of ['New', 'Exploring', 'Interested', 'Negotiating', 'Ready to buy']) await expect(page.getByRole('heading', { name: stage, exact: false }).first()).toBeVisible()
    await page.getByText('Anjali Deshmukh').first().click()
    await expect(page).toHaveURL(/\/app\/[a-z0-9-]+\/chats\//)
  })

  test('the inbox lists customers waiting, orders to confirm and questions, and a deal can be confirmed', async ({ page }) => {
    await page.goto(`${SHOP}/inbox`)
    await expect(page.getByText('Customers waiting')).toBeVisible()
    await page.getByRole('tab', { name: /To confirm/ }).click()
    await expect(page.getByText(/Banarasi Silk Saree/).first()).toBeVisible()
    await page.getByRole('tab', { name: /Questions/ }).click()
    await expect(page.getByRole('button', { name: 'Answer' }).first()).toBeVisible()
  })

  test('offers can be created, switched off and deleted', async ({ page }) => {
    await page.goto(`${SHOP}/offers`)
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
    await page.goto(`${SHOP}/customers`)
    const row = page.locator('div', { hasText: 'Ritu Singh' }).last()
    await expect(page.getByText('Ritu Singh').first()).toBeVisible()
    await page.getByRole('button', { name: 'Personal' }).first().click()
    await expect(page.getByText('Personal').first()).toBeVisible()
    await page.getByRole('button', { name: /Not personal/ }).first().click()   // restore
    void row
  })

  test('settings: shop details and the reply-speed preset save', async ({ page }) => {
    await page.goto(`${SHOP}/settings`)
    await page.getByLabel('Shop phone').fill('+91 99999 00002')
    await page.getByRole('button', { name: 'Save changes' }).first().click()
    await expect(page.getByText('Saved').first()).toBeVisible()
    await page.getByRole('tab', { name: /Speed/ }).click()
    await page.getByRole('button', { name: /Natural/ }).click()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText('Saved').first()).toBeVisible()
  })

  test('settings: the owner can download their data and is asked to type the shop name before deleting', async ({ page }) => {
    await page.goto(`${SHOP}/settings`)
    await page.getByRole('tab', { name: /Security/ }).click()
    const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download my data' }).click()])
    expect(dl.suggestedFilename()).toBe('my-shop-data.json')
    await page.getByRole('button', { name: /Delete my shop/ }).click()
    const confirm = page.getByRole('button', { name: 'Delete everything' })
    await expect(confirm).toBeDisabled()                                  // nothing is deleted until the exact name is typed
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByRole('heading', { name: 'Your data' })).toBeVisible()
  })

  test('catalog: cards and grid views, a confirmed on/off switch, and a bulk change with a final confirmation', async ({ page }) => {
    await page.goto(`${SHOP}/products`)
    await expect(page.getByRole('switch', { name: /is for sale/ }).first()).toBeVisible()
    await page.getByRole('tab', { name: 'Grid' }).click()
    await expect(page.getByRole('columnheader', { name: 'For sale' })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('columnheader', { name: 'For sale' })).toBeVisible()                // the view is remembered
    await page.getByRole('tab', { name: 'Cards' }).click()

    // one product: the switch only asks, the dialog decides
    const first = page.getByRole('switch', { name: /is for sale/ }).first()
    await expect(first).toHaveAttribute('aria-checked', 'true')
    await first.click()
    await expect(page.getByRole('heading', { name: /Switch off/ })).toBeVisible()
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(first).toHaveAttribute('aria-checked', 'true')                                     // cancelled: nothing changed
    await first.click()
    await page.getByRole('button', { name: 'Switch off', exact: true }).click()
    await expect(first).toHaveAttribute('aria-checked', 'false')
    await first.click()                                                                              // put it back
    await page.getByRole('button', { name: 'Switch on', exact: true }).click()
    await expect(first).toHaveAttribute('aria-checked', 'true')

    // several products: tick, choose, and confirm once more
    const ticks = page.getByRole('checkbox', { name: /^Select (?!all)/ })
    await expect(ticks).toHaveCount(0)                                                               // no ticks until Select is pressed
    await page.getByRole('button', { name: 'Select', exact: true }).click()
    await ticks.nth(0).click(); await ticks.nth(1).click()
    await expect(page.getByText('2 selected')).toBeVisible()
    await page.getByRole('region', { name: 'Selected products' }).getByRole('button', { name: 'Switch off' }).click()
    await expect(page.getByRole('heading', { name: 'Switch off 2 products?' })).toBeVisible()
    await page.getByRole('button', { name: 'Yes, switch off 2' }).click()
    await expect(page.getByText('2 products updated')).toBeVisible()
    await page.getByRole('button', { name: 'Select', exact: true }).click()                         // the bulk change ended selection; start again to restore both
    await ticks.nth(0).click(); await ticks.nth(1).click()
    await page.getByRole('region', { name: 'Selected products' }).getByRole('button', { name: 'Switch on' }).click()
    await page.getByRole('button', { name: 'Yes, switch on 2' }).click()
    await expect(page.getByText('2 products updated').first()).toBeVisible()
  })

  test('products: edit and delete on every item, a docked editor beside the list, and a category picker', async ({ page }) => {
    const stamp = Date.now(); const cat = `Zebra Prints ${stamp}`
    await page.goto(`${SHOP}/products`)
    await expect(page.getByRole('heading', { name: 'Products', exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Products', exact: true }).first()).toBeVisible()      // renamed in the menu too

    // the editor opens beside the list: no overlay, and the list stays usable
    await page.getByRole('button', { name: 'Add product' }).first().click()
    const panel = page.getByRole('complementary', { name: 'Add a product' })
    await expect(panel).toBeVisible()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.getByRole('switch', { name: /is for sale/ }).first()).toBeVisible()
    await panel.getByLabel('Product name').fill(`E2E Kurta ${stamp}`)
    await panel.getByRole('combobox', { name: 'Category' }).fill(cat)
    await panel.getByPlaceholder('e.g. 7650').fill('500')
    await panel.getByRole('button', { name: 'Add product' }).click()
    await expect(page.getByText('Product added')).toBeVisible()
    await expect(panel).toHaveCount(0)

    // a second product: typing a near-duplicate is caught, and the existing categories can be picked
    await page.getByRole('button', { name: 'Add product' }).first().click()
    const second = page.getByRole('complementary', { name: 'Add a product' })
    const box = second.getByRole('combobox', { name: 'Category' })
    await box.fill(cat.replace('Prints', 'print'))                                                    // singular and lower case
    await expect(second.getByText(`You already have a category called “${cat}”.`)).toBeVisible()
    await second.getByRole('button', { name: `Use “${cat}”` }).click()
    await expect(box).toHaveValue(cat)
    await second.getByRole('button', { name: 'Show your categories' }).click()
    await expect(second.getByRole('option', { name: cat })).toBeVisible()
    await second.getByRole('option', { name: cat }).click()
    await expect(box).toHaveValue(cat)
    await second.getByLabel('Product name').fill(`E2E Dupatta ${stamp}`)
    await second.getByPlaceholder('e.g. 7650').fill('600')
    await second.getByRole('button', { name: 'Add product' }).click()
    await expect(page.getByText('Product added').first()).toBeVisible()

    // every card has Edit, Delete and the on/off switch
    const kurta = page.getByText(`E2E Kurta ${stamp}`)
    await expect(kurta).toBeVisible()
    await page.getByRole('button', { name: `Edit E2E Kurta ${stamp}` }).click()
    const edit = page.getByRole('complementary', { name: 'Edit product' })
    await expect(edit.getByLabel('Product name')).toHaveValue(`E2E Kurta ${stamp}`)
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await edit.getByRole('button', { name: 'Cancel' }).click()
    await expect(edit).toHaveCount(0)

    // delete one, with a confirmation
    await page.getByRole('button', { name: `Delete E2E Kurta ${stamp}` }).click()
    await expect(page.getByRole('heading', { name: `Delete “E2E Kurta ${stamp}”?` })).toBeVisible()
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(kurta).toBeVisible()                                                                  // cancelled: still there
    await page.getByRole('button', { name: `Delete E2E Kurta ${stamp}` }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click()
    await expect(kurta).toHaveCount(0)

    // delete in bulk, with a final confirmation, from the grid
    await page.getByRole('tab', { name: 'Grid' }).click()
    await page.getByRole('button', { name: 'Select', exact: true }).click()
    await page.getByRole('checkbox', { name: `Select E2E Dupatta ${stamp}` }).click()
    await page.getByRole('region', { name: 'Selected products' }).getByRole('button', { name: 'Delete' }).click()
    await expect(page.getByRole('heading', { name: 'Delete 1 product?' })).toBeVisible()
    await page.getByRole('button', { name: 'Yes, delete 1' }).click()
    await expect(page.getByText(`E2E Dupatta ${stamp}`)).toHaveCount(0)
    await page.getByRole('tab', { name: 'Cards' }).click()
  })

  test('related pages share one menu entry and nothing technical sits in the menu', async ({ page }) => {
    const nav = page.locator('aside nav')
    for (const gone of ['Pipeline', 'Reports', 'My voice', 'Playground', 'Try the assistant']) await expect(nav.getByRole('link', { name: gone, exact: true })).toHaveCount(0)
    await page.goto(`${SHOP}/chats`)
    await expect(page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: 'Conversations' })).toBeVisible()
    await page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: 'Pipeline' }).click()
    await expect(page).toHaveURL(/\/pipeline$/)
    await expect(nav.getByRole('link', { name: 'Chats' })).toHaveClass(/nav-item-active/)           // the one menu entry stays lit
    await page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: 'Conversations' }).click()
    await page.getByRole('link', { name: 'Try the assistant' }).click()                              // the practice chat is a button on Chats
    await expect(page).toHaveURL(/\/try$/)
    await expect(page.getByRole('heading', { name: /Try the assistant/ })).toBeVisible()

    await page.goto(`${SHOP}/analytics`)
    await page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: 'Reports' }).click()
    await expect(page).toHaveURL(/\/reports$/)
    await expect(nav.getByRole('link', { name: 'Analytics' })).toHaveClass(/nav-item-active/)

    await page.goto(`${SHOP}/voice`)                                                                 // the old address lands in Settings
    await expect(page).toHaveURL(/\/settings\?tab=voice/)
    await expect(page.getByRole('heading', { name: /My voice/ })).toBeVisible()
  })

  test('the product editor has no visibility toggle or out-of-stock option, and one way of showing the price', async ({ page }) => {
    await page.goto(`${SHOP}/products`)
    await page.getByRole('button', { name: 'Add product' }).first().click()
    const panel = page.getByRole('complementary', { name: 'Add a product' })
    await expect(panel.getByText('Offer this to customers')).toHaveCount(0)
    await expect(panel.getByRole('option', { name: 'Out of stock' })).toHaveCount(0)
    const types = panel.getByRole('radiogroup', { name: 'How the price is shown' })
    await expect(types.getByRole('radio')).toHaveText(['Fixed price', 'Starts from', 'Price range', 'On request'])
    await expect(panel.getByText('The assistant tells customers the price straight away.')).toHaveCount(0)
    await types.getByRole('radio', { name: 'Price range' }).click()
    await expect(panel.getByText('Lowest in range (₹)')).toBeVisible()
    await panel.getByRole('button', { name: 'Add a variant (size, colour…)' }).click()
    await expect(panel.getByRole('radio')).toHaveCount(4)                                            // one price type for the whole product, not one per variant
    await expect(panel.getByText('Lowest in range (₹)')).toHaveCount(2)
    await types.getByRole('radio', { name: 'On request' }).click()
    await expect(panel.getByText('Lowest in range (₹)')).toHaveCount(0)
    await expect(panel.getByText('Customers may bargain')).toHaveCount(0)
  })

  test('offers work like products: docked editor, switch with confirmation, bulk actions, and a whole category as the target', async ({ page }) => {
    const stamp = Date.now(); const a = `E2E offer A ${stamp}`; const b = `E2E offer B ${stamp}`
    await page.goto(`${SHOP}/offers`)
    await page.getByRole('button', { name: 'New offer' }).first().click()
    const panel = page.getByRole('complementary', { name: 'New offer' })
    await expect(panel).toBeVisible()
    await expect(page.getByRole('dialog')).toHaveCount(0)                                            // beside the list, not over it
    await expect(panel.getByText('Offer is live')).toHaveCount(0)
    await panel.getByLabel('Offer name').fill(a)
    await panel.getByLabel('Percent off').fill('10')
    const target = panel.getByLabel('Applies to')
    await expect(target.locator('optgroup[label="A whole category"] option')).not.toHaveCount(0)    // categories can be picked, not only products
    await target.selectOption({ index: 1 })
    await panel.getByRole('button', { name: 'Save offer' }).click()
    await expect(page.getByText('Offer created')).toBeVisible()
    await page.getByRole('button', { name: 'New offer' }).first().click()
    const second = page.getByRole('complementary', { name: 'New offer' })
    await second.getByLabel('Offer name').fill(b)
    await second.getByLabel('Percent off').fill('5')
    await second.getByRole('button', { name: 'Save offer' }).click()
    await expect(page.getByText(b)).toBeVisible()

    const sw = page.getByRole('switch', { name: `${a} is on` })                                       // switch, with a confirmation
    await sw.click()
    await expect(page.getByRole('heading', { name: `Switch off “${a}”?` })).toBeVisible()
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(sw).toHaveAttribute('aria-checked', 'true')
    await sw.click()
    await page.getByRole('button', { name: 'Switch off', exact: true }).click()
    await expect(sw).toHaveAttribute('aria-checked', 'false')

    await page.getByRole('button', { name: `Edit ${a}` }).click()                                      // edit opens beside the list
    await expect(page.getByRole('complementary', { name: 'Edit offer' }).getByLabel('Offer name')).toHaveValue(a)
    await page.getByRole('complementary', { name: 'Edit offer' }).getByRole('button', { name: 'Cancel' }).click()

    await page.getByRole('tab', { name: 'Grid' }).click()                                              // grid view, tick both, delete in bulk
    await expect(page.getByRole('checkbox')).toHaveCount(0)
    await page.getByRole('button', { name: 'Select', exact: true }).click()
    await page.getByRole('checkbox', { name: `Select ${a}` }).click()
    await page.getByRole('checkbox', { name: `Select ${b}` }).click()
    await page.getByRole('region', { name: 'Selected offers' }).getByRole('button', { name: 'Delete' }).click()
    await expect(page.getByRole('heading', { name: 'Delete 2 offers?' })).toBeVisible()
    await page.getByRole('button', { name: 'Yes, delete 2' }).click()
    await expect(page.getByText(a)).toHaveCount(0)
    await page.getByRole('tab', { name: 'Cards' }).click()
  })
})
