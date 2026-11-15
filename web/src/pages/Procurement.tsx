import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, Truck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, Empty, ErrorBox, Field, Input, LoadingBlock, Modal, PageHeader, Select, StatusChip, Table, Tabs, Td, Textarea, Th } from '../components/ui'
import { date, dateTime, money, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function ProcurementPage() {
  const { can } = useAuth()
  const [tab, setTab] = useState<'requests' | 'orders'>('requests')
  const [creating, setCreating] = useState(false)
  const reqs = useQuery({ queryKey: ['requisitions'], queryFn: () => api.get<{ requests: R[] }>('/api/approvals/history', { action_type: 'purchase_request' }), enabled: can('approvals.inbox') })
  const mine = useQuery({ queryKey: ['my-requisitions'], queryFn: () => api.get<{ requests: R[] }>('/api/approvals/mine', { action_type: 'purchase_request' }), enabled: !can('approvals.inbox') })
  const orders = useQuery({ queryKey: ['orders'], queryFn: () => api.get<{ orders: R[] }>('/api/procurement/orders'), enabled: tab === 'orders' })
  const rows = (reqs.data ?? mine.data)?.requests ?? []
  return (
    <div>
      <PageHeader title="Procurement" subtitle="Requisition → operations review → finance verification → GM / CFO approval by amount → purchase order → receipt → invoice → payment."
        actions={can('procurement.request') && <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>New requisition</Button>} />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} items={[{ value: 'requests', label: 'Requisitions' }, { value: 'orders', label: 'Purchase orders & receiving' }]} /></div>
      {tab === 'requests' ? (
        <Card>
          {reqs.isLoading || mine.isLoading ? <LoadingBlock /> : rows.length ? (
            <Table><thead><tr><Th>Requisition</Th><Th>Branch</Th><Th>Status</Th><Th>Fulfilment</Th><Th align="right">Amount</Th><Th align="right">Raised</Th></tr></thead>
              <tbody>{rows.map((r) => <tr key={r.id} className="hover:bg-surface-2/50"><Td><Link to={`/approvals/${r.id}`} className="font-medium hover:text-accent hover:underline">{r.title}</Link><div className="text-[12px] text-muted">{r.ref}</div></Td><Td>{r.branch_name}</Td><Td><StatusChip status={r.status} label={r.status_label} /></Td><Td>{r.fulfilment_status ? <StatusChip status={r.fulfilment_status} /> : '—'}</Td><Td align="right">{money(r.amount)}</Td><Td align="right">{dateTime(r.created_at)}</Td></tr>)}</tbody></Table>
          ) : <Empty title="No requisitions yet" icon={<Truck className="size-6" />} />}
        </Card>
      ) : (
        <Card>
          {orders.isLoading ? <LoadingBlock /> : orders.data?.orders.length ? (
            <Table><thead><tr><Th>PO</Th><Th>Supplier</Th><Th>Branch</Th><Th>Delivery</Th><Th>Invoice</Th><Th align="right">Total</Th><Th align="right">Date</Th></tr></thead>
              <tbody>{orders.data.orders.map((o) => <tr key={o.id} className="hover:bg-surface-2/50"><Td><Link to={`/procurement/orders/${o.id}`} className="font-medium hover:text-accent hover:underline">{o.number}</Link><div className="text-[12px] text-muted">{o.reference}</div></Td><Td>{o.supplier}</Td><Td>{o.branch_code}</Td><Td><StatusChip status={o.shipment_state === 'waiting' ? 'pending' : o.shipment_state} label={o.shipment_state} /></Td><Td><StatusChip status={o.invoice_state} label={o.invoice_state} /></Td><Td align="right">{money(o.total)}</Td><Td align="right">{date(o.purchase_date)}</Td></tr>)}</tbody></Table>
          ) : <Empty title="No purchase orders" />}
        </Card>
      )}
      {creating && <NewRequisition onClose={() => setCreating(false)} />}
    </div>
  )
}

