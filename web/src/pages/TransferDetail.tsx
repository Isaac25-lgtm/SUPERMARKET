import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Truck, PackageCheck } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, CardHeader, ErrorBox, Field, Input, LoadingBlock, Notice, PageHeader, StatusChip, Table, Td, Th, useToast } from '../components/ui'
import { dateTime, humanize, money, num, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function TransferDetailPage() {
  const { id } = useParams()
  const { can, me } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({ queryKey: ['transfer', id], queryFn: () => api.get<R>(`/api/transfers/${id}`) })
  const [counts, setCounts] = useState<Record<number, { received: string; damaged: string }>>({})
  const [note, setNote] = useState('')
  const refresh = () => { qc.invalidateQueries({ queryKey: ['transfer', id] }); qc.invalidateQueries({ queryKey: ['transfers'] }) }
  const dispatch = useMutation({ mutationFn: () => api.post(`/api/transfers/${id}/dispatch`, {}), onSuccess: () => { toast('good', 'Dispatched — stock is now in transit.'); refresh() } })
  const receive = useMutation({
    mutationFn: () => api.post(`/api/transfers/${id}/receive`, {
      note: note || null,
      lines: q.data!.lines.filter((line: R) => num(line.dispatched_qty) > 0).map((line: R) => ({
        product_id: line.product_id,
        received_qty: counts[line.product_id]?.received,
        damaged_qty: counts[line.product_id]?.damaged || '0',
      })),
    }),
    onSuccess: () => { toast('good', 'Receipt confirmed.'); refresh() },
  })
  if (q.isLoading) return <LoadingBlock rows={6} />
  if (q.error) return <ErrorBox error={q.error} />
  const t = q.data!
  const inFrom = me?.branches.some((b) => b.id === t.from_branch_id) || me?.all_branches
  const inTo = me?.branches.some((b) => b.id === t.to_branch_id) || me?.all_branches
  const allCounted = t.lines.filter((line: R) => num(line.dispatched_qty) > 0)
    .every((line: R) => !!counts[line.product_id]?.received.trim())
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title={`Transfer ${t.ref}`} subtitle={`${t.from_branch} → ${t.to_branch} · ${t.reason ?? ''}`} actions={<StatusChip status={t.status} />} />
      <div className="grid gap-5 lg:grid-cols-[1fr_300px]">
        <div className="space-y-5">
          <Card>
            <CardHeader title="Items" />
            <Table><thead><tr><Th>Product</Th><Th align="right">Requested</Th><Th align="right">Dispatched</Th><Th align="right">Received</Th><Th align="right">Damaged</Th><Th align="right">Unresolved</Th></tr></thead>
              <tbody>{t.lines.map((l: R) => { const open = num(l.dispatched_qty) - num(l.received_qty) - num(l.damaged_qty); return (
                <tr key={l.id}><Td>{l.product_name}</Td><Td align="right">{qty(l.requested_qty, l.uom)}</Td><Td align="right">{l.dispatched_qty === null ? '—' : qty(l.dispatched_qty, l.uom)}</Td>
                  <Td align="right">{t.status === 'dispatched' && can('transfer.receive') && inTo ? <input aria-label={`Received ${l.product_name}`} inputMode="decimal" placeholder={String(num(l.dispatched_qty))} value={counts[l.product_id]?.received ?? ''} onChange={(e) => setCounts((c) => ({ ...c, [l.product_id]: { received: e.target.value, damaged: c[l.product_id]?.damaged ?? '' } }))} className="h-8 w-20 rounded-md border border-line px-2 text-right" /> : l.received_qty === null ? '—' : qty(l.received_qty, l.uom)}</Td>
                  <Td align="right">{t.status === 'dispatched' && can('transfer.receive') && inTo ? <input aria-label={`Damaged ${l.product_name}`} inputMode="decimal" placeholder="0" value={counts[l.product_id]?.damaged ?? ''} onChange={(e) => setCounts((c) => ({ ...c, [l.product_id]: { received: c[l.product_id]?.received ?? '', damaged: e.target.value } }))} className="h-8 w-20 rounded-md border border-line px-2 text-right" /> : qty(l.damaged_qty, l.uom)}</Td>
                  <Td align="right" className={t.received_at && open > 0 ? 'font-semibold text-warn' : ''}>{t.received_at ? qty(Math.max(0, open), l.uom) : '—'}</Td></tr>) })}</tbody></Table>
          </Card>
          {t.status === 'approved' && can('transfer.dispatch') && inFrom && (
            <Card className="p-5"><div className="flex items-center justify-between gap-3"><div><div className="font-semibold">Ready to dispatch</div><p className="text-[13px] text-muted">Dispatching moves the stock out of {t.from_branch} into transit. Stock is checked again now.</p></div><Button variant="primary" icon={<Truck className="size-4" />} loading={dispatch.isPending} onClick={() => dispatch.mutate()}>Dispatch</Button></div><ErrorBox error={dispatch.error} className="mt-3" /></Card>
          )}
          {t.status === 'dispatched' && can('transfer.receive') && inTo && (
            <Card className="space-y-3 p-5">
              <div className="font-semibold">Confirm what arrived</div>
              <p className="text-[13px] text-muted">Enter a count for every dispatched product. Enter zero if none arrived. Missing units stay in transit as a discrepancy.</p>
              <Field label="Note" htmlFor="rn"><Input id="rn" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. 2 cartons crushed" /></Field>
              <ErrorBox error={receive.error} />
              <Button variant="primary" icon={<PackageCheck className="size-4" />} disabled={!allCounted} loading={receive.isPending} onClick={() => receive.mutate()}>Confirm receipt</Button>
            </Card>
          )}
          {t.discrepancies.length > 0 && (
            <Card>
              <CardHeader title="Discrepancies" subtitle="Resolve via Stock → Discrepancies (authorised write-off or late arrival)" />
              <Table><thead><tr><Th>Type</Th><Th align="right">Qty</Th><Th align="right">Value at cost</Th><Th>Status</Th></tr></thead>
                <tbody>{t.discrepancies.map((d: R) => <tr key={d.id}><Td>{humanize(d.kind)}</Td><Td align="right">{qty(d.quantity)}</Td><Td align="right">{money(d.amount)}</Td><Td><StatusChip status={d.status} />{d.resolution && <span className="ml-1 text-[12px] text-muted">{humanize(d.resolution)}</span>}</Td></tr>)}</tbody></Table>
              <div className="p-4"><Link to="/stock?tab=discrepancies" className="text-[13px] font-medium text-accent hover:underline">Open discrepancy queue</Link></div>
            </Card>
          )}
        </div>
        <Card>
          <CardHeader title="Timeline" />
          <ol className="space-y-3 px-5 py-4 text-[13px]">
            <li><b>Requested</b> {dateTime(t.requested_at)}<div className="text-muted">{String(t.requested_by_name).replace(/\s*\(TEST.*\)/, '')}</div></li>
            {t.approval && <li><b>Approval</b> {t.approval.ref} <StatusChip status={t.approval.status} /></li>}
            {t.dispatched_at && <li><b>Dispatched</b> {dateTime(t.dispatched_at)}<div className="text-muted">{String(t.dispatched_by_name).replace(/\s*\(TEST.*\)/, '')}</div></li>}
            {t.received_at && <li><b>Received</b> {dateTime(t.received_at)}<div className="text-muted">{String(t.received_by_name).replace(/\s*\(TEST.*\)/, '')}</div></li>}
          </ol>
          {t.approval && <div className="border-t border-line p-4"><Link className="text-[13px] text-accent hover:underline" to={`/approvals/${t.approval_id}`}>View approval</Link></div>}
          <div className="border-t border-line p-4"><Notice>Internal transfers create no revenue and no journal entry; stock stays valued at cost while in transit.</Notice></div>
        </Card>
      </div>
    </div>
  )
}
