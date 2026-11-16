import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ClipboardList, PackagePlus, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { CsvButton } from '../components/CsvButton'
import {
  Badge, Button, Card, Empty, ErrorBox, Field, Input, Kpi, LoadingBlock, Modal, PageHeader, Select, StatusChip, Table, Tabs,
  Td, Textarea, Th, useToast,
} from '../components/ui'
import { dateTime, humanize, money, num, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function StockPage() {
  const { branchId, can } = useAuth()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as 'products' | 'discrepancies') ?? 'products'
  const overview = useQuery({ queryKey: ['stock-overview', branchId], queryFn: () => api.get<R>('/api/stock/overview', { branch_id: branchId }) })
  const [creating, setCreating] = useState(false)
  const [counting, setCounting] = useState(false)
  return (
    <div>
      <PageHeader title="Stock & products" subtitle="Quantities come from completed stock movements in the ledger engine — not editable by hand."
        actions={<>
          {can('stock.count') && <Button icon={<ClipboardList className="size-4" />} onClick={() => setCounting(true)}>Record count</Button>}
          {can('reports.export') && <CsvButton path="/api/reports/stock.csv" filename="stock.csv" query={{ branch_id: branchId }} />}
          {can('catalog.manage') && <Button variant="primary" icon={<PackagePlus className="size-4" />} onClick={() => setCreating(true)}>New product</Button>}
        </>} />
      {overview.data && (
        <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {overview.data.branches.map((b: R) => <Kpi key={b.code} label={b.name} value={b.value === null ? `${b.sku_count} SKUs` : money(b.value)} hint={b.value === null ? 'in stock' : `${b.sku_count} SKUs in stock at cost`} />)}
          <Kpi label="In transit" value={overview.data.in_transit_value === null ? `${overview.data.transfers_in_transit} transfers` : money(overview.data.in_transit_value)} hint={`${overview.data.transfers_in_transit} transfer(s) on the road`} />
        </div>
      )}
      <div className="mb-4"><Tabs value={tab} onChange={(v) => setParams({ tab: v })} items={[{ value: 'products', label: 'Products' }, { value: 'discrepancies', label: 'Discrepancies', count: overview.data?.open_discrepancies.length }]} /></div>
      {tab === 'products' ? <Products /> : <Discrepancies />}
      {creating && <CreateProduct onClose={() => setCreating(false)} />}
      {counting && <CountModal onClose={() => setCounting(false)} />}
    </div>
  )
}

