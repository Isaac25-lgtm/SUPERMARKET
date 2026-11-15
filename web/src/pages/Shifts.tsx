import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Timer } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, Empty, ErrorBox, Field, Input, LoadingBlock, Modal, PageHeader, Select, StatusChip, Table, Td, Textarea, Th, useToast } from '../components/ui'
import { dateTime, money, num } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function ShiftsPage() {
  const { branchId, can, me } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const [status, setStatus] = useState('')
  const [act, setAct] = useState<{ s: R; mode: 'reconcile' | 'close' } | null>(null)
  const q = useQuery({ queryKey: ['shifts', branchId, status], queryFn: () => api.get<{ shifts: R[] }>('/api/pos/shifts', { branch_id: branchId, status: status || undefined }) })
  return (
    <div>
      <PageHeader title="Shifts & cash reconciliation" subtitle="Expected cash = opening float + cash sales − cash refunds ± transfers. Variances are booked to Cash over/short."
        actions={<>{can('reports.export') && <a href={api.url('/api/reports/shifts.csv', { branch_id: branchId })}><Button icon={<Download className="size-4" />}>Export</Button></a>}</>} />
      <Card>
        <div className="border-b border-line p-3"><Select aria-label="Status" className="w-auto" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option><option value="open">Open</option><option value="closed">Closed (to reconcile)</option><option value="reconciled">Reconciled</option></Select></div>
        {q.isLoading ? <LoadingBlock /> : q.data?.shifts.length ? (
          <Table><thead><tr><Th>Register</Th><Th>Cashier</Th><Th>Opened</Th><Th>Closed</Th><Th align="right">Expected</Th><Th align="right">Counted</Th><Th align="right">Variance</Th><Th>Status</Th><Th /></tr></thead>
            <tbody>{q.data.shifts.map((s) => (
              <tr key={s.id}>
                <Td>{s.register_name}<div className="text-[12px] text-muted">{s.branch_code}</div></Td><Td>{String(s.cashier_name).replace(/\s*\(TEST.*\)/, '')}</Td><Td>{dateTime(s.opened_at)}</Td><Td>{dateTime(s.closed_at)}</Td>
                <Td align="right">{s.expected_cash === null ? '—' : money(s.expected_cash)}</Td><Td align="right">{s.counted_cash === null ? '—' : money(s.counted_cash)}</Td>
                <Td align="right" className={num(s.variance) < 0 ? 'font-semibold text-bad' : num(s.variance) > 0 ? 'font-semibold text-warn' : ''}>{s.variance === null ? '—' : money(s.variance, { sign: true })}</Td>
                <Td><StatusChip status={s.interrupted ? 'interrupted' : s.status} />{s.reconcile_note && <div className="max-w-[200px] truncate text-[12px] text-muted" title={s.reconcile_note}>{s.reconcile_note}</div>}</Td>
                <Td align="right">
                  {s.status === 'closed' && can('pos.shift.reconcile') && s.cashier_id !== me?.id && <Button size="sm" onClick={() => setAct({ s, mode: 'reconcile' })}>Reconcile</Button>}
                  {s.status === 'open' && can('pos.shift.reconcile') && s.cashier_id !== me?.id && <Button size="sm" variant={s.interrupted ? 'danger' : 'secondary'} onClick={() => setAct({ s, mode: 'close' })}>Force close</Button>}
                </Td>
              </tr>))}</tbody></Table>
        ) : <Empty title="No shifts" icon={<Timer className="size-6" />} />}
      </Card>
      {act && <ShiftAction s={act.s} mode={act.mode} onClose={() => setAct(null)} onDone={() => { toast('good', act.mode === 'reconcile' ? 'Shift reconciled.' : 'Shift closed.'); setAct(null); qc.invalidateQueries({ queryKey: ['shifts'] }) }} />}
    </div>
  )
}

function ShiftAction({ s, mode, onClose, onDone }: { s: R; mode: 'reconcile' | 'close'; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState('')
  const [counted, setCounted] = useState('')
  const m = useMutation({
    mutationFn: () => mode === 'reconcile' ? api.post(`/api/pos/shifts/${s.id}/reconcile`, { note }) : api.post(`/api/pos/shifts/${s.id}/close`, { counted_cash: counted, cash_drop: '0', note }),
    onSuccess: onDone,
  })
  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title={mode === 'reconcile' ? 'Reconcile shift' : 'Force-close an interrupted shift'}
      description={`${s.register_name} · ${String(s.cashier_name).replace(/\s*\(TEST.*\)/, '')} · opened ${dateTime(s.opened_at)}`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={note.trim().length < 3 || (mode === 'close' && counted === '')} onClick={() => m.mutate()}>Confirm</Button></>}>
      <div className="space-y-3">
        {mode === 'reconcile' && <div className="rounded-lg bg-surface-2 p-3 text-[13px]">Expected {money(s.expected_cash)} · counted {money(s.counted_cash)} · variance <b>{money(s.variance, { sign: true })}</b></div>}
        {mode === 'close' && <Field label="Cash counted in the drawer (UGX)" htmlFor="fc"><Input id="fc" inputMode="numeric" value={counted} onChange={(e) => setCounted(e.target.value.replace(/[^\d]/g, ''))} /></Field>}
        <Field label={mode === 'reconcile' ? 'Finding / explanation' : 'Why is a manager closing this shift?'} htmlFor="fn"><Textarea id="fn" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <ErrorBox error={m.error} />
      </div>
    </Modal>
  )
}
