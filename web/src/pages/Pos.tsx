import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  Banknote, CreditCard, LogOut, Minus, Percent, Plus, Printer, RotateCcw, ScanBarcode, Search, ShieldCheck, Smartphone,
  Trash2, Wifi, WifiOff, X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, uuid } from '../api/client'
import { useAuth } from '../auth/auth'
import {
  Badge, Button, Card, CardHeader, Empty, ErrorBox, Field, Input, Modal, Notice, Select, StatusChip, Tabs, Textarea,
  errorText, useToast,
} from '../components/ui'
import { dateTime, money, num, qty } from '../lib/format'
import { lineAmount, unitAfterDiscount } from '../lib/money'
import { barcodeCandidates, beep, listenForScanner } from '../lib/scanner'

interface Item { id: number; name: string; sku: string | null; barcode: string | null; barcodes?: string[]; plu: string | null; category: string | null
  uom: string; sold_by_weight: boolean; price: string; vat: boolean; on_hand: string }
interface Catalog { register: { id: number; name: string; branch_id: number }; items: Item[]; policy: { cashier_max_discount_pct: number; receipt_footer?: string } }
interface Shift { id: number; register_id: number; register_name: string; branch_name: string; opened_at: string; opening_float: string
  expected_cash_now?: string; sales: number; refunds: number; net_total?: string; interrupted: boolean }
interface Line { product: Item; quantity: number; discount_pct: number; discount_text?: string }
interface Payment { method: 'cash' | 'mobile_money' | 'card'; amount: string; reference?: string }

// Codex H2: one idempotency key per basket. It survives a failed request and a page reload,
// so re-submitting the same basket replays the receipt instead of charging twice.
const PENDING_TTL_MS = 15 * 60_000
function basketKey(registerId: number, lines: Line[], overrideId: number | null): string {
  const sig = JSON.stringify([overrideId, lines.map((l) => [l.product.id, l.quantity, l.discount_pct])])
  const slot = `motion.pos.pending.${registerId}`
  try {
    const saved = JSON.parse(localStorage.getItem(slot) ?? 'null') as { sig: string; key: string; at: number } | null
    if (saved && saved.sig === sig && Date.now() - saved.at < PENDING_TTL_MS) return saved.key
    const key = uuid()
    localStorage.setItem(slot, JSON.stringify({ sig, key, at: Date.now() }))
    return key
  } catch {
    return uuid()
  }
}
function clearBasketKey(registerId: number) {
  try { localStorage.removeItem(`motion.pos.pending.${registerId}`) } catch { /* storage unavailable */ }
}

const lineTotal = (l: Line) => lineAmount(l.quantity, unitAfterDiscount(num(l.product.price), l.discount_pct))

export default function PosPage() {
  const shiftQ = useQuery({ queryKey: ['pos-shift'], queryFn: () => api.get<{ shift: Shift | null }>('/api/pos/shift') })
  const [tab, setTab] = useState<'sell' | 'refund' | 'shift'>('sell')
  if (shiftQ.isLoading) return <div className="p-6 text-muted">Loading till…</div>
  if (shiftQ.error) return <ErrorBox error={shiftQ.error} />
  const shift = shiftQ.data?.shift
  if (!shift) return <OpenShift />
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-[20px] font-semibold tracking-tight">{shift.register_name}</h1>
          <span className="text-[13px] text-muted">{shift.branch_name} · shift opened {dateTime(shift.opened_at)}</span>
          {shift.interrupted && <Badge tone="bad">Open more than 14 h</Badge>}
        </div>
        <div className="flex items-center gap-3">
          <ConnectionPill />
          <Tabs value={tab} onChange={setTab} items={[{ value: 'sell', label: 'Sell' }, { value: 'refund', label: 'Refunds' }, { value: 'shift', label: 'Close shift' }]} />
        </div>
      </div>
      {tab === 'sell' && <Till shift={shift} />}
      {tab === 'refund' && <Refunds shift={shift} />}
      {tab === 'shift' && <CloseShift shift={shift} onDone={() => setTab('sell')} />}
    </div>
  )
}

function ConnectionPill() {
  const [online, setOnline] = useState(navigator.onLine)
  useEffect(() => {
    const up = () => setOnline(true); const down = () => setOnline(false)
    window.addEventListener('online', up); window.addEventListener('offline', down)
    return () => { window.removeEventListener('online', up); window.removeEventListener('offline', down) }
  }, [])
  return online
    ? <span className="inline-flex items-center gap-1.5 rounded-full bg-good-soft px-2.5 py-1 text-[12px] font-medium text-good"><Wifi className="size-3.5" />Online · central</span>
    : <span title="Use the branch edge till for offline selling" className="inline-flex items-center gap-1.5 rounded-full bg-bad-soft px-2.5 py-1 text-[12px] font-medium text-bad"><WifiOff className="size-3.5" />Offline — switch to branch till</span>
}

