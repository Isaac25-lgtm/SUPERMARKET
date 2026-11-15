import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeftRight, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, Empty, ErrorBox, Field, Input, LoadingBlock, Modal, PageHeader, Select, StatusChip, Table, Td, Textarea, Th } from '../components/ui'
import { dateTime, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function TransfersPage() {
  const { can } = useAuth()
  const [status, setStatus] = useState('')
  const [creating, setCreating] = useState(false)
  const q = useQuery({ queryKey: ['transfers', status], queryFn: () => api.get<{ transfers: R[] }>('/api/transfers', { status: status || undefined }) })
  return (
    <div>
      <PageHeader title="Inter-branch transfers" subtitle="Stock moves origin → in transit → destination. Transfers never count as sales or profit."
        actions={can('transfer.request') && <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>Request transfer</Button>} />
      <Card>
        <div className="border-b border-line p-3"><Select aria-label="Status" className="w-auto" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>{['requested', 'approved', 'dispatched', 'received', 'received_with_discrepancy', 'closed', 'rejected'].map((s) => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
        </Select></div>
        {q.isLoading ? <LoadingBlock /> : q.data?.transfers.length ? (
          <Table><thead><tr><Th>Reference</Th><Th>Route</Th><Th>Items</Th><Th>Requested</Th><Th>Status</Th></tr></thead>
            <tbody>{q.data.transfers.map((t) => (
              <tr key={t.id} className="hover:bg-surface-2/50">
                <Td><Link to={`/transfers/${t.id}`} className="font-medium hover:text-accent hover:underline">{t.ref}</Link></Td>
                <Td>{t.from_branch} → {t.to_branch}</Td>
                <Td className="text-[12.5px] text-ink-2">{t.lines.map((l: R) => `${l.product_name} × ${qty(l.requested_qty, l.uom)}`).join(', ')}</Td>
                <Td>{dateTime(t.requested_at)}<div className="text-[12px] text-muted">{String(t.requested_by_name).replace(/\s*\(TEST.*\)/, '')}</div></Td>
                <Td><StatusChip status={t.status} /></Td>
              </tr>))}</tbody></Table>
        ) : <Empty title="No transfers" icon={<ArrowLeftRight className="size-6" />} />}
      </Card>
      {creating && <NewTransfer onClose={() => setCreating(false)} />}
    </div>
  )
}

function NewTransfer({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const all = useQuery({ queryKey: ['all-branches'], queryFn: () => api.get<{ directory: R[] }>('/api/branches') })
  const { me } = useAuth()
  const branchesAll = all.data?.directory ?? me?.branches ?? []
  const [from, setFrom] = useState<number | ''>('')
  const [to, setTo] = useState<number | ''>(me?.branches[0]?.id ?? '')
  const products = useQuery({ queryKey: ['products-all'], queryFn: () => api.get<R>('/api/catalog/products') })
  const [lines, setLines] = useState<{ product_id: number | ''; quantity: string }[]>([{ product_id: '', quantity: '' }])
  const [reason, setReason] = useState('')
  const m = useMutation({
    mutationFn: () => api.post<R>('/api/transfers', { from_branch_id: from, to_branch_id: to, reason, lines: lines.filter((l) => l.product_id && l.quantity).map((l) => ({ product_id: l.product_id, quantity: l.quantity })) }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['transfers'] }); navigate(`/transfers/${r.id}`) },
  })
  return (
    <Modal open wide onOpenChange={(v) => !v && onClose()} title="Request a transfer" description="The Operations Manager approves; the sending branch dispatches; the receiving branch confirms the count."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!from || !to || from === to || reason.length < 3 || !lines.some((l) => l.product_id && l.quantity)} onClick={() => m.mutate()}>Submit request</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="From branch (sends stock)" htmlFor="tf"><Select id="tf" value={from} onChange={(e) => setFrom(Number(e.target.value))}><option value="">Choose…</option>{branchesAll.map((b: R) => <option key={b.id} value={b.id}>{b.name}</option>)}</Select></Field>
        <Field label="To branch (receives)" htmlFor="tt"><Select id="tt" value={to} onChange={(e) => setTo(Number(e.target.value))}>{me?.branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</Select></Field>
      </div>
      <div className="mt-4 space-y-2">
        {lines.map((l, i) => {
          const p = products.data?.items.find((x: R) => x.id === l.product_id)
          const fromCode = branchesAll.find((b: R) => b.id === from)?.code
          return (
            <div key={i} className="flex items-end gap-2">
              <Field label={i === 0 ? 'Product' : ''} htmlFor={`p${i}`} className="flex-1"><Select id={`p${i}`} value={l.product_id} onChange={(e) => setLines((ls) => ls.map((x, j) => j === i ? { ...x, product_id: Number(e.target.value) } : x))}><option value="">Choose product…</option>{products.data?.items.map((x: R) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
              <Field label={i === 0 ? 'Quantity' : ''} htmlFor={`q${i}`} className="w-28"><Input id={`q${i}`} inputMode="decimal" value={l.quantity} onChange={(e) => setLines((ls) => ls.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} /></Field>
              <div className="w-28 pb-2 text-[12px] text-muted">{p && fromCode ? `${fromCode} has ${qty(p.stock[fromCode], p.uom)}` : ''}</div>
              <Button variant="ghost" aria-label="Remove line" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))} disabled={lines.length === 1}><Trash2 className="size-4" /></Button>
            </div>
          )
        })}
        <Button size="sm" icon={<Plus className="size-4" />} onClick={() => setLines((ls) => [...ls, { product_id: '', quantity: '' }])}>Add line</Button>
      </div>
      <Field label="Reason" htmlFor="tr" className="mt-4"><Textarea id="tr" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <ErrorBox error={m.error} className="mt-3" />
    </Modal>
  )
}