function NewRequisition({ onClose }: { onClose: () => void }) {
  const { me, branchId } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [branch, setBranch] = useState<number>(branchId ?? me?.branches[0]?.id ?? 0)
  const sup = useQuery({ queryKey: ['suppliers'], queryFn: () => api.get<{ suppliers: R[] }>('/api/procurement/suppliers') })
  const products = useQuery({ queryKey: ['products', branch, false, ''], queryFn: () => api.get<R>('/api/catalog/products', { branch_id: branch }) })
  const [supplier, setSupplier] = useState<number | ''>('')
  const [lines, setLines] = useState<{ product_id: number | ''; quantity: string; unit_cost: string }[]>([{ product_id: '', quantity: '', unit_cost: '' }])
  const [justification, setJustification] = useState('')
  const [urgency, setUrgency] = useState('normal')
  const ids = lines.map((l) => l.product_id).filter(Boolean).join(',')
  const evidence = useQuery({ queryKey: ['evidence', branch, ids], queryFn: () => api.get<{ evidence: R[] }>('/api/procurement/evidence', { branch_id: branch, product_ids: ids }), enabled: !!ids })
  useEffect(() => {
    // Prefill the preferred supplier of the first product.
    const first = products.data?.items.find((x: R) => x.id === lines[0].product_id)
    if (first?.preferred_supplier && !supplier) setSupplier(first.preferred_supplier)
  }, [lines, products.data, supplier])
  const total = lines.reduce((s, l) => {
    const p = products.data?.items.find((x: R) => x.id === l.product_id)
    return s + Number(l.quantity || 0) * Number(l.unit_cost || p?.cost_price || 0)
  }, 0)
  const m = useMutation({
    mutationFn: () => api.post<R>('/api/procurement/requisitions', { branch_id: branch, supplier_id: supplier, justification, urgency, lines: lines.filter((l) => l.product_id && l.quantity).map((l) => ({ product_id: l.product_id, quantity: l.quantity, unit_cost: l.unit_cost || null })) }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['requisitions'] }); navigate(`/approvals/${r.id}`) },
  })
  return (
    <Modal open wide onOpenChange={(v) => !v && onClose()} title="New purchase requisition" description="Amount bands pick the approval chain automatically. Evidence below is computed by the system and attached to the request."
      footer={<><span className="mr-auto self-center text-[13.5px]">Estimated total <b className="tabular">{money(total)}</b></span><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!supplier || justification.length < 5 || !lines.some((l) => l.product_id && l.quantity)} onClick={() => m.mutate()}>Submit for approval</Button></>}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Branch" htmlFor="rb"><Select id="rb" value={branch} onChange={(e) => setBranch(Number(e.target.value))}>{me?.branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</Select></Field>
        <Field label="Supplier" htmlFor="rs"><Select id="rs" value={supplier} onChange={(e) => setSupplier(Number(e.target.value))}><option value="">Choose…</option>{sup.data?.suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>
        <Field label="Urgency" htmlFor="ru"><Select id="ru" value={urgency} onChange={(e) => setUrgency(e.target.value)}>{['low', 'normal', 'high', 'critical'].map((u) => <option key={u}>{u}</option>)}</Select></Field>
      </div>
      <div className="mt-4 space-y-2">
        {lines.map((l, i) => {
          const p = products.data?.items.find((x: R) => x.id === l.product_id)
          return (
            <div key={i} className="flex items-end gap-2">
              <Field label={i === 0 ? 'Product' : ''} htmlFor={`rp${i}`} className="flex-1"><Select id={`rp${i}`} value={l.product_id} onChange={(e) => setLines((ls) => ls.map((x, j) => j === i ? { ...x, product_id: Number(e.target.value) } : x))}><option value="">Choose…</option>{products.data?.items.map((x: R) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
              <Field label={i === 0 ? `Qty${p ? ` (${p.uom})` : ''}` : ''} htmlFor={`rq${i}`} className="w-24"><Input id={`rq${i}`} inputMode="decimal" value={l.quantity} onChange={(e) => setLines((ls) => ls.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} /></Field>
              <Field label={i === 0 ? 'Unit cost' : ''} htmlFor={`rc${i}`} className="w-28"><Input id={`rc${i}`} inputMode="decimal" placeholder={p?.cost_price ? String(Math.round(Number(p.cost_price))) : ''} value={l.unit_cost} onChange={(e) => setLines((ls) => ls.map((x, j) => j === i ? { ...x, unit_cost: e.target.value } : x))} /></Field>
              <Button variant="ghost" aria-label="Remove line" disabled={lines.length === 1} onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}><Trash2 className="size-4" /></Button>
            </div>
          )
        })}
        <Button size="sm" icon={<Plus className="size-4" />} onClick={() => setLines((ls) => [...ls, { product_id: '', quantity: '', unit_cost: '' }])}>Add line</Button>
      </div>
      {evidence.data?.evidence.length ? (
        <div className="mt-4 rounded-lg border border-line">
          <Table><thead><tr><Th>Evidence</Th><Th align="right">On hand</Th><Th align="right">Sales/day</Th><Th align="right">Cover (days)</Th><Th>Other branches</Th></tr></thead>
            <tbody>{evidence.data.evidence.map((e) => <tr key={e.product_id}><Td>{e.product}</Td><Td align="right">{qty(e.on_hand)}</Td><Td align="right">{qty(e.daily_sales_14d)}</Td><Td align="right">{e.days_of_cover ?? '—'}</Td><Td className="text-[12.5px] text-muted">{Object.entries(e.stock_at_other_branches).map(([k, v]) => `${k}: ${qty(v)}`).join(' · ')}</Td></tr>)}</tbody></Table>
        </div>
      ) : null}
      <Field label="Justification" htmlFor="rj" className="mt-4"><Textarea id="rj" value={justification} onChange={(e) => setJustification(e.target.value)} placeholder="Why is this needed now? Could another branch transfer instead?" /></Field>
      <ErrorBox error={m.error} className="mt-3" />
    </Modal>
  )
}
