import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { HardDrive, Plus, ServerCog } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, CardHeader, Empty, ErrorBox, Field, Input, Kpi, LoadingBlock, Modal, Notice, PageHeader, Select, StatusChip, Table, Td, Th } from '../components/ui'
import { dateTime, relative } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

export default function SystemPage() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const health = useQuery({ queryKey: ['healthz'], queryFn: () => fetch('/healthz').then((r) => r.json()) as Promise<R>, refetchInterval: 30_000 })
  const dash = useQuery({ queryKey: ['dashboard', 'sys'], queryFn: () => api.get<R>('/api/dashboard', { role: 'sysadmin' }), enabled: can('system.health') })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => api.get<R>('/api/sync/devices'), enabled: can('sync.devices') })
  const [open, setOpen] = useState(false)
  const [token, setToken] = useState<string | null>(null)
  return (
    <div>
      <PageHeader title="System & branch devices" subtitle="Technical administration only — no business approvals or financial data here."
        actions={can('sync.devices') && <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setOpen(true)}>Register branch edge</Button>} />
      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="API" value={health.data?.ready ? 'Ready' : health.data?.phase ?? '…'} tone={health.data?.ready ? 'good' : 'warn'} hint={`v${health.data?.version ?? '?'} · up ${Math.round((health.data?.uptime_s ?? 0) / 60)} min`} icon={<ServerCog className="size-4" />} />
        {dash.data && <Kpi label="Database size" value={dash.data.db_size} icon={<HardDrive className="size-4" />} />}
        {dash.data && <Kpi label="Platform migrations" value={dash.data.migrations.length} hint={dash.data.migrations.map((m: R) => m.version).join(', ')} />}
      </div>
      <Card>
        <CardHeader title="Branch edge devices" subtitle="On-premise servers that keep tills selling during internet outages and sync to this system." />
        {devices.isLoading ? <LoadingBlock /> : devices.data?.devices.length ? (
          <Table><thead><tr><Th>Device</Th><Th>Branch</Th><Th>Last sync</Th><Th align="right">Events</Th><Th align="right">Conflicts</Th><Th>Status</Th></tr></thead>
            <tbody>{devices.data.devices.map((d: R) => <tr key={d.id}><Td>{d.name}</Td><Td>{d.branch_code}</Td><Td title={dateTime(d.last_seen_at)}>{relative(d.last_seen_at)}</Td><Td align="right">{d.events}</Td><Td align="right">{d.conflicts}</Td><Td>{d.revoked_at ? <StatusChip status="cancelled" label="Revoked" /> : <StatusChip status="active" />}</Td></tr>)}</tbody></Table>
        ) : <Empty title="No branch edge devices registered" />}
        {devices.error && <div className="p-4"><ErrorBox error={devices.error} /></div>}
      </Card>
      {(devices.data?.recent?.length ?? 0) > 0 && (
        <Card className="mt-5"><CardHeader title="Recent offline events received" />
          <Table><thead><tr><Th>Received</Th><Th>Device</Th><Th>Status</Th><Th>Central receipt</Th></tr></thead>
            <tbody>{devices.data!.recent.map((e: R) => <tr key={e.event_id}><Td>{dateTime(e.received_at)}</Td><Td>{e.device}</Td><Td><StatusChip status={e.status} /></Td><Td mono>{e.number}</Td></tr>)}</tbody></Table>
        </Card>
      )}
      {open && <RegisterDevice onClose={() => setOpen(false)} onToken={(t) => { setOpen(false); setToken(t); qc.invalidateQueries({ queryKey: ['devices'] }) }} />}
      {token && (
        <Modal open onOpenChange={(v) => !v && setToken(null)} title="Device token" footer={<Button variant="primary" onClick={() => setToken(null)}>I have stored it</Button>}>
          <Notice tone="warn">Copy this into the branch edge's configuration (MOTION_EDGE_TOKEN). It is shown only once; only its hash is stored. Revoke the device if it is lost.</Notice>
          <div className="mt-4 break-all rounded-xl border border-line-strong bg-surface-2 p-4 font-mono text-[13px]">{token}</div>
        </Modal>
      )}
    </div>
  )
}

function RegisterDevice({ onClose, onToken }: { onClose: () => void; onToken: (t: string) => void }) {
  const all = useQuery({ queryKey: ['all-branches'], queryFn: () => api.get<{ directory: R[] }>('/api/branches') })
  const [branch, setBranch] = useState<number | ''>('')
  const [name, setName] = useState('')
  const m = useMutation({ mutationFn: () => api.post<R>('/api/sync/devices', { branch_id: branch, name }), onSuccess: (r) => onToken(r.token) })
  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title="Register a branch edge device"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!branch || name.length < 2} onClick={() => m.mutate()}>Register</Button></>}>
      <div className="space-y-3">
        <Field label="Branch" htmlFor="db"><Select id="db" value={branch} onChange={(e) => setBranch(Number(e.target.value))}><option value="">Choose…</option>{all.data?.directory.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</Select></Field>
        <Field label="Device name" htmlFor="dn"><Input id="dn" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. NTH back-office mini PC" /></Field>
        <ErrorBox error={m.error} />
      </div>
    </Modal>
  )
}
