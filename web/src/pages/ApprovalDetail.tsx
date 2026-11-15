import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { Check, CornerUpLeft, X } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import {
  Button, Card, CardHeader, ErrorBox, Field, Input, LoadingBlock, Modal, Notice, PageHeader, StatusChip, Table, Td, Textarea, Th,
  errorText, useToast,
} from '../components/ui'
import { date, dateTime, humanize, money, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Req = Record<string, any>

export default function ApprovalDetailPage() {
  const { id } = useParams()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['approval', id], queryFn: () => api.get<Req>(`/api/approvals/${id}`) })
  const [decision, setDecision] = useState<'approve' | 'reject' | 'return' | null>(null)
  if (q.isLoading) return <LoadingBlock rows={8} />
  if (q.error) return <ErrorBox error={q.error} />
  const r = q.data!
  const refresh = () => { qc.invalidateQueries({ queryKey: ['approval', id] }); qc.invalidateQueries({ queryKey: ['inbox'] }); qc.invalidateQueries({ queryKey: ['inbox-count'] }) }
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title={r.title} subtitle={<>{r.ref} · {r.action_label} · {r.branch_name} · raised by {String(r.initiator_name).replace(/\s*\(TEST.*\)/, '')} on {dateTime(r.created_at)}</>}
        actions={<><StatusChip status={r.status} label={r.status_label} />{r.fulfilment_status && <StatusChip status={r.fulfilment_status} />}</>} />
      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <div className="space-y-5">
          <Card>
            <CardHeader title="Amount" subtitle={r.justification} action={<span className="tabular text-xl font-semibold">{money(r.amount)}</span>} />
            <Payload r={r} />
          </Card>
          <NextAction r={r} onDone={refresh} />
          {r.can_act && (
            <Card className="p-5">
              <div className="text-[13px] text-muted">Your decision for step <b className="text-ink">{r.steps[r.current_step].label}</b></div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="primary" icon={<Check className="size-4" />} onClick={() => setDecision('approve')}>Approve</Button>
                <Button icon={<CornerUpLeft className="size-4" />} onClick={() => setDecision('return')}>Return for correction</Button>
                <Button variant="danger" icon={<X className="size-4" />} onClick={() => setDecision('reject')}>Reject</Button>
              </div>
            </Card>
          )}
        </div>
        <div className="space-y-5">
          <Card>
            <CardHeader title="Approval chain" subtitle={`Revision ${r.revision}`} />
            <ol className="space-y-0 px-5 py-4">
              {r.steps.map((s: Req, i: number) => {
                const done = i < r.current_step || r.status === 'approved' || r.status === 'executed'
                const current = i === r.current_step && r.status === 'pending'
                const failed = i === r.current_step && (r.status === 'rejected' || r.status === 'returned')
                return (
                  <li key={i} className="relative flex gap-3 pb-5 last:pb-0">
                    {i < r.steps.length - 1 && <span className={clsx('absolute left-[11px] top-6 h-[calc(100%-18px)] w-0.5', done ? 'bg-accent' : 'bg-line')} />}
                    <span className={clsx('relative z-10 grid size-6 shrink-0 place-items-center rounded-full border-2 text-[11px] font-bold',
                      done ? 'border-accent bg-accent text-white dark:text-canvas' : failed ? 'border-bad bg-bad-soft text-bad' : current ? 'border-accent bg-accent-soft text-accent-ink' : 'border-line bg-surface text-muted')}>
                      {done ? <Check className="size-3.5" /> : failed ? <X className="size-3.5" /> : i + 1}
                    </span>
                    <div><div className="text-[13.5px] font-medium">{s.label}</div><div className="text-[12px] text-muted">{humanize(s.role)} · {s.kind}</div></div>
                  </li>
                )
              })}
            </ol>
          </Card>
          <Card>
            <CardHeader title="History" subtitle="Immutable decision trail" />
            <ol className="divide-y divide-line">
              {r.actions.map((a: Req, i: number) => (
                <li key={i} className="px-5 py-3 text-[13px]">
                  <div className="flex items-center justify-between gap-2"><span className="font-medium">{humanize(a.action)}</span><span className="text-[12px] text-muted">{dateTime(a.at)}</span></div>
                  <div className="text-muted">{String(a.actor_name).replace(/\s*\(TEST.*\)/, '')}{a.acting_role ? ` as ${humanize(a.acting_role)}` : ''}{a.delegated_from_name ? ` (delegated by ${a.delegated_from_name})` : ''}</div>
                  {a.comment && <p className="mt-1 rounded-md bg-surface-2 px-2 py-1.5 text-ink-2">{a.comment}</p>}
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>
      {decision && <DecisionModal r={r} decision={decision} onClose={() => setDecision(null)} onDone={() => { setDecision(null); refresh() }} />}
    </div>
  )
}

function Payload({ r }: { r: Req }) {
  const p = r.payload
  if (r.action_type === 'purchase_request') {
    return (
      <div>
        <div className="px-5 pt-3 text-[13px] text-muted">Supplier: <b className="text-ink">{p.supplier}</b></div>
        <Table className="mt-2"><thead><tr><Th>Item</Th><Th align="right">Qty</Th><Th align="right">Unit cost</Th><Th align="right">Line total</Th></tr></thead>
          <tbody>{p.lines.map((l: Req) => <tr key={l.product_id}><Td>{l.product}</Td><Td align="right">{qty(l.quantity, l.uom)}</Td><Td align="right">{money(l.unit_cost)}</Td><Td align="right">{money(Number(l.quantity) * Number(l.unit_cost))}</Td></tr>)}</tbody></Table>
        {p.evidence?.length > 0 && (
          <>
            <div className="px-5 pt-4 text-[13px] font-semibold">Evidence (computed by the system at submission)</div>
            <Table className="mt-2"><thead><tr><Th>Item</Th><Th align="right">On hand</Th><Th align="right">Sales/day (14d)</Th><Th align="right">Days of cover</Th><Th>Other branches</Th></tr></thead>
              <tbody>{p.evidence.map((e: Req) => <tr key={e.product_id}><Td>{e.product}</Td><Td align="right">{qty(e.on_hand)}</Td><Td align="right">{qty(e.daily_sales_14d)}</Td><Td align="right">{e.days_of_cover ?? '—'}</Td><Td className="text-[12.5px] text-muted">{Object.entries(e.stock_at_other_branches).map(([k, v]) => `${k}: ${qty(v)}`).join(' · ')}</Td></tr>)}</tbody></Table>
          </>
        )}
      </div>
    )
  }
  if (r.action_type === 'transfer') {
    return <div className="px-5 py-4 text-[13.5px]">{p.lines.map((l: Req) => <div key={l.product}>{l.product} × {qty(l.quantity)}</div>)}<Link to={`/transfers/${p.transfer_id}`} className="mt-2 inline-block text-accent hover:underline">Open transfer {p.ref}</Link></div>
  }
  const entries = Object.entries(p).filter(([k]) => !['lines', 'evidence'].includes(k) && !k.endsWith('_id'))
  return (
    <dl className="grid gap-x-6 gap-y-2 px-5 py-4 text-[13.5px] sm:grid-cols-2">
      {entries.map(([k, v]) => <div key={k}><dt className="text-[12px] text-muted">{humanize(k)}</dt><dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd></div>)}
      {Array.isArray(p.lines) && <div className="sm:col-span-2"><dt className="text-[12px] text-muted">Lines</dt><dd>{p.lines.map((l: Req, i: number) => <div key={i}>{l.name ?? l.product ?? `Line ${l.line_id}`} — {l.quantity ?? `${l.discount_pct}% discount`}</div>)}</dd></div>}
    </dl>
  )
}

function DecisionModal({ r, decision, onClose, onDone }: { r: Req; decision: 'approve' | 'reject' | 'return'; onClose: () => void; onDone: () => void }) {
  const [comment, setComment] = useState('')
  const toast = useToast()
  const m = useMutation({
    mutationFn: () => api.post(`/api/approvals/${r.id}/decision`, { decision, comment: comment || null }),
    onSuccess: () => { toast('good', decision === 'approve' ? 'Approved.' : decision === 'reject' ? 'Rejected.' : 'Returned to the requester.'); onDone() },
  })
  const needsReason = decision !== 'approve'
  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title={`${humanize(decision)} ${r.ref}`} description={`${r.title} · ${money(r.amount)}`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant={decision === 'reject' ? 'danger' : 'primary'} loading={m.isPending} disabled={needsReason && comment.trim().length < 3} onClick={() => m.mutate()}>Confirm</Button></>}>
      <Field label={needsReason ? 'Reason (required — the requester will see it)' : 'Comment (optional)'} htmlFor="c"><Textarea id="c" value={comment} onChange={(e) => setComment(e.target.value)} autoFocus /></Field>
      <ErrorBox error={m.error} className="mt-3" />
    </Modal>
  )
}

