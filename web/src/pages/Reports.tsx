import { useMutation, useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { BarList } from '../components/charts'
import { Button, Card, CardHeader, ErrorBox, LoadingBlock, PageHeader, Select, Table, Td, Th } from '../components/ui'
import { money, num, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function ReportsPage() {
  const { branchId } = useAuth()
  const [period, setPeriod] = useState('month')
  const q = useQuery({ queryKey: ['sales-kpis', period, branchId], queryFn: () => api.get<R>('/api/finance/sales', { period, branch_id: branchId }) })
  const download = useMutation({
    mutationFn: (name: string) => api.downloadCsv(`/api/reports/${name}.csv`, `${name}.csv`, { period, branch_id: branchId }),
  })
  const d = q.data
  return (
    <div>
      <PageHeader title="Reports" subtitle="Exports respect your branch scope and are recorded in the audit trail."
        actions={<Select aria-label="Period" className="w-auto" value={period} onChange={(e) => setPeriod(e.target.value)}>{[['today', 'Today'], ['week', 'This week'], ['month', 'Month to date'], ['last30', 'Last 30 days'], ['fytd', 'Year to date']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>} />
      <Card className="mb-5 p-5">
        <div className="text-[14px] font-semibold">Downloads (CSV)</div>
        <div className="mt-3 flex flex-wrap gap-2">
          {[['pnl', 'Branch & consolidated P&L'], ['stock', 'Stock on hand by branch'], ['shifts', 'Cashier shift settlement']].map(([n, l]) => (
            <Button key={n} disabled={download.isPending} onClick={() => download.mutate(n)} icon={<Download className="size-4" />}>{l}</Button>
          ))}
        </div>
        <ErrorBox error={download.error} />
      </Card>
      {q.isLoading ? <LoadingBlock rows={6} /> : q.error ? <ErrorBox error={q.error} /> : d && (
        <div className="grid gap-5 xl:grid-cols-2">
          <Card><CardHeader title="Sales by category" subtitle={`${d.period.label} · VAT-inclusive till prices`} /><div className="p-4"><BarList data={d.by_category.map((c: R) => ({ name: c.category ?? 'Other', value: num(c.amount) }))} label="Sales" /></div></Card>
          <Card><CardHeader title="Top products" subtitle={d.period.label} /><Table><thead><tr><Th>Product</Th><Th align="right">Qty</Th><Th align="right">Sales</Th></tr></thead><tbody>{d.top_products.map((p: R) => <tr key={p.name}><Td>{p.name}</Td><Td align="right">{qty(p.qty)}</Td><Td align="right">{money(p.amount)}</Td></tr>)}</tbody></Table></Card>
          <Card><CardHeader title="Payment methods" subtitle={d.period.label} /><Table><thead><tr><Th>Method</Th><Th align="right">Payments</Th><Th align="right">Amount</Th></tr></thead><tbody>{d.by_method.map((m: R) => <tr key={m.method}><Td>{String(m.method).replace('_', ' ')}</Td><Td align="right">{m.n}</Td><Td align="right">{money(m.amount)}</Td></tr>)}</tbody></Table></Card>
          <Card><CardHeader title="Cashier activity" subtitle={d.period.label} /><Table><thead><tr><Th>Cashier</Th><Th align="right">Sales</Th><Th align="right">Refunds</Th><Th align="right">Discounts</Th><Th align="right">Net</Th></tr></thead><tbody>{d.by_cashier.map((c: R) => <tr key={c.cashier}><Td>{String(c.cashier).replace(/\s*\(TEST.*\)/, '')}</Td><Td align="right">{c.sales}</Td><Td align="right">{c.refunds}</Td><Td align="right">{money(c.discounts)}</Td><Td align="right">{money(c.amount)}</Td></tr>)}</tbody></Table></Card>
          <Card className="xl:col-span-2"><CardHeader title="Sales by hour of day" subtitle={`${d.period.label} · local time`} /><div className="p-4"><BarList data={d.by_hour.map((h: R) => ({ name: `${String(h.hour).padStart(2, '0')}:00`, value: num(h.amount) }))} label="Sales" /></div></Card>
        </div>
      )}
    </div>
  )
}
