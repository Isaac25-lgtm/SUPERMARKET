import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'

// Phone-size checks (390 x 844): the till, the My access panel and the Ask Motion AI floater
// must fit without horizontal scrolling.
const creds: Record<string, string> = JSON.parse(readFileSync(process.env.E2E_CREDS!, 'utf8'))
const shots = process.env.E2E_SHOTS ?? 'e2e/screenshots'

test.use({ viewport: { width: 390, height: 844 } })

async function login(page: Page, user: string) {
  await page.context().clearCookies()
  await page.goto('/login')
  await page.getByLabel('Username').fill(user)
  await page.getByLabel('Password').fill(creds[user])
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'))
}

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
}

test('phone: cashier till and My access fit the screen', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await login(page, 'cash.cen1')
  await page.goto('/pos')
  await expect(page.getByLabel('Scan barcode or search products').or(page.getByText('Open your shift'))).toBeVisible()
  await noHorizontalScroll(page)
  await page.screenshot({ path: `${shots}/phone-pos.png` })
  await page.getByRole('button', { name: 'Account menu' }).click()
  await page.getByRole('menuitem', { name: 'My access' }).click()
  const panel = page.getByRole('dialog', { name: 'My access' })
  await expect(panel.getByText('Cashier', { exact: true })).toBeVisible()
  await noHorizontalScroll(page)
  await page.screenshot({ path: `${shots}/phone-my-access.png` })
  expect(errors).toEqual([])
})

test('phone: manager dashboard and Ask Motion AI fit the screen', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await login(page, 'bm.sarah')
  await expect(page.getByRole('button', { name: 'Ask Motion AI' })).toBeVisible()
  await noHorizontalScroll(page)
  await page.screenshot({ path: `${shots}/phone-dashboard.png` })
  await page.getByRole('button', { name: 'Ask Motion AI' }).click()
  await expect(page.getByRole('dialog', { name: 'Ask Motion AI' })).toBeVisible()
  await noHorizontalScroll(page)
  await page.screenshot({ path: `${shots}/phone-ask-motion-ai.png` })
  expect(errors).toEqual([])
})
