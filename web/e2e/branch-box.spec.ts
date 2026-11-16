import { expect, request as playwrightRequest, test, type Page } from '@playwright/test'
import { readFileSync, rmSync, writeFileSync } from 'node:fs'

// Outage drill against a real branch box (run by branch-edge/tools/outage_drill.py): the till is
// served by the box over HTTPS; head office is reached through a relay we can switch off.
const box = process.env.E2E_BOX_URL
const flag = process.env.E2E_WAN_FLAG ?? ''
const shots = process.env.E2E_SHOTS ?? 'e2e/screenshots'
const cashier = process.env.E2E_BOX_CASHIER ?? 'cash.cen2'
const manager = 'bm.joseph'
const tillName = process.env.E2E_BOX_REGISTER ?? 'CEN-T2'

test.skip(!box, 'needs a running branch box: python branch-edge/tools/outage_drill.py')
test.use({ baseURL: box, ignoreHTTPSErrors: true })

const creds: Record<string, string> = box ? JSON.parse(readFileSync(process.env.E2E_CREDS!, 'utf8')) : {}

async function sell(page: Page, search: string, product: RegExp) {
  await page.getByLabel('Scan barcode or search products').fill(search)
  await page.getByRole('button', { name: product }).first().click()
}

async function pay(page: Page): Promise<string> {
  await page.getByRole('button', { name: /^Charge/ }).click()
  await page.getByLabel('Cash received').fill('100000')
  await page.getByRole('button', { name: /Complete sale/ }).click()
  await expect(page.getByText('Sale complete')).toBeVisible()
  const number = (await page.getByText(/-B\d{6}/).first().textContent())!.match(/[A-Z]+-T\d+-B\d{6}/)![0]
  await page.getByRole('button', { name: 'New sale' }).click()
  return number
}

async function boxStatus(page: Page) {
  return (await page.request.get('/api/edge/status')).json() as Promise<{ online: boolean; pending: number; rejected: number }>
}

test('branch till keeps selling through an internet outage and uploads everything afterwards', async ({ page }) => {
  test.setTimeout(300_000)
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  rmSync(flag, { force: true })

  await page.goto('/login')
  await expect(page.getByText(/Branch till · /)).toBeVisible()
  await page.getByLabel('Username').fill(cashier)
  await page.getByLabel('Password').fill(creds[cashier])
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.waitForURL(/\/pos/)
  await page.getByLabel('Register').selectOption({ label: tillName })
  await page.getByRole('button', { name: 'Open shift' }).click()
  const pill = page.getByTestId('box-status')
  await expect(pill).toContainText('Online', { timeout: 30_000 })

  // Online through the box.
  await sell(page, 'Drinking water', /Drinking water 500ml/)
  await sell(page, 'Drinking water', /Drinking water 500ml/)
  const first = await pay(page)

  // ---- the internet goes off
  writeFileSync(flag, 'down')
  await expect(pill).toContainText('Offline', { timeout: 90_000 })
  await page.screenshot({ path: `${shots}/box-offline-till.png` })

  // A weighed item with a discount above the cashier limit, approved by the manager in person.
  await sell(page, 'Rice', /Rice - Super/)
  await page.getByLabel('Weight (kg)').fill('1.5')
  await page.getByRole('button', { name: /^Add/ }).click()
  await page.getByLabel('Discount percent').fill('20')
  await page.getByRole('button', { name: 'Manager override' }).click()
  await page.getByLabel('Manager username').fill(manager)
  await page.getByLabel('Manager password').fill(creds[manager])
  await page.getByLabel('Reason').fill('Torn sack, near expiry')
  await page.getByRole('button', { name: 'Approve discount' }).click()
  await expect(page.getByText('Manager override attached')).toBeVisible()
  await pay(page)
  await sell(page, 'Drinking water', /Drinking water 500ml/)
  await pay(page)

  // Refund one bottle from the first receipt, approved in person.
  await page.getByRole('tab', { name: 'Refunds' }).click()
  await page.getByLabel('Receipt number').fill(first)
  await page.getByRole('button', { name: 'Find' }).click()
  await page.getByLabel('Refund quantity for Drinking water 500ml').fill('1')
  await page.getByLabel('Reason').fill('Bottle was leaking')
  await page.getByLabel('Manager username').fill(manager)
  await page.getByLabel('Manager password').fill(creds[manager])
  await page.getByRole('button', { name: 'Approve and pay out' }).click()
  await expect(page.getByText('Refund complete')).toBeVisible()
  await page.screenshot({ path: `${shots}/box-offline-refund.png` })
  await page.getByRole('button', { name: 'New sale' }).click()
  await expect(pill).toContainText('saved on branch box')
  expect((await boxStatus(page)).pending).toBeGreaterThanOrEqual(3) // + the online sale if the line dropped before it uploaded

  // ---- the internet comes back: everything uploads by itself
  rmSync(flag, { force: true })
  await expect.poll(async () => { const s = await boxStatus(page); return s.online && s.pending === 0 && s.rejected === 0 },
    { timeout: 120_000, intervals: [2_000] }).toBe(true)
  await expect(pill).not.toContainText('waiting')

  // Close the shift (blind count) and let it upload too.
  await page.getByRole('tab', { name: 'Close shift' }).click()
  await page.getByLabel('Cash counted in drawer (UGX)').fill('200000')
  await page.getByRole('button', { name: 'Close shift' }).click()
  await expect(page.getByRole('button', { name: 'Open shift' })).toBeVisible()
  await expect.poll(async () => (await boxStatus(page)).pending, { timeout: 60_000, intervals: [2_000] }).toBe(0)

  // Head office has the sale, on the shift the box opened, under its own number.
  const atBox = await (await page.request.get(`/api/pos/receipts/${first}`)).json()
  expect(atBox.central_number).toBeTruthy()
  const central = await playwrightRequest.newContext({ baseURL: process.env.E2E_CENTRAL_URL })
  expect((await central.post('/api/auth/login', { data: { username: manager, password: creds[manager] } })).ok()).toBe(true)
  const sale = await (await central.get(`/api/pos/receipts/${atBox.central_number}`)).json()
  expect(sale.source).toBe('edge')
  expect(sale.shift_id).toBeTruthy()
  await central.dispose()
  expect(errors).toEqual([])
})
