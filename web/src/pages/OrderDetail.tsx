import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PackageCheck } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, CardHeader, Empty, ErrorBox, Field, Input, LoadingBlock, PageHeader, StatusChip, Table, Td, Th, useToast } from '../components/ui'
import { date, money, num, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function OrderDetailPage() {
  const { id } = useParams()
  const { can } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({ queryKey: ['po', id], queryFn: () => api.get<R>(`/api/procurement/orders/${id}`) })
  const [rows, setRows] = useState<Record<number, { qty: string; lot: string; expiry: string }>>({})
  const [note, setNote] = useState('')
  const m = useMutation({
    mutationFn: () => api.post(`/api/procurement/orders/${id}/receive`, {
      note: note || null,
      lines: q.data!.lines.filter((l: R) => num(l.pending) > 0).map((l: R) => ({ line_id: l.id, received_qty: rows[l.id]?.qty ?? l.pending, lot_ref: rows[l.id]?.lot || null, expiry_date: rows[l.id]?.expiry || null })),
    }),
    onSuccess: () => { toast('good', 'Goods received. Stock and the supplier bill draft were updated.'); setRows({}); qc.invalidateQueries({ queryKey: ['po', id] }) },
  })
  if (q.isLoading) return <LoadingBlock rows={6} />
  if (q.error) return <ErrorBox error={q.error} />
  const po = q.data!
  const pending = po.lines.some((l: R) => num(l.pending) > 0)
  const set = (lid: number, k: 'qty' | 'lot' | 'expiry', v: string) => setRows((r) => ({ ...r, [lid]: { qty: r[lid]?.qty ?? '', lot: r[lid]?.lot ?? '', expiry: r[lid]?.expiry ?? '', [k]: v } }))
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title={`Purchase order ${po.number}`} subtitle={`${po.supplier} · ${po.branch_name} · ${date(po.purchase_date)}${po.request ? ` · from ${po.request.ref}` : ''}`}
        actions={<><StatusChip status={po.shipment_state} label={`Delivery: ${po.shipment_state}`} /><StatusChip status={po.invoice_state} label={`Invoice: ${po.invoice_state}`} /></>} />
      <Card>
        <CardHeader title="Lines" subtitle={`Order total ${money(po.total)}`} />
        <Table><thead><tr><Th>Item</Th><Th align="right">Ordered</Th><Th align="right">Unit cost</Th><Th align="right">Received</Th><Th align="right">Pending</Th>{pending && can('stock.receive') && <><Th align="right">Receive now</Th><Th>Lot</Th><Th>Expiry</Th></>}</tr></thead>
          <tbody>{po.lines.map((l: R) => (
            <tr key={l.id}><Td>{l.description}</Td><Td align="right">{qty(l.quantity)}</Td><Td align="right">{money(l.unit_price)}</Td><Td align="right">{qty(l.received)}</Td><Td align="right" className={num(l.pending) > 0 ? 'text-warn' : ''}>{qty(l.pending)}</Td>
              {pending && can('stock.receive') && <>
                <Td align="right">{num(l.pending) > 0 && <input aria-label={`Receive ${l.description}`} inputMode="decimal" placeholder={String(num(l.pending))} value={rows[l.id]?.qty ?? ''} onChange={(e) => set(l.id, 'qty', e.target.value)} className="h-8 w-20 rounded-md border border-line px-2 text-right" />}</Td>
                <Td>{num(l.pending) > 0 && <input aria-label="Lot" value={rows[l.id]?.lot ?? ''} onChange={(e) => set(l.id, 'lot', e.target.value)} className="h-8 w-24 rounded-md border border-line px-2" />}</Td>
                <Td>{num(l.pending) > 0 && <input aria-label="Expiry date" type="date" value={rows[l.id]?.expiry ?? ''} onChange={(e) => set(l.id, 'expiry', e.target.value)} className="h-8 rounded-md border border-line px-2" />}</Td>
              </>}
            </tr>))}</tbody></Table>
        {pending && can('stock.receive') && (
          <div className="flex flex-wrap items-end gap-3 border-t border-line p-4">
            <Field label="Delivery note" htmlFor="dn" className="min-w-[240px] flex-1"><Input id="dn" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. 5kg short, driver signed" /></Field>
            <Button variant="primary" icon={<PackageCheck className="size-4" />} loading={m.isPending} onClick={() => m.mutate()}>Receive goods</Button>
          </div>
        )}
        <ErrorBox error={m.error} className="m-4" />
      </Card>
      <Card className="mt-5">
        <CardHeader title="Supplier invoices" subtitle="Drafted automatically from received quantities; the accountant verifies the 3-way match and posts." />
        {po.invoices.length ? <ul className="divide-y divide-line">{po.invoices.map((i: R) => <li key={i.id} className="flex items-center justify-between px-5 py-3 text-[13.5px]"><Link to={`/payables/invoices/${i.id}`} className="font-medium hover:text-accent hover:underline">{i.number ?? `Draft bill #${i.id}`}</Link><span className="flex items-center gap-3"><span className="tabular">{money(i.total)}</span><StatusChip status={i.state} /></span></li>)}</ul> : <Empty title="No invoice yet" >It appears after goods are received.</Empty>}
      </Card>
    </div>
  )
}
