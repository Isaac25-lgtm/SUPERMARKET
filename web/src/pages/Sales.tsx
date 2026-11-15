import { useQuery } from '@tanstack/react-query'
import { ReceiptText } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Badge, Card, Empty, ErrorBox, Input, Kpi, LoadingBlock, PageHeader, StatusChip, Table, Td, Th } from '../components/ui'
import { dateTime, money, num, todayISO } from '../lib/format'
import { ReceiptModal } from './Pos'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function SalesPage() {
  const { branchId } = useAuth()
  const [from, setFrom] = useState(todayISO())
  const [to, setTo] = useState(todayISO())
  const [open, setOpen] = useState<R | null>(null)
  const q = useQuery({ queryKey: ['sales', branchId, from, to], queryFn: () => api.get<{ sales: R[] }>('/api/pos/sales', { branch_id: branchId, date_from: from, date_to: to }) })
  const sales = q.data?.sales ?? []
  const total = sales.reduce((s, x) => s + num(x.total), 0)
  const openReceipt = async (id: number) => setOpen(await api.get<R>(`/api/pos/sales/${id}`))
  return (
    <div>
      <PageHeader title="Sales & receipts" subtitle="Every receipt is numbered gap-free per register by the accounting engine."
        actions={<><Input aria-label="From" type="date" className="w-auto" value={from} onChange={(e) => setFrom(e.target.value)} /><Input aria-label="To" type="date" className="w-auto" value={to} onChange={(e) => setTo(e.target.value)} /></>} />
      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Receipts" value={sales.filter((s) => s.kind === 'sale').length} />
        <Kpi label="Refunds" value={sales.filter((s) => s.kind === 'refund').length} />
        <Kpi label="Net takings" value={money(total)} />
        <Kpi label="Unverified MoMo/card" value={sales.filter((s) => s.unverified_claims).length} hint="awaiting reconciliation" />
      </div>
      <Card>
        {q.isLoading ? <LoadingBlock /> : q.error ? <div className="p-4"><ErrorBox error={q.error} /></div> : sales.length ? (
          <Table><thead><tr><Th>Receipt</Th><Th>Time</Th><Th>Register</Th><Th>Cashier</Th><Th>Payment</Th><Th align="right">Discount</Th><Th align="right">Total</Th></tr></thead>
            <tbody>{sales.map((s) => (
              <tr key={s.id} className="cursor-pointer hover:bg-surface-2/50" onClick={() => openReceipt(s.id)}>
                <Td><button className="font-mono text-[12.5px] font-medium hover:text-accent hover:underline">{s.number}</button> {s.kind === 'refund' && <StatusChip status="returned" label="Refund" />}{s.source === 'edge' && <Badge tone="info" className="ml-1">Offline</Badge>}</Td>
                <Td>{dateTime(s.posted_at)}</Td><Td>{s.register_name}</Td><Td>{String(s.cashier_name).replace(/\s*\(TEST.*\)/, '')}</Td>
                <Td className="text-[12.5px]">{(s.payments as R[]).map((p) => p.method.replace('_', ' ')).join(' + ')}{s.unverified_claims && <Badge tone="warn" className="ml-1">unverified</Badge>}</Td>
                <Td align="right">{num(s.discount_total) ? money(s.discount_total) : '—'}</Td><Td align="right" className={num(s.total) < 0 ? 'text-bad' : 'font-medium'}>{money(s.total)}</Td>
              </tr>))}</tbody></Table>
        ) : <Empty title="No sales in this range" icon={<ReceiptText className="size-6" />} />}
      </Card>
      {open && <ReceiptModal receipt={open} onClose={() => setOpen(null)} />}
    </div>
  )
}
