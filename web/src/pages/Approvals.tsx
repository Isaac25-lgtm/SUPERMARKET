import { useQuery } from '@tanstack/react-query'
import { ClipboardCheck } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { Badge, Card, Empty, ErrorBox, LoadingBlock, PageHeader, StatusChip, Table, Tabs, Td, Th } from '../components/ui'
import { dateTime, money, relative } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Req = Record<string, any>

export default function ApprovalsPage() {
  const [tab, setTab] = useState<'actionable' | 'watching' | 'mine' | 'history'>('actionable')
  const inbox = useQuery({ queryKey: ['inbox'], queryFn: () => api.get<{ actionable: Req[]; watching: Req[]; mine: Req[] }>('/api/approvals/inbox'), refetchInterval: 30_000 })
  const hist = useQuery({ queryKey: ['approval-history'], queryFn: () => api.get<{ requests: Req[] }>('/api/approvals/history'), enabled: tab === 'history' })
  const rows: Req[] = tab === 'history' ? hist.data?.requests ?? [] : inbox.data?.[tab] ?? []
  return (
    <div>
      <PageHeader title="Approvals" subtitle="Requests move step by step through the configured authority chain. You cannot act on your own requests." />
      <div className="mb-4">
        <Tabs value={tab} onChange={setTab} items={[
          { value: 'actionable', label: 'Waiting for me', count: inbox.data?.actionable.length },
          { value: 'watching', label: 'Other pending', count: inbox.data?.watching.length },
          { value: 'mine', label: 'Raised by me', count: inbox.data?.mine.length },
          { value: 'history', label: 'History' },
        ]} />
      </div>
      <Card>
        {inbox.isLoading || (tab === 'history' && hist.isLoading) ? <LoadingBlock /> : inbox.error ? <div className="p-5"><ErrorBox error={inbox.error} /></div> : rows.length ? (
          <Table>
            <thead><tr><Th>Request</Th><Th>Type</Th><Th>Branch</Th><Th>Raised by</Th><Th>Status</Th><Th align="right">Amount</Th><Th align="right">Age</Th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="hover:bg-surface-2/50">
                  <Td><Link to={`/approvals/${r.id}`} className="font-medium hover:text-accent hover:underline">{r.title}</Link><div className="text-[12px] text-muted">{r.ref}{r.revision > 1 ? ` · revision ${r.revision}` : ''}</div></Td>
                  <Td>{r.action_label}</Td>
                  <Td>{r.branch_name}</Td>
                  <Td>{(r.initiator_name ?? 'You').replace(/\s*\(TEST.*\)/, '')}</Td>
                  <Td><div className="flex flex-wrap items-center gap-1.5"><StatusChip status={r.status} label={r.status_label} />{r.urgency === 'high' || r.urgency === 'critical' ? <Badge tone="bad">{r.urgency}</Badge> : null}{r.overdue && <Badge tone="bad">Overdue</Badge>}{r.fulfilment_status && <StatusChip status={r.fulfilment_status} />}</div></Td>
                  <Td align="right">{money(r.amount)}</Td>
                  <Td align="right"><span title={dateTime(r.created_at)}>{relative(r.created_at)}</span></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : <Empty title={tab === 'actionable' ? 'Nothing waiting for you' : 'No requests here'} icon={<ClipboardCheck className="size-6" />} />}
      </Card>
    </div>
  )
}