function OpenShift() {
  const { me } = useAuth()
  const qc = useQueryClient()
  const [register, setRegister] = useState<number | ''>(me?.registers[0]?.id ?? '')
  const [float, setFloat] = useState('200000')
  const m = useMutation({
    mutationFn: () => api.post('/api/pos/shifts', { register_id: register, opening_float: float || '0' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pos-shift'] }),
  })
  return (
    <div className="mx-auto mt-6 max-w-md">
      <Card>
        <CardHeader title="Open your shift" subtitle="Count the cash in the drawer before you start selling." icon={<Banknote className="size-4" />} />
        <form className="space-y-4 p-5" onSubmit={(e) => { e.preventDefault(); m.mutate() }}>
          <Field label="Register" htmlFor="reg">
            <Select id="reg" value={register} onChange={(e) => setRegister(Number(e.target.value))}>
              {me?.registers.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </Select>
          </Field>
          <Field label="Opening float counted (UGX)" htmlFor="float" hint="Any difference from the previous close is recorded as a float top-up or drop.">
            <Input id="float" inputMode="numeric" value={float} onChange={(e) => setFloat(e.target.value.replace(/[^\d]/g, ''))} className="h-12 text-lg" />
          </Field>
          <ErrorBox error={m.error} />
          <Button type="submit" variant="primary" size="xl" className="w-full" loading={m.isPending} disabled={!register}>Open shift</Button>
        </form>
      </Card>
    </div>
  )
}

function Till({ shift }: { shift: Shift }) {
  const qc = useQueryClient()
  const toast = useToast()
  const cat = useQuery({ queryKey: ['till-catalog', shift.register_id], queryFn: () => api.get<Catalog>(`/api/pos/registers/${shift.register_id}/catalog`) })
  const [lines, setLines] = useState<Line[]>([])
  const [scan, setScan] = useState('')
  const [category, setCategory] = useState<string>('all')
  const [payOpen, setPayOpen] = useState(false)
  const [overrideOpen, setOverrideOpen] = useState(false)
  const [overrideId, setOverrideId] = useState<number | null>(null)
  const [receipt, setReceipt] = useState<Record<string, unknown> | null>(null)
  const [weighFor, setWeighFor] = useState<Item | null>(null)
  const scanRef = useRef<HTMLInputElement>(null)
  const items = useMemo(() => cat.data?.items ?? [], [cat.data])
  const maxDisc = num(cat.data?.policy.cashier_max_discount_pct)
  useEffect(() => { scanRef.current?.focus() }, [payOpen, receipt, weighFor])

  const categories = useMemo(() => ['all', ...Array.from(new Set(items.map((i) => i.category ?? 'Other')))], [items])
  const visible = useMemo(() => {
    const q = scan.trim().toLowerCase()
    return items.filter((i) => (category === 'all' || (i.category ?? 'Other') === category)
      && (!q || i.name.toLowerCase().includes(q) || i.sku?.toLowerCase().includes(q) || (i.barcodes ?? []).includes(q) || i.plu === q)).slice(0, 60)
  }, [items, scan, category])

  const add = (item: Item, quantity = 1) => {
    setOverrideId(null)
    setLines((ls) => {
      const idx = ls.findIndex((l) => l.product.id === item.id && !item.sold_by_weight)
      if (idx >= 0) return ls.map((l, i) => (i === idx ? { ...l, quantity: l.quantity + quantity } : l))
      return [...ls, { product: item, quantity, discount_pct: 0 }]
    })
  }

  /** One path for every scan: search box Enter, or the page-wide scanner listener. */
  const handleCode = async (raw: string, fromSearchBox: boolean) => {
    const code = raw.trim()
    if (!code) return
    const forms = barcodeCandidates(code)
    const local = items.find((i) => (i.barcodes ?? []).some((b) => forms.includes(b)) || i.plu === code || i.sku === code)
    if (local) {
      beep(true)
      if (local.sold_by_weight) setWeighFor(local); else add(local)
      setScan('')
      return
    }
    try {
      // Scale labels (price/weight embedded) and anything not in the cached catalog.
      const r = await api.get<{ id: number; scale?: { quantity?: string } | null }>('/api/catalog/lookup', { code })
      const item = items.find((i) => i.id === r.id)
      if (!item) throw new Error(`${code} is not sold at this till.`)
      beep(true)
      if (r.scale?.quantity) add(item, num(r.scale.quantity)); else if (item.sold_by_weight) setWeighFor(item); else add(item)
      setScan('')
    } catch (err) {
      if (fromSearchBox && visible.length === 1 && !/^\d{6,}$/.test(code)) { beep(true); add(visible[0]); setScan(''); return }
      beep(false)
      if (!fromSearchBox || !visible.length || /^\d{6,}$/.test(code)) {
        toast('bad', errorText(err))
        setScan('')
      }
    }
  }
  const onScan = (e: React.FormEvent) => { e.preventDefault(); void handleCode(scan, true) }
  const handleRef = useRef(handleCode)
  handleRef.current = handleCode
  // Scans work even when the cursor is not in the search box (e.g. after tapping the basket).
  useEffect(() => listenForScanner((code) => { if (!payOpen && !weighFor && !overrideOpen) void handleRef.current(code, false) }),
    [payOpen, weighFor, overrideOpen])

  const total = lines.reduce((s, l) => s + lineTotal(l), 0)
  const discount = lines.reduce((s, l) => s + lineAmount(l.quantity, num(l.product.price)) - lineTotal(l), 0)
  const needsOverride = lines.some((l) => l.discount_pct > maxDisc) && !overrideId

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
      <div className="min-w-0 space-y-3">
        <form onSubmit={onScan} className="relative">
          <ScanBarcode className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-muted" aria-hidden />
          <input ref={scanRef} value={scan} onChange={(e) => setScan(e.target.value)} aria-label="Scan barcode or search products"
            placeholder="Scan barcode, type PLU, or search…" autoComplete="off"
            className="h-14 w-full rounded-xl border border-line-strong bg-surface pl-12 pr-4 text-[16px] shadow-sm focus:border-accent focus:outline-none focus:ring-4 focus:ring-accent/15" />
        </form>
        <div className="flex gap-1.5 overflow-x-auto pb-1" role="tablist" aria-label="Categories">
          {categories.map((c) => (
            <button key={c} role="tab" aria-selected={category === c} onClick={() => setCategory(c)}
              className={clsx('whitespace-nowrap rounded-full border px-3.5 py-1.5 text-[13px] font-medium', category === c ? 'border-accent bg-accent-soft text-accent-ink' : 'border-line bg-surface text-ink-2 hover:bg-surface-2')}>
              {c === 'all' ? 'All' : c}
            </button>
          ))}
        </div>
        {cat.error && <ErrorBox error={cat.error} />}
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-4">
          {visible.map((i) => {
            const out = num(i.on_hand) <= 0
            return (
              <button key={i.id} onClick={() => (i.sold_by_weight ? setWeighFor(i) : add(i))} disabled={out}
                className="group flex min-h-[92px] flex-col justify-between rounded-xl border border-line-strong bg-surface p-3 text-left shadow-[var(--shadow-card)] transition hover:border-accent/50 hover:shadow-md active:scale-[0.99] disabled:opacity-45">
                <span className="line-clamp-2 text-[13.5px] font-medium leading-snug">{i.name}</span>
                <span className="mt-2 flex items-end justify-between gap-1">
                  <span className="tabular text-[15px] font-semibold">{money(i.price, { currency: false })}{i.sold_by_weight && <span className="text-[11px] font-normal text-muted">/kg</span>}</span>
                  <span className={clsx('text-[11.5px]', out ? 'font-semibold text-bad' : 'text-muted')}>{out ? 'Out' : qty(i.on_hand, i.uom)}</span>
                </span>
              </button>
            )
          })}
        </div>
        {!visible.length && !cat.isLoading && <Empty title="No matching products" icon={<Search className="size-6" />}>Check the spelling or scan the barcode.</Empty>}
      </div>

      <Card className="flex h-fit flex-col lg:sticky lg:top-20">
        <CardHeader title="Basket" subtitle={`${lines.length} line${lines.length === 1 ? '' : 's'}`}
          action={lines.length > 0 && <Button size="sm" variant="ghost" onClick={() => { setLines([]); setOverrideId(null) }} icon={<X className="size-4" />}>Clear</Button>} />
        <ul className="max-h-[46vh] divide-y divide-line overflow-y-auto">
          {lines.map((l, idx) => (
            <li key={`${l.product.id}-${idx}`} className="px-4 py-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-[14px] font-medium">{l.product.name}</div>
                  <div className="text-[12px] text-muted">{money(l.product.price)}{l.product.sold_by_weight ? '/kg' : ' each'}{l.discount_pct > 0 && <span className="ml-1 font-medium text-warn">−{l.discount_pct}%</span>}</div>
                </div>
                <div className="tabular text-[14.5px] font-semibold">{money(lineTotal(l), { currency: false })}</div>
              </div>
              <div className="mt-2 flex items-center gap-1.5">
                {l.product.sold_by_weight ? (
                  <button onClick={() => setWeighFor(l.product)} className="rounded-lg border border-line px-2.5 py-1.5 text-[13px] font-medium">{qty(l.quantity, 'kg')}</button>
                ) : (
                  <>
                    <button aria-label="Decrease" className="grid size-9 place-items-center rounded-lg border border-line hover:bg-surface-2" onClick={() => setLines((ls) => ls.map((x, i) => i === idx ? { ...x, quantity: Math.max(1, x.quantity - 1) } : x))}><Minus className="size-4" /></button>
                    <span className="tabular w-8 text-center text-[14px] font-medium">{l.quantity}</span>
                    <button aria-label="Increase" className="grid size-9 place-items-center rounded-lg border border-line hover:bg-surface-2" onClick={() => setLines((ls) => ls.map((x, i) => i === idx ? { ...x, quantity: x.quantity + 1 } : x))}><Plus className="size-4" /></button>
                  </>
                )}
                <label className="ml-auto flex items-center gap-1 text-[12px] text-muted">
                  <Percent className="size-3.5" aria-hidden /><span className="sr-only">Discount percent</span>
                  <input inputMode="decimal" value={l.discount_text ?? (l.discount_pct || '')} placeholder="0" onChange={(e) => { const t = e.target.value.trim(); if (!/^\d{0,3}(\.\d{0,2})?$/.test(t) || num(t) > 100) return; setOverrideId(null); setLines((ls) => ls.map((x, i) => i === idx ? { ...x, discount_pct: num(t), discount_text: t } : x)) }}
                    className="h-9 w-14 rounded-lg border border-line px-2 text-right text-[13px]" />
                </label>
                <button aria-label={`Remove ${l.product.name}`} className="grid size-9 place-items-center rounded-lg text-bad hover:bg-bad-soft" onClick={() => setLines((ls) => ls.filter((_, i) => i !== idx))}><Trash2 className="size-4" /></button>
              </div>
            </li>
          ))}
          {!lines.length && <li><Empty title="Scan an item to start" icon={<ScanBarcode className="size-6" />}>Keyboard barcode scanners work in the search box.</Empty></li>}
        </ul>
        <div className="border-t border-line px-4 py-3.5">
          {discount > 0 && <div className="flex justify-between text-[13px] text-muted"><span>Discounts</span><span className="tabular">−{money(discount)}</span></div>}
          <div className="flex items-baseline justify-between"><span className="text-[14px] font-medium">Total</span><span className="tabular text-[28px] font-semibold tracking-tight">{money(total)}</span></div>
          <p className="text-[11.5px] text-muted">Prices include VAT where applicable. Final total is confirmed by the server.</p>
          {needsOverride && (
            <div className="mt-3"><Notice tone="warn">Discount above {maxDisc}% needs a manager. <button className="font-semibold underline" onClick={() => setOverrideOpen(true)}>Manager override</button></Notice></div>
          )}
          {overrideId && <div className="mt-2 text-[12.5px] text-good"><ShieldCheck className="mr-1 inline size-4" />Manager override attached</div>}
          <Button variant="primary" size="xl" className="mt-3 w-full" disabled={!lines.length || needsOverride} onClick={() => setPayOpen(true)}>
            Charge {money(total)}
          </Button>
        </div>
      </Card>

      {weighFor && <WeighModal item={weighFor} onClose={() => setWeighFor(null)} onAdd={(q) => { const existing = lines.findIndex((l) => l.product.id === weighFor.id); if (existing >= 0) setLines((ls) => ls.map((l, i) => i === existing ? { ...l, quantity: q } : l)); else add(weighFor, q); setWeighFor(null) }} />}
      {overrideOpen && <OverrideModal registerId={shift.register_id} lines={lines} onClose={() => setOverrideOpen(false)} onApproved={(id) => { setOverrideId(id); setOverrideOpen(false) }} />}
      {payOpen && <PayModal total={total} lines={lines} registerId={shift.register_id} overrideId={overrideId} onClose={() => setPayOpen(false)}
        onDone={(r) => { setPayOpen(false); setLines([]); setOverrideId(null); setReceipt(r); qc.invalidateQueries({ queryKey: ['till-catalog'] }); qc.invalidateQueries({ queryKey: ['pos-shift'] }) }} />}
      {receipt && <ReceiptModal receipt={receipt} onClose={() => setReceipt(null)} />}
    </div>
  )
}

function WeighModal({ item, onClose, onAdd }: { item: Item; onClose: () => void; onAdd: (q: number) => void }) {
  const [kg, setKg] = useState('')
  const n = num(kg)
  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title={item.name} description={`${money(item.price)} per kg · enter the weight from the scale`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={n <= 0} onClick={() => onAdd(Math.round(n * 1000) / 1000)}>Add {n > 0 ? money(lineAmount(n, num(item.price))) : ''}</Button></>}>
      <form onSubmit={(e) => { e.preventDefault(); if (n > 0) onAdd(Math.round(n * 1000) / 1000) }}>
        <Field label="Weight (kg)" htmlFor="kg" hint="Up to 3 decimals, e.g. 1.250. Scale labels starting with 2 are read automatically when scanned.">
          <Input id="kg" autoFocus inputMode="decimal" value={kg} onChange={(e) => setKg(e.target.value.replace(/[^\d.]/g, ''))} className="h-12 text-lg" />
        </Field>
        <div className="mt-3 flex flex-wrap gap-2">{['0.25', '0.5', '1', '1.5', '2', '5'].map((v) => <Button key={v} type="button" size="sm" onClick={() => setKg(v)}>{v} kg</Button>)}</div>
      </form>
    </Modal>
  )
}

function OverrideModal({ registerId, lines, onClose, onApproved }: { registerId: number; lines: Line[]; onClose: () => void; onApproved: (id: number) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [reason, setReason] = useState('')
  const m = useMutation({
    mutationFn: () => api.post<{ approval_id: number; approved_by: string }>('/api/pos/overrides', {
      register_id: registerId, manager_username: username, manager_password: password, reason,
      lines: lines.map((l) => ({ product_id: l.product.id, quantity: l.quantity, discount_pct: l.discount_pct })),
    }),
    onSuccess: (r) => onApproved(r.approval_id),
  })
  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title="Manager override" description="The manager enters their own credentials. The approval is single-use, tied to this basket, and audited."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!username || !password || reason.length < 3} onClick={() => m.mutate()}>Approve discount</Button></>}>
      <div className="space-y-3">
        <Field label="Manager username" htmlFor="mu"><Input id="mu" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} /></Field>
        <Field label="Manager password" htmlFor="mp"><Input id="mp" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        <Field label="Reason" htmlFor="mr"><Input id="mr" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Damaged packaging, near expiry" /></Field>
        <ErrorBox error={m.error} />
      </div>
    </Modal>
  )
}