function NextAction({ r, onDone }: { r: Req; onDone: () => void }) {
  const { can, me } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const [postDate, setPostDate] = useState('')
  const run = useMutation({
    mutationFn: async (kind: string) => {
      if (kind === 'po') return api.post<Req>(`/api/procurement/requisitions/${r.id}/issue-po`)
      if (kind === 'writeoff') return api.post<Req>(`/api/stock/writeoffs/${r.id}/execute`)
      if (kind === 'expense') return api.post<Req>(`/api/finance/expenses/${r.id}/post`, { posting_date: postDate || null })
      if (kind === 'payment') return api.post<Req>(`/api/finance/payments/${r.id}/record`, {})
      if (kind === 'cancel') return api.post<Req>(`/api/approvals/${r.id}/cancel`, { reason: 'Cancelled by requester' })
      throw new Error('unknown')
    },
    onSuccess: (res, kind) => {
      toast('good', { po: 'Purchase order issued.', writeoff: 'Stock written off.', expense: 'Expense posted to the ledger.', payment: 'Payment recorded.', cancel: 'Request cancelled.' }[kind] ?? 'Done')
      onDone()
      if (kind === 'po' && res?.id) navigate(`/procurement/orders/${res.id}`)
    },
    onError: (e) => toast('bad', errorText(e)),
  })
  const isMine = r.initiator_id === me?.id
  const content: React.ReactNode[] = []
  if (r.status === 'approved') {
    if (r.action_type === 'purchase_request' && !r.result_id && can('procurement.issue_po')) content.push(<Button key="po" variant="primary" loading={run.isPending} onClick={() => run.mutate('po')}>Issue purchase order</Button>)
    if (r.action_type === 'stock_writeoff' && can('stock.adjust.request')) content.push(<Button key="wo" variant="primary" loading={run.isPending} onClick={() => run.mutate('writeoff')}>Execute write-off</Button>)
    if (r.action_type === 'expense' && can('invoice.post') && !isMine) content.push(
      <div key="exp" className="flex flex-wrap items-end gap-2"><Field label="Posting date (optional)" htmlFor="pd"><Input id="pd" type="date" value={postDate} onChange={(e) => setPostDate(e.target.value)} /></Field><Button variant="primary" loading={run.isPending} onClick={() => run.mutate('expense')}>Post expense to ledger</Button></div>)
    if (r.action_type === 'supplier_payment' && can('payment.record')) content.push(<Button key="pay" variant="primary" loading={run.isPending} onClick={() => run.mutate('payment')}>Record payment</Button>)
    if (r.action_type === 'refund') content.push(<Notice key="rf">Approved. The cashier pays out the refund from their till (POS → Refunds). It can be used once.</Notice>)
  }
  if (r.result_type === 'purchase.purchase') content.push(<Link key="po-link" className="text-[13.5px] font-medium text-accent hover:underline" to={`/procurement/orders/${r.result_id}`}>Open purchase order</Link>)
  if (r.result_type === 'account.invoice') content.push(<Link key="inv-link" className="text-[13.5px] font-medium text-accent hover:underline" to={`/payables/invoices/${r.result_id}`}>Open bill in payables</Link>)
  if (isMine && ['pending', 'returned', 'approved'].includes(r.status) && !r.fulfilment_status) content.push(<Button key="cancel" variant="ghost" loading={run.isPending} onClick={() => run.mutate('cancel')}>Cancel my request</Button>)
  if (r.status === 'returned' && isMine) content.push(<Notice key="ret" tone="warn">Returned for correction: see the reason in the history, then raise a corrected request.</Notice>)
  if (!content.length) return null
  return <Card className="space-y-3 p-5"><div className="text-[13px] font-semibold">Next step</div><div className="flex flex-wrap items-center gap-3">{content}</div>{r.due_at && r.status === 'pending' && <div className="text-[12px] text-muted">Due {date(r.due_at)}</div>}</Card>
}
