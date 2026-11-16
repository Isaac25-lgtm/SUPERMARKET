import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'

// Credentials are derived locally (python -m motion_api.creds) into a temp JSON file — never committed.
const creds: Record<string, string> = JSON.parse(readFileSync(process.env.E2E_CREDS!, 'utf8'))
const shots = process.env.E2E_SHOTS ?? 'e2e/screenshots'

async function login(page: Page, user: string) {
  await page.context().clearCookies()
  await page.goto('/login')
  await page.getByLabel('Username').fill(user)
  await page.getByLabel('Password').fill(creds[user])
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'))
}

const roles: [string, string, string][] = [
  ['hm.owner', '/dashboard', 'Head Manager'], ['gm.grace', '/dashboard', 'General Manager'], ['acc.amina', '/dashboard', 'Accountant'],
  ['ops.david', '/dashboard', 'Operations'], ['inv.ruth', '/dashboard', 'Inventory'], ['bm.joseph', '/dashboard', 'Branch Manager'],
]

for (const [user, path, label] of roles) {
  test(`${label} dashboard renders with live data`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    await login(page, user)
    await page.goto(path)
    await expect(page.getByText('Sales today (incl. VAT)')).toBeVisible()
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: `${shots}/${user}-dashboard.png`, fullPage: true })
    if (user === 'hm.owner' || user === 'bm.joseph') {
      await page.setViewportSize({ width: 390, height: 844 })
      await page.screenshot({ path: `${shots}/${user}-dashboard-mobile.png`, fullPage: false })
    }
    expect(errors).toEqual([])
  })
}

test('every navigation link renders for the Head Manager', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await login(page, 'hm.owner')
  await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Audit trail' })).toBeVisible()
  const links =await page.getByRole('navigation', { name: 'Main' }).getByRole('link').evaluateAll((els) => els.map((e) => (e as HTMLAnchorElement).pathname))
  // M7: assert the expected navigation set before iterating (cannot pass with zero links).
  for (const expected of ['/dashboard', '/approvals', '/stock', '/finance', '/admin/users', '/audit']) expect(links).toContain(expected)
  for (const href of links) {
    await page.goto(href)
    await page.waitForLoadState('networkidle')
    await expect(page.getByText('Page not found')).toHaveCount(0)
    await expect(page.getByRole('alert')).toHaveCount(0)
    await page.screenshot({ path: `${shots}/owner${href.replace(/\//g, '_')}.png`, fullPage: true })
  }
  expect(errors).toEqual([])
})

test('cashier sells at the POS and gets a receipt', async ({ page }) => {
  await login(page, 'cash.cen1')
  await expect(page).toHaveURL(/\/pos/)
  // M7: wait for the till to settle into either the fresh-shift form or the open till.
  const openShift = page.getByRole('button', { name: 'Open shift' })
  const scanner = page.getByLabel('Scan barcode or search products')
  await expect(openShift.or(scanner)).toBeVisible()
  if (await openShift.isVisible()) {
    await openShift.click()
  }
  await expect(scanner).toBeVisible()
  await page.getByLabel('Scan barcode or search products').fill('Fresh milk')
  await page.getByRole('button', { name: /Fresh milk 500ml/ }).first().click()
  await page.getByRole('button', { name: /^Charge/ }).click()
  await page.getByRole('button', { name: /^5,000$|5,000/ }).first().click().catch(() => page.getByLabel('Cash received').fill('5000'))
  await page.getByLabel('Cash received').fill('5000')
  await page.getByRole('button', { name: /Complete sale/ }).click()
  await expect(page.getByText('Sale complete')).toBeVisible()
  await expect(page.getByText(/CEN-T1-\d{6}/)).toBeVisible()
  await page.screenshot({ path: `${shots}/cashier-receipt.png` })
  await page.getByRole('button', { name: 'New sale' }).click()
  await page.screenshot({ path: `${shots}/cashier-pos.png`, fullPage: true })
})

test('cashier cannot reach back-office pages', async ({ page }) => {
  await login(page, 'cash.cen1')
  await page.goto('/admin/users')
  await expect(page.getByText('Not available for your role')).toBeVisible()
})