function PayModal({ total, lines, registerId, overrideId, onClose, onDone }: {
  total: number; lines: Line[]; registerId: number; overrideId: number | null; onClose: () => void; onDone: (r: Record<string, unknown>) => void
}) {
  const [saleKey] = useState(() => basketKey(registerId, lines, overrideId))
  const [payments, setPayments] = useState<Payment[]>([])
  const [cash, setCash] = useState('')
  const [method, setMethod] = useState<'mobile_money' | 'card'>('mobile_money')
  const [ref, setRef] = useState('')
  const [nonCashAmt, setNonCashAmt] = useState('')
  const nonCash = payments.reduce((s, p) => s + num(p.amount), 0)
  const due = Math.max(0, total - nonCash)
  const change = Math.max(0, num(cash) - due)
  const m = useMutation({
    mutationFn: () => api.post<Record<string, unknown>>('/api/pos/sales', {
      register_id: registerId, idempotency_key: saleKey, override_approval_id: overrideId,
      lines: lines.map((l) => ({ product_id: l.product.id, quantity: l.quantity, discount_pct: l.discount_pct })),
      payments: [...payments, ...(due > 0 ? [{ method: 'cash', amount: String(num(cash)) }] : [])],
    }),
    onSuccess: (r) => { clearBasketKey(registerId); onDone(r) },
  })
  const quick = Array.from(new Set([due, Math.ceil(due / 1000) * 1000, Math.ceil(due / 5000) * 5000, Math.ceil(due / 10000) * 10000, Math.ceil(due / 50000) * 50000].filter((v) => v > 0))).slice(0, 5)
  const canPay = due === 0 || num(cash) >= due
  return (
    <Modal open onOpenChange={(v) => !v && !m.isPending && onClose()} title={`Take payment · ${money(total)}`} wide
      footer={<><Button onClick={onClose} disabled={m.isPending}>Back</Button><Button variant="primary" size="lg" loading={m.isPending} disabled={!canPay} onClick={() => m.mutate()}>Complete sale{change > 0 ? ` · change ${money(change)}` : ''}</Button></>}>
      <div className="grid gap-5 md:grid-cols-2">
        <div>
          <h4 className="mb-2 flex items-center gap-2 text-[13px] font-semibold"><Banknote className="size-4" />Cash {nonCash > 0 && <span className="font-normal text-muted">(remaining {money(due)})</span>}</h4>
          <Input inputMode="numeric" aria-label="Cash received" value={cash} onChange={(e) => setCash(e.target.value.replace(/[^\d]/g, ''))} placeholder={String(due)} className="h-14 text-2xl" autoFocus />
          <div className="mt-2 flex flex-wrap gap-2">{quick.map((v) => <Button key={v} size="lg" onClick={() => setCash(String(v))}>{money(v, { currency: false })}</Button>)}</div>
          <div className="mt-4 rounded-xl bg-surface-2 p-4">
            <div className="flex justify-between text-[13px] text-muted"><span>Cash due</span><span className="tabular">{money(due)}</span></div>
            <div className="mt-1 flex items-baseline justify-between"><span className="font-medium">Change</span><span className="tabular text-2xl font-semibold text-good">{money(change)}</span></div>
          </div>
        </div>
        <div>
          <h4 className="mb-2 flex items-center gap-2 text-[13px] font-semibold"><Smartphone className="size-4" />Mobile money / card</h4>
          <div className="grid grid-cols-2 gap-2">
            <Button variant={method === 'mobile_money' ? 'subtle' : 'secondary'} onClick={() => setMethod('mobile_money')} icon={<Smartphone className="size-4" />}>Mobile money</Button>
            <Button variant={method === 'card' ? 'subtle' : 'secondary'} onClick={() => setMethod('card')} icon={<CreditCard className="size-4" />}>Card</Button>
          </div>
          <div className="mt-2 grid grid-cols-[1fr_1.2fr] gap-2">
            <Input inputMode="numeric" aria-label="Amount" placeholder={String(due)} value={nonCashAmt} onChange={(e) => setNonCashAmt(e.target.value.replace(/[^\d]/g, ''))} />
            <Input aria-label="Transaction reference" placeholder="Transaction ref." value={ref} onChange={(e) => setRef(e.target.value)} />
          </div>
          <Button className="mt-2 w-full" disabled={!ref.trim() || (num(nonCashAmt || due) <= 0) || num(nonCashAmt || due) > due}
            onClick={() => { setPayments((p) => [...p, { method, amount: String(num(nonCashAmt || due)), reference: ref.trim() }]); setRef(''); setNonCashAmt('') }}>Add {method === 'card' ? 'card' : 'mobile money'} payment</Button>
          <p className="mt-2 text-[12px] text-muted">Recorded as a <b>pending-verification claim</b>: the accountant reconciles it against the provider statement. No live payment integration.</p>
          {payments.length > 0 && (
            <ul className="mt-3 divide-y divide-line rounded-lg border border-line">
              {payments.map((p, i) => <li key={i} className="flex items-center justify-between px-3 py-2 text-[13px]"><span>{p.method === 'card' ? 'Card' : 'Mobile money'} · {p.reference}</span><span className="flex items-center gap-2"><span className="tabular font-medium">{money(p.amount)}</span><button aria-label="Remove payment" onClick={() => setPayments((x) => x.filter((_, j) => j !== i))}><X className="size-4 text-muted" /></button></span></li>)}
            </ul>
          )}
        </div>
      </div>
      <ErrorBox error={m.error} className="mt-4" />
    </Modal>
  )
}

