import { useMutation, useQuery } from '@tanstack/react-query'
import { Plus, Wallet } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, Empty, ErrorBox, Field, Input, LoadingBlock, Modal, PageHeader, Select, StatusChip, Table, Tabs, Td, Textarea, Th } from '../components/ui'
import { date, money, todayISO } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

const CATEGORIES = [['rent', 'Rent'], ['salaries', 'Salaries & wages'], ['utilities', 'Electricity & water'], ['transport', 'Transport & logistics'], ['repairs', 'Repairs & maintenance'], ['marketing', 'Marketing'], ['security_cleaning', 'Security & cleaning'], ['other', 'Other']]

export default function PayablesPage() {
  const { can } = useAuth()
  const [tab, setTab] = useState<'bills' | 'expenses'>(can('finance.view') ? 'bills' : 'expenses')
  const [state, setState] = useState('')
  const [open, setOpen] = useState(false)
  const bills = useQuery({ queryKey: ['payables', state], queryFn: () => api.get<{ invoices: R[] }>('/api/finance/payables', { state: state || undefined }), enabled: can('finance.view') && tab === 'bills' })
  const expenses = useQuery({ queryKey: ['expenses'], queryFn: () => can('approvals.inbox') ? api.get<{ requests: R[] }>('/api/approvals/history', { action_type: 'expense' }) : api.get<{ requests: R[] }>('/api/approvals/mine', { action_type: 'expense' }), enabled: tab === 'expenses' })
  return (
    <div>
      <PageHeader title="Payables & expenses" subtitle="Expense approval, posting (accrual) and payment are three separate, audited steps."
        actions={can('expense.submit') && <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setOpen(true)}>Submit expense</Button>} />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} items={[...(can('finance.view') ? [{ value: 'bills' as const, label: 'Supplier bills' }] : []), { value: 'expenses' as const, label: 'Expense claims' }]} /></div>
      {tab === 'bills' ? (
        <Card>
          <div className="border-b border-line p-3"><Select aria-label="State" className="w-auto" value={state} onChange={(e) => setState(e.target.value)}><option value="">All</option><option value="draft">Draft — to verify & post</option><option value="posted">Posted — unpaid</option><option value="paid">Paid</option></Select></div>
          {bills.isLoading ? <LoadingBlock /> : bills.data?.invoices.length ? (
            <Table><thead><tr><Th>Bill</Th><Th>Supplier / payee</Th><Th>Branch</Th><Th>Date</Th><Th>Status</Th><Th align="right">Total</Th><Th align="right">Open</Th></tr></thead>
              <tbody>{bills.data.invoices.map((i) => <tr key={i.id} className="hover:bg-surface-2/50"><Td><Link to={`/payables/invoices/${i.id}`} className="font-medium hover:text-accent hover:underline">{i.number ?? `Draft #${i.id}`}</Link><div className="max-w-[260px] truncate text-[12px] text-muted">{i.description ?? i.reference}</div></Td><Td>{i.supplier}</Td><Td>{i.branch_code ?? 'HQ'}</Td><Td>{date(i.invoice_date)}</Td><Td><StatusChip status={i.state} /></Td><Td align="right">{money(i.total)}</Td><Td align="right">{i.open_amount === null ? '—' : money(i.open_amount)}</Td></tr>)}</tbody></Table>
          ) : <Empty title="No bills" icon={<Wallet className="size-6" />} />}
        </Card>
      ) : (
        <Card>
          {expenses.isLoading ? <LoadingBlock /> : expenses.data?.requests.length ? (
            <Table><thead><tr><Th>Expense</Th><Th>Branch</Th><Th>Status</Th><Th>Ledger</Th><Th align="right">Amount</Th></tr></thead>
              <tbody>{expenses.data.requests.map((r) => <tr key={r.id} className="hover:bg-surface-2/50"><Td><Link to={`/approvals/${r.id}`} className="font-medium hover:text-accent hover:underline">{r.title}</Link><div className="text-[12px] text-muted">{r.ref} · {r.payload?.incurred_on}</div></Td><Td>{r.branch_name}</Td><Td><StatusChip status={r.status} label={r.status_label} /></Td><Td>{r.fulfilment_status ? <StatusChip status={r.fulfilment_status} /> : '—'}</Td><Td align="right">{money(r.amount)}</Td></tr>)}</tbody></Table>
          ) : <Empty title="No expense claims" />}
        </Card>
      )}
      {open && <ExpenseModal onClose={() => setOpen(false)} />}
    </div>
  )
}

function ExpenseModal({ onClose }: { onClose: () => void }) {
  const { me, branchId } = useAuth()
  const navigate = useNavigate()
  const [f, setF] = useState({ branch_id: branchId ?? me?.branches[0]?.id ?? 0, category: 'utilities', payee: '', amount: '', description: '', document_ref: '', incurred_on: todayISO() })
  const m = useMutation({ mutationFn: () => api.post<R>('/api/finance/expenses', { ...f, document_ref: f.document_ref || null }), onSuccess: (r) => navigate(`/approvals/${r.id}`) })
  return (
    <Modal open wide onOpenChange={(v) => !v && onClose()} title="Submit an expense" description="Finance verifies, the GM (or CFO above the limit) approves, then the accountant posts it. Payment is requested separately."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!f.payee || !f.amount || f.description.length < 3} onClick={() => m.mutate()}>Submit</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Branch" htmlFor="eb"><Select id="eb" value={f.branch_id} onChange={(e) => setF({ ...f, branch_id: Number(e.target.value) })}>{me?.branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</Select></Field>
        <Field label="Category" htmlFor="ec"><Select id="ec" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
        <Field label="Payee" htmlFor="ep"><Input id="ep" value={f.payee} onChange={(e) => setF({ ...f, payee: e.target.value })} /></Field>
        <Field label="Amount (UGX)" htmlFor="ea"><Input id="ea" inputMode="numeric" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value.replace(/[^\d]/g, '') })} /></Field>
        <Field label="Date incurred" htmlFor="ed"><Input id="ed" type="date" value={f.incurred_on} onChange={(e) => setF({ ...f, incurred_on: e.target.value })} /></Field>
        <Field label="Document / receipt number" htmlFor="er" hint="Keep the paper document; file uploads are not enabled in this test system."><Input id="er" value={f.document_ref} onChange={(e) => setF({ ...f, document_ref: e.target.value })} /></Field>
        <Field label="Description" htmlFor="ex" className="sm:col-span-2"><Textarea id="ex" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      </div>
      <ErrorBox error={m.error} className="mt-3" />
    </Modal>
  )
}
