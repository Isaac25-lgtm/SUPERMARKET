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
  const links = await page.getByRole('navigation', { name: 'Main' }).getByRole('link').evaluateAll((els) => els.map((e) => (e as HTMLAnchorElement).pathname))
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
  if (await page.getByRole('button', { name: 'Open shift' }).isVisible()) {
    await page.getByRole('button', { name: 'Open shift' }).click()
  }
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
