'use strict'

let token = null
let items = []
let basket = []
let saleKey = null
let submitting = false

const byId = (id) => document.getElementById(id)
const formatMoney = (amount) => Number(amount).toLocaleString('en-UG')
const element = (tag, contents, className) => {
  const node = document.createElement(tag)
  if (contents !== undefined) node.textContent = contents
  if (className) node.className = className
  return node
}

async function request(url, body) {
  const headers = { Accept: 'application/json' }
  if (token) headers.Authorization = 'Bearer ' + token
  if (body) headers['Content-Type'] = 'application/json'
  const response = await fetch(url, { headers, method: body ? 'POST' : 'GET', body: body && JSON.stringify(body) })
  const data = await response.json()
  if (!response.ok) throw new Error(data.message || 'Request failed (' + response.status + ')')
  return data
}

async function showStatus() {
  const badge = byId('sync')
  try {
    const status = await request('/edge/status')
    badge.textContent = status.state + (status.pending ? ' · ' + status.pending + ' pending' : '')
      + (status.rejected ? ' · ' + status.rejected + ' need review' : '')
    badge.className = 'pill ' + (status.online ? (status.pending || status.rejected ? 'pend' : 'on') : 'off')
  } catch {
    badge.textContent = 'Till status unavailable'
    badge.className = 'pill off'
  }
}

async function initialize() {
  const config = await request('/edge/registers')
  byId('brand').textContent = config.brand || 'Branch till'
  byId('branch').textContent = config.branch?.name || 'No snapshot yet. Connect once to sync.'
  byId('test').classList.toggle('hidden', !config.test_data)
  byId('reg').replaceChildren(...config.registers.map((register) => new Option(register.name, String(register.id))))
  void showStatus()
  setInterval(showStatus, 10000)
}

async function loadCatalog() {
  items = (await request('/edge/catalog')).items
  renderBasket()
}

async function signIn() {
  const button = byId('sign-in')
  button.disabled = true
  byId('lerr').textContent = ''
  try {
    const session = await request('/edge/login', {
      username: byId('u').value, password: byId('p').value, register_id: Number(byId('reg').value),
    })
    token = session.token
    byId('p').value = ''
    await loadCatalog()
    byId('who').textContent = session.full_name
    byId('login').classList.add('hidden')
    byId('pos').classList.remove('hidden')
    byId('scan').focus()
  } catch (error) {
    token = null
    byId('lerr').textContent = error.message
  } finally {
    button.disabled = false
  }
}

function roundedLine(line) {
  const scaled = BigInt(line.grams) * BigInt(String(line.item.price).split('.')[0])
  const whole = scaled / 1000n
  const remainder = scaled % 1000n
  return whole + (remainder > 500n || (remainder === 500n && whole % 2n === 1n) ? 1n : 0n)
}

function renderBasket() {
  const query = byId('scan').value.trim().toLowerCase()
  const results = items.filter((item) => !query || item.name.toLowerCase().includes(query)
    || (item.barcodes || [item.barcode]).includes(query) || item.plu === query)
  byId('grid').replaceChildren(...results.slice(0, 40).map((item) => {
    const button = element('button', undefined, 'item')
    button.append(element('b', item.name), element('span',
      formatMoney(item.price) + (item.sold_by_weight ? '/kg' : '') + ' · ' + item.available + ' left', 'muted'))
    button.addEventListener('click', () => addItem(item))
    button.disabled = submitting
    return button
  }))
  byId('basketEl').replaceChildren(...(basket.length ? basket.map((line, index) => {
    const row = element('div', undefined, 'line')
    const remove = element('button', 'Remove')
    remove.disabled = submitting
    remove.addEventListener('click', () => {
      basket.splice(index, 1)
      saleKey = null
      renderBasket()
    })
    const amount = element('span', formatMoney(roundedLine(line)) + ' ')
    amount.append(remove)
    row.append(element('span', line.item.name + ' × ' + line.grams / 1000), amount)
    return row
  }) : [element('p', 'Empty', 'muted')]))
  const total = basket.reduce((sum, line) => sum + roundedLine(line), 0n)
  byId('total').textContent = 'UGX ' + formatMoney(total)
  const cash = Number(byId('cash').value || 0)
  byId('change').textContent = cash >= Number(total) && total ? 'Change UGX ' + formatMoney(cash - Number(total)) : ''
  return total
}

function addItem(item) {
  if (submitting) return
  let grams = 1000
  if (item.sold_by_weight) {
    const weight = prompt('Weight in kg for ' + item.name, '1')
    if (!weight || !/^\d+(\.\d{1,3})?$/.test(weight)) return
    grams = Math.round(Number(weight) * 1000)
    if (!Number.isSafeInteger(grams) || grams <= 0) return
  }
  const line = basket.find((entry) => entry.item.id === item.id && !item.sold_by_weight)
  if (line) line.grams += grams
  else basket.push({ item, grams })
  saleKey = null
  renderBasket()
}

async function completeSale() {
  if (submitting || !basket.length) return
  const total = renderBasket()
  const cash = byId('cash')
  const tendered = cash.value || String(total)
  if (!/^\d+$/.test(tendered)) {
    byId('perr').textContent = 'Enter cash received in whole shillings.'
    return
  }
  const button = byId('complete-sale')
  submitting = button.disabled = cash.disabled = true
  renderBasket()
  byId('perr').textContent = ''
  try {
    saleKey ||= crypto.randomUUID()
    const receipt = await request('/edge/sales', {
      client_key: saleKey,
      lines: basket.map((line) => ({ product_id: line.item.id, quantity: (line.grams / 1000).toFixed(3) })),
      payments: tendered === '0' ? [] : [{ method: 'cash', amount: tendered }],
    })
    byId('receipt').replaceChildren(
      element('b', 'Receipt ' + receipt.local_receipt_no),
      element('br'),
      document.createTextNode('Total UGX ' + formatMoney(receipt.total) + ' · '
        + (receipt.status === 'pending' ? 'saved, will sync when online' : receipt.status === 'acked' ? 'synced' : 'needs review')),
    )
    basket = []
    cash.value = ''
    saleKey = null
    await loadCatalog()
    void showStatus()
  } catch (error) {
    byId('perr').textContent = error.message
  } finally {
    submitting = button.disabled = cash.disabled = false
    renderBasket()
  }
}

const barcodeForms = (code) => /^\d{12}$/.test(code) ? [code, '0' + code]
  : /^0\d{12}$/.test(code) ? [code, code.slice(1)] : [code]

byId('sign-in').addEventListener('click', signIn)
byId('complete-sale').addEventListener('click', completeSale)
byId('scan').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return
  const code = byId('scan').value.trim()
  if (!code) return
  const forms = barcodeForms(code)
  const item = items.find((entry) => (entry.barcodes || [entry.barcode]).some((value) => forms.includes(value))
    || entry.plu === code)
  if (item) {
    byId('scan').value = ''
    addItem(item)
  } else {
    byId('perr').textContent = 'Unknown barcode ' + code
  }
})
byId('scan').addEventListener('input', renderBasket)
byId('cash').addEventListener('input', () => { saleKey = null; renderBasket() })
initialize().catch((error) => { byId('lerr').textContent = error.message })