export function ReceiptModal({ receipt, onClose }: { receipt: Record<string, unknown>; onClose: () => void }) {
  const r = receipt as Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any
  const cashPay = (r.payments as Record<string, string>[]).find((p) => p.method === 'cash')
  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title={r.kind === 'refund' ? 'Refund complete' : 'Sale complete'}
      footer={<><Button icon={<Printer className="size-4" />} onClick={() => window.print()}>Print receipt</Button><Button variant="primary" autoFocus onClick={onClose}>New sale</Button></>}>
      <div id="receipt" className="mx-auto max-w-[320px] font-mono text-[12.5px] leading-relaxed">
        <div className="text-center"><div className="text-[14px] font-bold">{r.brand}</div><div>{r.branch_name}</div><div>{r.register_name} · {r.number}</div><div>{dateTime(r.posted_at)}</div>{r.test_data && <div className="mt-1 font-bold">*** TEST DATA – NOT A FISCAL RECEIPT ***</div>}</div>
        <div className="my-2 border-t border-dashed border-line-strong" />
        {(r.lines as Record<string, string>[]).map((l) => (
          <div key={l.id}><div>{l.name}</div><div className="flex justify-between"><span>{qty(l.quantity, l.uom)} × {money(l.unit_price, { currency: false })}{num(l.discount_pct) > 0 ? ` (−${num(l.discount_pct)}%)` : ''}</span><span>{money(l.amount, { currency: false })}</span></div></div>
        ))}
        <div className="my-2 border-t border-dashed border-line-strong" />
        <div className="flex justify-between font-bold"><span>TOTAL</span><span>{money(r.total)}</span></div>
        <div className="flex justify-between"><span>incl. VAT</span><span>{money(r.total_tax)}</span></div>
        {(r.payments as Record<string, string>[]).map((p, i) => <div key={i} className="flex justify-between"><span>{p.method.replace('_', ' ')}{p.reference ? ` ${p.reference}` : ''}</span><span>{money(p.tendered ?? p.amount)}</span></div>)}
        {cashPay?.change && num(cashPay.change) > 0 && <div className="flex justify-between font-bold"><span>CHANGE</span><span>{money(cashPay.change)}</span></div>}
        <div className="my-2 border-t border-dashed border-line-strong" />
        <div className="text-center">Served by {String(r.cashier_name).replace(/\s*\(TEST.*\)/, '')}<br />{r.footer}<br />Ref {r.journal_entry}</div>
      </div>
      <style>{'@media print { body * { visibility: hidden } #receipt, #receipt * { visibility: visible } #receipt { position: absolute; left: 0; top: 0 } }'}</style>
    </Modal>
  )
}