test('an accountant downloads an audited CSV report', async ({ page }) => {
  await login(page, 'acc.amina')
  await page.goto('/reports')
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Branch & consolidated P&L' }).click()
  expect((await downloaded).suggestedFilename()).toBe('pnl.csv')
})

test('logo returns to the dashboard and sign-out lands on the sign-in page', async ({ page }) => {
  await login(page, 'gm.grace')
  await page.goto('/stock')
  await page.getByRole('link', { name: /go to my dashboard/ }).click()
  await expect(page).toHaveURL(/\/dashboard/)
  await expect(page.getByText('Sales today (incl. VAT)')).toBeVisible()
  await page.getByRole('button', { name: 'Account menu' }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  // The old session is really gone: protected pages send you back to sign in.
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\/login/)
})

test('Ask Motion AI floater answers on any page; alerts page renders', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await login(page, 'gm.grace')
  await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Ask Motion AI' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Ask Motion AI' }).click()
  const chat = page.getByRole('dialog', { name: 'Ask Motion AI' })
  await expect(chat).toBeVisible()
  await chat.getByLabel('Message Motion AI').fill('Which items may run out soon?')
  await page.keyboard.press('Enter')
  await expect(chat.getByText('Which items may run out soon?')).toBeVisible()
  await expect(chat.getByText('Motion AI is checking the figures')).toHaveCount(0, { timeout: 60_000 })
  await expect(chat.getByText(/rule-based answer|AI-generated from your authorised data/)).toBeVisible()
  await page.screenshot({ path: `${shots}/ask-motion-ai.png` })
  await page.keyboard.press('Escape')
  await expect(chat).toHaveCount(0)
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Alerts & insights' }).click()
  await expect(page.getByRole('heading', { name: 'Alerts & insights' })).toBeVisible()
  await expect(page.getByLabel('Type')).toBeVisible()
  expect(errors).toEqual([])
})

test('My access shows the account roles, branches and permissions and collapses', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await login(page, 'cash.cen1')
  await page.goto('/pos')
  await page.getByRole('button', { name: 'Account menu' }).click()
  await page.getByRole('menuitem', { name: 'My access' }).click()
  const panel = page.getByRole('dialog', { name: 'My access' })
  await expect(panel).toBeVisible()
  await expect(panel.getByText('Cashier', { exact: true })).toBeVisible()
  await expect(panel.getByText(/6 of \d+ permissions/)).toBeVisible()
  await expect(panel.getByText('Operate the POS: sell, take payment, print receipt')).toBeVisible()
  await expect(panel.getByText('This account does not approve requests.')).toBeVisible()
  // the permissions the role does not have stay collapsed until asked for
  await expect(panel.getByText('View P&L, cash, payables, break-even, capital recovery')).toHaveCount(0)
  await panel.getByRole('button', { name: /Not included in your roles/ }).click()
  await expect(panel.getByText('View P&L, cash, payables, break-even, capital recovery')).toBeVisible()
  await panel.getByRole('button', { name: 'Collapse all' }).click()
  await expect(panel.getByText('Operate the POS: sell, take payment, print receipt')).toHaveCount(0)
  await panel.getByRole('button', { name: 'Expand all' }).click()
  await expect(panel.getByText('Operate the POS: sell, take payment, print receipt')).toBeVisible()
  await page.screenshot({ path: `${shots}/my-access.png` })
  await panel.getByRole('button', { name: 'Close' }).click()
  await expect(panel).toHaveCount(0)
  expect(errors).toEqual([])
})