function Products() {
  const { branchId } = useAuth()
  const [params] = useSearchParams()
  const [q, setQ] = useState('')
  const [low, setLow] = useState(params.get('low') === '1')
  const [category, setCategory] = useState('')
  const cats = useQuery({ queryKey: ['categories'], queryFn: () => api.get<{ categories: R[] }>('/api/catalog/categories') })
  const list = useQuery({ queryKey: ['products', branchId, low, category], queryFn: () => api.get<R>('/api/catalog/products', { branch_id: branchId, low_stock: low, category: category || undefined }) })
  const items = useMemo(() => (list.data?.items ?? []).filter((i: R) => !q || i.name.toLowerCase().includes(q.toLowerCase()) || i.sku?.toLowerCase().includes(q.toLowerCase()) || i.barcode === q), [list.data, q])
  const branches: R[] = list.data?.branches ?? []
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
        <div className="relative min-w-[220px] flex-1"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" /><Input aria-label="Search products" className="pl-9" placeholder="Search name, SKU or barcode" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <Select aria-label="Category" className="w-auto" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All categories</option>{cats.data?.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select>
        <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={low} onChange={(e) => setLow(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />Low stock only</label>
      </div>
      {list.isLoading ? <LoadingBlock rows={8} /> : list.error ? <div className="p-4"><ErrorBox error={list.error} /></div> : items.length ? (
        <Table>
          <thead><tr><Th>Product</Th><Th>Category</Th><Th align="right">Shelf price</Th>{branches.map((b) => <Th key={b.code} align="right">{b.code}</Th>)}<Th align="right">Total</Th></tr></thead>
          <tbody>
            {items.map((i: R) => (
              <tr key={i.id} className="hover:bg-surface-2/50">
                <Td><Link to={`/stock/products/${i.id}`} className="font-medium hover:text-accent hover:underline">{i.name}</Link><div className="text-[12px] text-muted">{i.sku}{i.plu ? ` · PLU ${i.plu}` : i.barcode ? ` · ${i.barcode}` : ''}{i.perishable ? ' · perishable' : ''}</div></Td>
                <Td className="text-ink-2">{i.category}</Td>
                <Td align="right">{money(i.shelf_price)}{i.sold_by_weight ? '/kg' : ''}</Td>
                {branches.map((b) => { const v = num(i.stock[b.code]); const isLow = i.low_at?.includes(b.code); return <Td key={b.code} align="right" className={v < 0 ? 'font-semibold text-bad' : isLow ? 'font-medium text-warn' : ''}>{qty(v, i.uom)}{isLow && <span className="sr-only"> low</span>}</Td> })}
                <Td align="right" className="font-medium">{qty(i.stock_total, i.uom)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : <Empty title="No products match" />}
    </Card>
  )
}

function Discrepancies() {
  const { branchId, can } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const [status, setStatus] = useState('open')
  const q = useQuery({ queryKey: ['discrepancies', branchId, status], queryFn: () => api.get<{ discrepancies: R[] }>('/api/stock/discrepancies', { branch_id: branchId, status }) })
  const [act, setAct] = useState<{ d: R; mode: 'writeoff' | 'late' } | null>(null)
  return (
    <Card>
      <div className="flex items-center gap-2 border-b border-line p-3">
        <Select aria-label="Status" className="w-auto" value={status} onChange={(e) => setStatus(e.target.value)}><option value="open">Open</option><option value="pending_approval">Pending approval</option><option value="resolved">Resolved</option><option value="all">All</option></Select>
        <span className="text-[12.5px] text-muted">Discrepancies are never deleted — they are resolved through an authorised write-off, a late receipt, or a manager review.</span>
      </div>
      {q.isLoading ? <LoadingBlock /> : q.data?.discrepancies.length ? (
        <Table>
          <thead><tr><Th>Raised</Th><Th>Type</Th><Th>Branch</Th><Th>Product / source</Th><Th align="right">Qty</Th><Th align="right">Value</Th><Th>Status</Th><Th /></tr></thead>
          <tbody>{q.data.discrepancies.map((d) => (
            <tr key={d.id}>
              <Td>{dateTime(d.created_at)}</Td><Td>{humanize(d.kind)}</Td><Td>{d.branch_code}</Td>
              <Td>{d.product_name ?? '—'}<div className="text-[12px] text-muted">{d.source_type} {d.detail?.ref ?? d.source_id}{d.detail?.note ? ` · ${d.detail.note}` : ''}</div></Td>
              <Td align="right">{d.quantity === null ? '—' : qty(d.quantity)}</Td><Td align="right">{d.amount === null ? '—' : money(d.amount)}</Td>
              <Td><StatusChip status={d.status} />{d.resolution && <div className="text-[12px] text-muted">{humanize(d.resolution)}</div>}</Td>
              <Td align="right">
                {d.status === 'open' && ['transfer_shortage', 'transfer_damage'].includes(d.kind) && (
                  <div className="flex justify-end gap-1.5">
                    {d.kind === 'transfer_shortage' && can('transfer.receive') && <Button size="sm" onClick={() => setAct({ d, mode: 'late' })}>Arrived late</Button>}
                    {can('stock.adjust.request') && <Button size="sm" onClick={() => setAct({ d, mode: 'writeoff' })}>Request write-off</Button>}
                  </div>
                )}
                {d.kind === 'cash_variance' && d.status === 'open' && <Link className="text-[13px] text-accent hover:underline" to="/shifts">Review shift</Link>}
              </Td>
            </tr>))}</tbody>
        </Table>
      ) : <Empty title="No discrepancies" />}
      {act && <ResolveModal d={act.d} mode={act.mode} onClose={() => setAct(null)} onDone={() => { setAct(null); toast('good', act.mode === 'late' ? 'Late receipt recorded.' : 'Write-off sent for approval.'); qc.invalidateQueries({ queryKey: ['discrepancies'] }); qc.invalidateQueries({ queryKey: ['stock-overview'] }) }} />}
    </Card>
  )
}

function ResolveModal({ d, mode, onClose, onDone }: { d: R; mode: 'writeoff' | 'late'; onClose: () => void; onDone: () => void }) {
  const [quantity, setQuantity] = useState(String(num(d.quantity)))
  const [note, setNote] = useState('')
  const m = useMutation({
    mutationFn: () => mode === 'late'
      ? api.post(`/api/stock/discrepancies/${d.id}/receive-late`, { quantity, note })
      : api.post('/api/stock/writeoffs', { branch_id: d.branch_id, product_id: d.product_id, quantity: d.quantity, reason: note, discrepancy_id: d.id, kind: d.kind === 'transfer_damage' ? 'damage' : 'loss' }),
    onSuccess: onDone,
  })
  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title={mode === 'late' ? 'Record late arrival' : 'Request write-off'} description={`${d.product_name} · ${qty(d.quantity)} ${humanize(d.kind).toLowerCase()}`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={note.trim().length < (mode === 'late' ? 3 : 5)} onClick={() => m.mutate()}>Submit</Button></>}>
      <div className="space-y-3">
        {mode === 'late' && <Field label="Quantity that arrived" htmlFor="q"><Input id="q" inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} /></Field>}
        <Field label={mode === 'late' ? 'Note' : 'Reason for the loss (goes to the approver)'} htmlFor="n"><Textarea id="n" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        {mode === 'writeoff' && <p className="text-[12.5px] text-muted">On approval, the units move from transit to Lost & Found and are expensed as shrinkage (not cost of goods sold).</p>}
        <ErrorBox error={m.error} />
      </div>
    </Modal>
  )
}

function CreateProduct({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const cats = useQuery({ queryKey: ['categories'], queryFn: () => api.get<{ categories: R[] }>('/api/catalog/categories') })
  const [f, setF] = useState<R>({ name: '', category_id: '', shelf_price: '', cost_price: '', sku: '', barcode: '', sold_by_weight: false, plu: '', perishable: false, shelf_life_days: '', reorder_point: '10', reorder_qty: '40' })
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }))
  const m = useMutation({
    mutationFn: () => api.post('/api/catalog/products', { ...f, category_id: Number(f.category_id), sku: f.sku || null, barcode: f.barcode || null, plu: f.plu || null, shelf_life_days: f.shelf_life_days ? Number(f.shelf_life_days) : null }),
    onSuccess: () => { toast('good', 'Product created.'); qc.invalidateQueries({ queryKey: ['products'] }); onClose() },
  })
  return (
    <Modal open wide onOpenChange={(v) => !v && onClose()} title="New product" description="Shelf price includes VAT where the category is VAT-able (illustrative tax setup)."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!f.name || !f.category_id || !f.shelf_price || f.cost_price === ''} onClick={() => m.mutate()}>Create product</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" htmlFor="pn" className="sm:col-span-2"><Input id="pn" value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Category" htmlFor="pc"><Select id="pc" value={f.category_id} onChange={(e) => set('category_id', e.target.value)}><option value="">Choose…</option>{cats.data?.categories.map((c) => <option key={c.id} value={c.id}>{c.name}{c.vat ? ' (VAT)' : ''}</option>)}</Select></Field>
        <Field label="SKU" htmlFor="ps"><Input id="ps" value={f.sku} onChange={(e) => set('sku', e.target.value)} /></Field>
        <Field label="Shelf price (UGX)" htmlFor="pp"><Input id="pp" inputMode="numeric" value={f.shelf_price} onChange={(e) => set('shelf_price', e.target.value.replace(/[^\d]/g, ''))} /></Field>
        <Field label="Unit cost (UGX)" htmlFor="pk"><Input id="pk" inputMode="decimal" value={f.cost_price} onChange={(e) => set('cost_price', e.target.value.replace(/[^\d.]/g, ''))} /></Field>
        <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={f.sold_by_weight} onChange={(e) => set('sold_by_weight', e.target.checked)} className="size-4" />Sold by weight (per kg)</label>
        {f.sold_by_weight ? <Field label="PLU (5 digits, for scale labels)" htmlFor="pl"><Input id="pl" inputMode="numeric" maxLength={5} value={f.plu} onChange={(e) => set('plu', e.target.value.replace(/[^\d]/g, ''))} /></Field>
          : <Field label="Barcode" htmlFor="pb"><Input id="pb" inputMode="numeric" value={f.barcode} onChange={(e) => set('barcode', e.target.value.replace(/\s/g, ''))} /></Field>}
        <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={f.perishable} onChange={(e) => set('perishable', e.target.checked)} className="size-4" />Perishable (track expiry)</label>
        {f.perishable && <Field label="Shelf life (days)" htmlFor="pd"><Input id="pd" inputMode="numeric" value={f.shelf_life_days} onChange={(e) => set('shelf_life_days', e.target.value.replace(/[^\d]/g, ''))} /></Field>}
        <Field label="Reorder point" htmlFor="pr"><Input id="pr" inputMode="decimal" value={f.reorder_point} onChange={(e) => set('reorder_point', e.target.value)} /></Field>
        <Field label="Reorder quantity" htmlFor="pq"><Input id="pq" inputMode="decimal" value={f.reorder_qty} onChange={(e) => set('reorder_qty', e.target.value)} /></Field>
      </div>
      <ErrorBox error={m.error} className="mt-3" />
    </Modal>
  )
}

function CountModal({ onClose }: { onClose: () => void }) {
  const { me, branchId } = useAuth()
  const toast = useToast()
  const qc = useQueryClient()
  const [branch, setBranch] = useState<number>(branchId ?? me?.branches[0]?.id ?? 0)
  const list = useQuery({ queryKey: ['products', branch, false, ''], queryFn: () => api.get<R>('/api/catalog/products', { branch_id: branch }) })
  const [counts, setCounts] = useState<Record<number, string>>({})
  const [note, setNote] = useState('')
  const code = me?.branches.find((b) => b.id === branch)?.code ?? ''
  const m = useMutation({
    mutationFn: () => api.post<{ lines: R[] }>('/api/stock/counts', { branch_id: branch, note, counts: Object.entries(counts).filter(([, v]) => v !== '').map(([product_id, counted]) => ({ product_id: Number(product_id), counted })) }),
    onSuccess: (r) => { const v = r.lines.filter((l) => num(l.variance) !== 0).length; toast(v ? 'info' : 'good', v ? `${v} variance(s) recorded as discrepancies for review.` : 'Count matches the system.'); qc.invalidateQueries({ queryKey: ['discrepancies'] }); onClose() },
  })
  return (
    <Modal open wide onOpenChange={(v) => !v && onClose()} title="Record a stock count" description="Enter what you physically counted. Differences become discrepancies — stock is never silently adjusted."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!Object.values(counts).some((v) => v !== '')} onClick={() => m.mutate()}>Submit count</Button></>}>
      <div className="mb-3 flex gap-2">
        <Select aria-label="Branch" className="w-auto" value={branch} onChange={(e) => { setBranch(Number(e.target.value)); setCounts({}) }}>{me?.branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</Select>
        <Input aria-label="Note" placeholder="Note (e.g. aisle 3 cycle count)" value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="max-h-[50vh] overflow-y-auto rounded-lg border border-line">
        <Table><thead><tr><Th>Product</Th><Th align="right">Counted</Th></tr></thead>
          <tbody>{(list.data?.items ?? []).map((i: R) => <tr key={i.id}><Td>{i.name} <Badge className="ml-1">{i.uom}</Badge></Td><Td align="right"><input aria-label={`Counted ${i.name}`} inputMode="decimal" value={counts[i.id] ?? ''} onChange={(e) => setCounts((c) => ({ ...c, [i.id]: e.target.value.replace(/[^\d.]/g, '') }))} className="h-8 w-24 rounded-md border border-line px-2 text-right" /></Td></tr>)}</tbody></Table>
      </div>
      <p className="mt-2 text-[12px] text-muted">System quantities are hidden on purpose so counts are blind. Branch {code}.</p>
      <ErrorBox error={m.error} className="mt-3" />
    </Modal>
  )
}
