import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, XCircle } from 'lucide-react'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, CardHeader, ErrorBox, Field, Input, LoadingBlock, Notice, PageHeader, StatusChip, Table, Td, Th, useToast } from '../components/ui'
import { date, money, num, qty, todayISO } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function InvoiceDetailPage() {
  const { id } = useParams()
  const { can } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()
  const q = useQuery({ queryKey: ['invoice', id], queryFn: () => api.get<R>(`/api/finance/invoices/${id}`) })
  const [ref, setRef] = useState('')
  const [invDate, setInvDate] = useState(todayISO())
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const post = useMutation({ mutationFn: () => api.post(`/api/finance/invoices/${id}/post`, { supplier_reference: ref, invoice_date: invDate }), onSuccess: () => { toast('good', 'Bill posted to the ledger.'); qc.invalidateQueries({ queryKey: ['invoice', id] }) } })
  const pay = useMutation({ mutationFn: () => api.post<R>(`/api/finance/invoices/${id}/payment-request`, { amount: amount || q.data!.amount_to_pay, reason }), onSuccess: (r) => navigate(`/approvals/${r.id}`) })
  if (q.isLoading) return <LoadingBlock rows={6} />
  if (q.error) return <ErrorBox error={q.error} />
  const inv = q.data!
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title={inv.number ?? `Draft bill #${inv.id}`} subtitle={`${inv.supplier}${inv.branch ? ` · ${inv.branch}` : ''}${inv.reference ? ` · supplier ref ${inv.reference}` : ''}`} actions={<StatusChip status={inv.state} />} />
      <Card>
        <CardHeader title="Lines and 3-way match" subtitle="Ordered vs received vs invoiced, at the PO price"
          action={inv.lines.some((l: R) => l.match !== null) && (inv.three_way_match ? <span className="flex items-center gap-1 text-[13px] font-medium text-good"><CheckCircle2 className="size-4" />Matched</span> : <span className="flex items-center gap-1 text-[13px] font-medium text-bad"><XCircle className="size-4" />Mismatch</span>)} />
        <Table><thead><tr><Th>Description</Th><Th>Account</Th><Th align="right">Ordered</Th><Th align="right">Received</Th><Th align="right">Invoiced</Th><Th align="right">Unit price</Th><Th align="right">Amount</Th></tr></thead>
          <tbody>{inv.lines.map((l: R, i: number) => <tr key={i}><Td>{l.description}</Td><Td className="text-[12px] text-muted">{l.account}</Td><Td align="right">{l.ordered === null ? '—' : qty(l.ordered)}</Td><Td align="right">{l.received === null ? '—' : qty(l.received)}</Td><Td align="right" className={l.match === false ? 'font-semibold text-bad' : ''}>{qty(l.quantity)}</Td><Td align="right">{money(l.unit_price)}</Td><Td align="right">{money(l.amount)}</Td></tr>)}</tbody></Table>
        <dl className="grid grid-cols-3 gap-4 border-t border-line px-5 py-4 text-[13.5px]">
          <div><dt className="text-muted">Untaxed</dt><dd className="tabular font-medium">{money(inv.untaxed)}</dd></div>
          <div><dt className="text-muted">VAT (illustrative)</dt><dd className="tabular font-medium">{money(inv.tax)}</dd></div>
          <div><dt className="text-muted">Total</dt><dd className="tabular text-lg font-semibold">{money(inv.total)}</dd></div>
        </dl>
      </Card>
      {inv.state === 'draft' && can('invoice.post') && (
        <Card className="mt-5 space-y-3 p-5">
          <div className="font-semibold">Verify and post</div>
          <p className="text-[13px] text-muted">Posting recognises the liability and clears goods-received-not-invoiced. A mismatched bill cannot be posted.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Supplier invoice number" htmlFor="sr"><Input id="sr" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
            <Field label="Invoice date" htmlFor="sd"><Input id="sd" type="date" value={invDate} onChange={(e) => setInvDate(e.target.value)} /></Field>
          </div>
          <ErrorBox error={post.error} />
          <Button variant="primary" loading={post.isPending} disabled={!ref || !inv.three_way_match} onClick={() => post.mutate()}>Post bill</Button>
        </Card>
      )}
      {inv.state === 'posted' && num(inv.amount_to_pay) > 0 && can('payment.request') && (
        <Card className="mt-5 space-y-3 p-5">
          <div className="font-semibold">Request payment · {money(inv.amount_to_pay)} outstanding</div>
          <Notice>Payment needs its own approval — approving the purchase did not authorise paying it.</Notice>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Amount (UGX)" htmlFor="pa"><Input id="pa" inputMode="numeric" placeholder={String(num(inv.amount_to_pay))} value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ''))} /></Field>
            <Field label="Reason" htmlFor="pr"><Input id="pr" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. due 30 days after delivery" /></Field>
          </div>
          <ErrorBox error={pay.error} />
          <Button variant="primary" loading={pay.isPending} disabled={reason.length < 3} onClick={() => pay.mutate()}>Send for payment approval</Button>
        </Card>
      )}
      {inv.state === 'paid' && <div className="mt-5"><Notice tone="good">Paid and reconciled{inv.invoice_date ? ` · invoice dated ${date(inv.invoice_date)}` : ''}.</Notice></div>}
    </div>
  )
}