test('Roles & permissions lists what every role does, and compares them', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await login(page, 'cash.cen1')
  await page.goto('/pos')
  await page.getByRole('button', { name: 'Account menu' }).click()
  await page.getByRole('menuitem', { name: 'Roles & permissions' }).click()
  const panel = page.getByRole('dialog', { name: 'Roles & permissions' })
  const roles = panel.getByTestId('all-roles')
  await expect(roles).toBeVisible()
  // every role is listed; the viewer's own role is open and marked, the rest are collapsed
  for (const name of ['Head Manager / Owner / CFO', 'General Manager', 'Accountant / Finance Manager', 'Operations Manager',
    'Inventory / Stock Manager', 'Branch Manager', 'Cashier', 'System Administrator (technical)']) {
    await expect(roles.getByRole('button', { name: new RegExp(name.replace(/[/()]/g, '.')) })).toBeVisible()
  }
  await expect(roles.getByText('Your role')).toBeVisible()
  await expect(roles.getByText('Search the audit trail')).toHaveCount(0)
  await roles.getByRole('button', { name: /Head Manager \/ Owner/ }).click()
  await expect(roles.getByText('Search the audit trail')).toBeVisible()
  await expect(roles.getByText('People with this role')).toHaveCount(0) // names are for user administrators only
  await page.screenshot({ path: `${shots}/roles-by-role.png` })
  await roles.getByRole('tab', { name: 'Compare roles' }).click()
  await expect(roles.getByRole('columnheader', { name: 'Branch Mgr' })).toBeVisible()
  await expect(roles.getByRole('rowheader', { name: 'Refunds' })).toBeVisible()
  await page.screenshot({ path: `${shots}/roles-compare.png` })
  // the same panel switches back to the viewer's own access
  await panel.getByRole('tab', { name: 'My access' }).click()
  await expect(page.getByRole('dialog', { name: 'My access' }).getByTestId('my-access')).toBeVisible()
  expect(errors).toEqual([])
})

test('Head Manager sees who holds each role', async ({ page }) => {
  await login(page, 'hm.owner')
  await page.getByRole('button', { name: 'Account menu' }).click()
  await page.getByRole('menuitem', { name: 'Roles & permissions' }).click()
  const roles = page.getByRole('dialog', { name: 'Roles & permissions' }).getByTestId('all-roles')
  await roles.getByRole('button', { name: /^Cashier/ }).click()
  await expect(roles.getByText('People with this role').first()).toBeVisible()
})

test('weighed refund accepts exactly the remaining quantity (0.300 sold, 0.100 refunded)', async ({ page }) => {
  await login(page, 'cash.cen1')
  await page.goto('/pos')
  const openShift = page.getByRole('button', { name: 'Open shift' })
  const scanner = page.getByLabel('Scan barcode or search products')
  await expect(openShift.or(scanner)).toBeVisible()
  if (await openShift.isVisible()) await openShift.click()
  await expect(scanner).toBeVisible()
  // The receipt lookup is stubbed so the quantities are exact: 0.3 - 0.1 is 0.19999… in floating point.
  await page.route('**/api/pos/receipts/**', (route) => route.fulfill({ json: {
    id: 999999, number: 'CEN-T1-999999', posted_at: new Date().toISOString(), total: '2400',
    lines: [{ id: 1, name: 'Tomatoes loose (TEST)', quantity: '0.300', refunded_qty: '0.100', uom: 'kg', unit_price: '8000' }],
  } }))
  await page.getByRole('tab', { name: 'Refunds' }).click()
  await page.getByLabel('Receipt number').fill('CEN-T1-999999')
  await page.getByRole('button', { name: 'Find' }).click()
  const box = page.getByLabel('Refund quantity for Tomatoes loose (TEST)')
  await box.fill('0.2')
  await expect(box).toHaveValue('0.2')
  await box.fill('0.201') // more than remains: refused, previous entry kept
  await expect(box).toHaveValue('0.2')
})

test('a page that cannot load shows a retry instead of a blank screen', async ({ page }) => {
  await login(page, 'acc.amina')
  await page.route(/\/assets\/Reports-[^/]+\.js$/, (route) => route.abort())
  await page.goto('/reports')
  // the first failure reloads once automatically; the second shows the error screen
  const alert = page.getByRole('alert')
  await expect(alert.getByText('This page could not be loaded')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible() // navigation still usable
  await page.screenshot({ path: `${shots}/page-load-error.png` })
  await page.unroute(/\/assets\/Reports-[^/]+\.js$/)
  await alert.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByRole('heading', { name: 'Reports' })).toBeVisible()
})