// Quantities are compared in whole thousandths (the till's precision), never as floats:
// 0.300 - 0.100 must leave exactly 0.200 refundable.
const milli = (v: string | number | null | undefined) => Math.round(num(v) * 1000)

function Refunds({ shift }: { shift: Shift }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [number, setNumber] = useState('')
  const [sale, setSale] = useState<Record<string, any> | null>(null) // eslint-disable-line @typescript-eslint/no-explicit-any
  // Raw text per line, so decimals like "0.5" can be typed; parsed and capped when sent.
  const [pick, setPick] = useState<Record<number, string>>({})
  const [reason, setReason] = useState('')
  const [findErr, setFindErr] = useState<unknown>(null)
  const [receipt, setReceipt] = useState<Record<string, unknown> | null>(null)
  const mine = useQuery({ queryKey: ['my-refunds'], queryFn: () => api.get<{ requests: Record<string, any>[] }>('/api/approvals/mine', { action_type: 'refund' }), refetchInterval: 30_000 }) // eslint-disable-line @typescript-eslint/no-explicit-any
  const find = async (e: React.FormEvent) => {
    e.preventDefault(); setFindErr(null); setSale(null); setPick({})
    try { setSale(await api.get(`/api/pos/receipts/${encodeURIComponent(number.trim())}`)) } catch (err) { setFindErr(err) }
  }
  const request = useMutation({
    mutationFn: () => api.post('/api/pos/refunds/requests', { sale_id: sale!.id, reason, lines: Object.entries(pick).filter(([, q]) => milli(q) > 0).map(([line_id, q]) => ({ line_id: Number(line_id), quantity: (milli(q) / 1000).toFixed(3) })) }),
    onSuccess: () => { toast('good', 'Refund request sent to the branch manager.'); setSale(null); setReason(''); setNumber(''); qc.invalidateQueries({ queryKey: ['my-refunds'] }) },
  })
  const execute = useMutation({
    mutationFn: (approvalId: number) => api.post<Record<string, unknown>>('/api/pos/refunds', { approval_id: approvalId, register_id: shift.register_id, idempotency_key: `refund-approval-${approvalId}` }),
    onSuccess: (r) => { setReceipt(r); qc.invalidateQueries({ queryKey: ['my-refunds'] }); qc.invalidateQueries({ queryKey: ['pos-shift'] }) },
    onError: (e) => toast('bad', errorText(e)),
  })
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card>
        <CardHeader title="Request a refund" subtitle="Refunds must reference the original receipt and are approved by a manager." icon={<RotateCcw className="size-4" />} />
        <div className="space-y-4 p-5">
          <form onSubmit={find} className="flex gap-2">
            <Input aria-label="Receipt number" placeholder="Receipt number, e.g. CEN-T1-000123" value={number} onChange={(e) => setNumber(e.target.value)} />
            <Button type="submit">Find</Button>
          </form>
          <ErrorBox error={findErr} />
          {sale && (
            <>
              <div className="text-[13px] text-muted">{sale.number} · {dateTime(sale.posted_at)} · {money(sale.total)}</div>
              <ul className="divide-y divide-line rounded-lg border border-line">
                {(sale.lines as Record<string, string>[]).map((l) => {
                  const leftM = milli(l.quantity) - milli(l.refunded_qty)
                  const left = leftM / 1000
                  return (
                    <li key={l.id} className="flex items-center justify-between gap-3 px-3 py-2 text-[13px]">
                      <div><div className="font-medium">{l.name}</div><div className="text-muted">{qty(l.quantity, l.uom)} sold · {qty(left, l.uom)} refundable · {money(l.unit_price)}</div></div>
                      <input aria-label={`Refund quantity for ${l.name}`} inputMode="decimal" disabled={leftM <= 0} value={pick[Number(l.id)] ?? ''} placeholder="0"
                        onChange={(e) => { const v = e.target.value.trim(); if (/^\d*\.?\d{0,3}$/.test(v) && (v === '' || milli(v) <= leftM)) setPick((p) => ({ ...p, [Number(l.id)]: v })) }} className="h-9 w-20 rounded-lg border border-line px-2 text-right" />
                    </li>
                  )
                })}
              </ul>
              <Field label="Reason" htmlFor="rr"><Textarea id="rr" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What is wrong with the goods?" /></Field>
              <ErrorBox error={request.error} />
              <Button variant="primary" loading={request.isPending} disabled={reason.trim().length < 5 || !Object.values(pick).some((q) => milli(q) > 0)} onClick={() => request.mutate()}>Send for approval</Button>
            </>
          )}
        </div>
      </Card>
      <Card>
        <CardHeader title="My refund requests" subtitle="Approved refunds can be paid out in cash from this till once." />
        {mine.isLoading ? <div className="p-5 text-muted">Loading…</div> : mine.data?.requests.length ? (
          <ul className="divide-y divide-line">
            {mine.data.requests.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-3 px-5 py-3">
                <div><div className="text-[13.5px] font-medium">{r.title}</div><div className="text-[12px] text-muted">{r.ref} · {money(r.amount)} · {dateTime(r.created_at)}</div></div>
                {r.status === 'approved' ? <Button size="sm" variant="primary" loading={execute.isPending} onClick={() => execute.mutate(r.id)}>Pay out</Button> : <StatusChip status={r.status} label={r.status_label} />}
              </li>
            ))}
          </ul>
        ) : <Empty title="No refund requests yet" />}
      </Card>
      {receipt && <ReceiptModal receipt={receipt} onClose={() => setReceipt(null)} />}
    </div>
  )
}

