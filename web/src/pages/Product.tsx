import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Badge, Button, Card, CardHeader, Empty, ErrorBox, Field, Input, Kpi, LoadingBlock, PageHeader, StatusChip, Table, Td, Th, useToast } from '../components/ui'
import { date, dateTime, humanize, money, num, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function ProductPage() {
  const { id } = useParams()
  const { can, me, branchId } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const p = useQuery({ queryKey: ['product', id], queryFn: () => api.get<R>(`/api/catalog/products/${id}`) })
  const moveBranch = branchId ?? me?.branches[0]?.id
  const moves = useQuery({ queryKey: ['movements', id, moveBranch], queryFn: () => api.get<{ movements: R[] }>('/api/stock/movements', { branch_id: moveBranch, product_id: id, days: 30 }), enabled: !!moveBranch && can('stock.view') })
  const [price, setPrice] = useState('')
  const [reason, setReason] = useState('')
  const [rp, setRp] = useState('')
  const priceM = useMutation({
    mutationFn: () => api.post(`/api/catalog/products/${id}/price`, { new_price: price, reason }),
    onSuccess: () => { toast('good', 'Price updated and recorded in the audit trail.'); setPrice(''); setReason(''); qc.invalidateQueries({ queryKey: ['product', id] }) },
  })
  const rpM = useMutation({
    mutationFn: () => api.patch(`/api/catalog/products/${id}`, { reorder_point: rp }),
    onSuccess: () => { toast('good', 'Reorder point saved.'); setRp(''); qc.invalidateQueries({ queryKey: ['product', id] }) },
  })
  if (p.isLoading) return <LoadingBlock rows={8} />
  if (p.error) return <ErrorBox error={p.error} />
  const d = p.data!
  return (
    <div>
      <PageHeader title={d.name} subtitle={<>{d.category} · {d.sku}{d.barcode ? ` · barcode ${d.barcode}` : ''}{d.plu ? ` · PLU ${d.plu}` : ''} · sold per {d.uom === 'kg' ? 'kg' : 'unit'}</>}
        actions={<>{d.perishable && <Badge tone="warn">Perishable · {d.shelf_life_days} days</Badge>}{d.vat ? <Badge>VAT (illustrative)</Badge> : <Badge>No VAT (illustrative)</Badge>}</>} />
      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Shelf price" value={money(d.shelf_price)} hint={d.uom === 'kg' ? 'per kg' : 'each'} />
        {d.cost_price !== undefined && <Kpi label="Average unit cost" value={money(d.cost_price)} hint={`margin ${d.shelf_price ? ((1 - num(d.cost_price) / num(d.shelf_price)) * 100).toFixed(1) : '—'}% of shelf`} />}
        {Object.entries(d.stock as Record<string, string>).map(([code, v]) => <Kpi key={code} label={`On hand · ${code}`} value={qty(v, d.uom)} tone={num(v) <= num(d.reorder_point) ? 'warn' : 'neutral'} hint={`reorder at ${qty(d.reorder_point, d.uom)}`} />)}
      </div>
      <div className="grid gap-5 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Recent movements" subtitle="Last 30 days, selected branch" />
          {moves.data?.movements.length ? (
            <Table><thead><tr><Th>Date</Th><Th>Type</Th><Th>From → To</Th><Th align="right">Qty</Th><Th>By</Th></tr></thead>
              <tbody>{moves.data.movements.slice(0, 40).map((m) => <tr key={m.id}><Td>{date(m.effective_date)}</Td><Td><StatusChip status={m.direction === 'in' ? 'received' : 'dispatched'} label={humanize(m.kind)} /></Td><Td className="text-[12.5px] text-ink-2">{m.from_name} → {m.to_name}</Td><Td align="right" className={m.direction === 'in' ? 'text-good' : ''}>{m.direction === 'in' ? '+' : '−'}{qty(m.qty, d.uom)}</Td><Td className="text-[12.5px] text-muted">{String(m.created_by ?? '').replace(/\s*\(TEST.*\)/, '')}</Td></tr>)}</tbody></Table>
          ) : <Empty title="No movements in this period" />}
        </Card>
        <div className="space-y-5">
          {can('catalog.price') && (
            <Card className="p-5">
              <div className="text-[14px] font-semibold">Change shelf price</div>
              <p className="mt-1 text-[12.5px] text-muted">Applies to all branches immediately. Offline tills flag sales at the old price as conflicts.</p>
              <div className="mt-3 space-y-3">
                <Field label="New price (UGX)" htmlFor="np"><Input id="np" inputMode="numeric" value={price} onChange={(e) => setPrice(e.target.value.replace(/[^\d]/g, ''))} /></Field>
                <Field label="Reason" htmlFor="nr"><Input id="nr" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. supplier price increase" /></Field>
                <ErrorBox error={priceM.error} />
                <Button variant="primary" loading={priceM.isPending} disabled={!price || reason.length < 3} onClick={() => priceM.mutate()}>Update price</Button>
              </div>
            </Card>
          )}
          {can('catalog.manage') && (
            <Card className="p-5">
              <div className="text-[14px] font-semibold">Reorder point</div>
              <div className="mt-3 flex gap-2"><Input aria-label="Reorder point" inputMode="decimal" placeholder={String(num(d.reorder_point))} value={rp} onChange={(e) => setRp(e.target.value)} /><Button loading={rpM.isPending} disabled={!rp} onClick={() => rpM.mutate()}>Save</Button></div>
              <ErrorBox error={rpM.error} className="mt-2" />
            </Card>
          )}
          <Card>
            <CardHeader title="Price history" />
            {d.price_history.length ? <ul className="divide-y divide-line">{d.price_history.map((h: R, i: number) => <li key={i} className="px-5 py-2.5 text-[13px]"><div className="flex justify-between"><span>{money(h.old_price)} → <b>{money(h.new_price)}</b></span><span className="text-[12px] text-muted">{dateTime(h.changed_at)}</span></div><div className="text-[12px] text-muted">{String(h.full_name).replace(/\s*\(TEST.*\)/, '')} · {h.reason}</div></li>)}</ul> : <Empty title="No price changes yet" />}
          </Card>
        </div>
      </div>
      {d.batches.length > 0 && (
        <Card className="mt-5">
          <CardHeader title="Batches & expiry" subtitle="Remaining per batch is an ESTIMATE: current stock is allocated to the newest receipts first." />
          <Table><thead><tr><Th>Branch</Th><Th>Lot</Th><Th>Received</Th><Th>Expiry</Th><Th align="right">Received qty</Th><Th align="right">Est. remaining</Th></tr></thead>
            <tbody>{d.batches.map((b: R) => { const expired = b.expiry_date && b.expiry_date < new Date().toISOString().slice(0, 10); return <tr key={b.id}><Td>{b.branch_code}</Td><Td mono>{b.lot_ref ?? '—'}</Td><Td>{date(b.received_at)}</Td><Td className={expired && num(b.estimated_remaining) > 0 ? 'font-medium text-bad' : ''}>{date(b.expiry_date)}</Td><Td align="right">{qty(b.received_qty, d.uom)}</Td><Td align="right">{qty(b.estimated_remaining, d.uom)}</Td></tr> })}</tbody></Table>
        </Card>
      )}
      <p className="mt-4 text-[12.5px] text-muted"><Link className="text-accent hover:underline" to="/stock">← Back to stock</Link></p>
    </div>
  )
}
