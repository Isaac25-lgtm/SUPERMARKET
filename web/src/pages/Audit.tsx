import { useQuery } from '@tanstack/react-query'
import { Download, Lock, ScrollText, Search } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, Empty, ErrorBox, Input, LoadingBlock, Modal, PageHeader, Select, StatusChip, Table, Td, Th } from '../components/ui'
import { dateTime, money, todayISO } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function AuditPage() {
  const { can, me } = useAuth()
  const [params] = useSearchParams()
  const thirty = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10)
  const [f, setF] = useState({ q: '', actor: params.get('actor') ?? '', branch: '', action: '', outcome: '', date_from: thirty, date_to: todayISO(), min_amount: '' })
  const [applied, setApplied] = useState(f)
  const [open, setOpen] = useState<R | null>(null)
  const query = { ...applied, actor: applied.actor || undefined, branch: applied.branch || undefined, min_amount: applied.min_amount || undefined }
  const q = useQuery({ queryKey: ['audit', applied], queryFn: () => api.get<{ events: R[]; actors: R[]; actions: string[] }>('/api/audit', query) })
  return (
    <div>
      <PageHeader title="Audit trail" subtitle={<span className="inline-flex items-center gap-1.5"><Lock className="size-3.5" />Append-only: the database rejects any edit or deletion of audit events, including by administrators of this app.</span>}
        actions={can('audit.export') && <a href={api.url('/api/audit/export.csv', query)}><Button icon={<Download className="size-4" />}>Export CSV</Button></a>} />
      <Card className="mb-4 p-3">
        <form className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8" onSubmit={(e) => { e.preventDefault(); setApplied(f) }}>
          <div className="relative sm:col-span-2"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" /><Input aria-label="Search" className="pl-9" placeholder="Search action, reference, reason…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} /></div>
          <Select aria-label="User" value={f.actor} onChange={(e) => setF({ ...f, actor: e.target.value })}><option value="">Any user</option>{q.data?.actors.map((a) => <option key={a.id} value={a.id}>{a.full_name.replace(/\s*\(TEST.*\)/, '')}</option>)}</Select>
          <Select aria-label="Branch" value={f.branch} onChange={(e) => setF({ ...f, branch: e.target.value })}><option value="">Any branch</option>{me?.branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</Select>
          <Select aria-label="Action" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })}><option value="">Any action</option>{['auth', 'admin', 'pos', 'approval', 'transfer', 'stock', 'procurement', 'invoice', 'payment', 'expense', 'catalog', 'finance', 'sync', 'reports', 'audit', 'insights'].map((p) => <option key={p} value={p}>{p}.*</option>)}{q.data?.actions.map((a) => <option key={a} value={a}>{a}</option>)}</Select>
          <Select aria-label="Outcome" value={f.outcome} onChange={(e) => setF({ ...f, outcome: e.target.value })}><option value="">Any outcome</option><option value="success">Success</option><option value="denied">Denied</option><option value="failure">Failure</option></Select>
          <Input aria-label="From" type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} />
          <Input aria-label="To" type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} />
          <Input aria-label="Minimum amount" inputMode="numeric" placeholder="Min amount" value={f.min_amount} onChange={(e) => setF({ ...f, min_amount: e.target.value.replace(/[^\d]/g, '') })} />
          <Button type="submit" variant="primary">Search</Button>
        </form>
      </Card>
      <Card>
        {q.isLoading ? <LoadingBlock rows={10} /> : q.error ? <div className="p-4"><ErrorBox error={q.error} /></div> : q.data?.events.length ? (
          <Table><thead><tr><Th>When</Th><Th>Who</Th><Th>Action</Th><Th>Outcome</Th><Th>Target</Th><Th>Branch</Th><Th align="right">Amount</Th><Th>Reason</Th></tr></thead>
            <tbody>{q.data.events.map((e) => (
              <tr key={e.id} className="cursor-pointer hover:bg-surface-2/50" onClick={() => setOpen(e)}>
                <Td className="whitespace-nowrap">{dateTime(e.at)}</Td><Td>{e.actor_username ?? 'system'}<div className="text-[11.5px] text-muted">{(e.actor_roles ?? []).join(', ')}</div></Td>
                <Td mono>{e.action}</Td><Td><StatusChip status={e.outcome} /></Td><Td className="text-[12.5px]">{e.target_type} <span className="font-mono">{e.target_id}</span></Td>
                <Td>{e.branch_code ?? '—'}</Td><Td align="right">{e.amount === null ? '' : money(e.amount)}</Td><Td className="max-w-[260px] truncate text-[12.5px] text-muted" title={e.reason}>{e.reason}</Td>
              </tr>))}</tbody></Table>
        ) : <Empty title="No events match" icon={<ScrollText className="size-6" />} />}
      </Card>
      {open && (
        <Modal open wide onOpenChange={(v) => !v && setOpen(null)} title={open.action} description={`${dateTime(open.at)} · ${open.actor_username ?? 'system'} · ${open.outcome}`}>
          <dl className="grid gap-3 text-[13px] sm:grid-cols-2">
            <div><dt className="text-muted">Target</dt><dd>{open.target_type} {open.target_id}</dd></div>
            <div><dt className="text-muted">Branch</dt><dd>{open.branch_code ?? '—'}</dd></div>
            <div><dt className="text-muted">Request ID</dt><dd className="font-mono">{open.request_id ?? '—'}</dd></div>
            <div><dt className="text-muted">Session / IP</dt><dd className="font-mono">{open.session_ref ?? '—'} · {open.ip ?? '—'}</dd></div>
            <div className="sm:col-span-2"><dt className="text-muted">Reason</dt><dd>{open.reason ?? '—'}</dd></div>
            <div><dt className="text-muted">Before</dt><dd><pre className="max-h-64 overflow-auto rounded-lg bg-surface-2 p-2 text-[12px]">{open.before_state ? JSON.stringify(open.before_state, null, 2) : '—'}</pre></dd></div>
            <div><dt className="text-muted">After</dt><dd><pre className="max-h-64 overflow-auto rounded-lg bg-surface-2 p-2 text-[12px]">{open.after_state ? JSON.stringify(open.after_state, null, 2) : '—'}</pre></dd></div>
          </dl>
        </Modal>
      )}
    </div>
  )
}