function CloseShift({ shift, onDone }: { shift: Shift; onDone: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [counted, setCounted] = useState('')
  const [drop, setDrop] = useState('')
  const [note, setNote] = useState('')
  const m = useMutation({
    mutationFn: () => api.post<Record<string, string>>(`/api/pos/shifts/${shift.id}/close`, { counted_cash: counted || '0', cash_drop: drop || '0', note: note || null }),
    onSuccess: (r) => {
      const v = num(r.variance)
      toast(v === 0 ? 'good' : 'info', v === 0 ? 'Shift closed. Cash balanced.' : `Shift closed with a ${v < 0 ? 'shortage' : 'surplus'} of ${money(Math.abs(v))}. Your manager will review it.`)
      qc.invalidateQueries({ queryKey: ['pos-shift'] }); onDone()
    },
  })
  return (
    <div className="mx-auto max-w-xl">
      <Card>
        <CardHeader title="Close shift" subtitle={`${shift.sales} sales · ${shift.refunds} refunds · count the drawer before you see the expected total`} icon={<LogOut className="size-4" />} />
        <form className="space-y-4 p-5" onSubmit={(e) => { e.preventDefault(); m.mutate() }}>
          <Notice>Count the drawer <b>before</b> looking at the expected amount. The system compares your count with expected cash and records any difference.</Notice>
          <Field label="Cash counted in drawer (UGX)" htmlFor="cc"><Input id="cc" inputMode="numeric" value={counted} onChange={(e) => setCounted(e.target.value.replace(/[^\d]/g, ''))} className="h-12 text-lg" /></Field>
          <Field label="Cash moved to the safe (UGX)" htmlFor="cd" hint="The rest stays in the drawer as the next shift's float."><Input id="cd" inputMode="numeric" value={drop} onChange={(e) => setDrop(e.target.value.replace(/[^\d]/g, ''))} /></Field>
          <Field label="Note (optional)" htmlFor="cn"><Input id="cn" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          <ErrorBox error={m.error instanceof ApiError ? m.error : m.error} />
          <Button type="submit" variant="primary" size="lg" className="w-full" disabled={counted === ''} loading={m.isPending}>Close shift</Button>
        </form>
      </Card>
    </div>
  )
}
