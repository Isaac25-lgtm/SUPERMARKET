import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Badge, Button, Card, Empty, ErrorBox, LoadingBlock, Notice, PageHeader, Select, StatusChip, useToast } from '../components/ui'
import { humanize, money, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>
function fmtEvidence(k: string, v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (/value|sales|variance|discounts/.test(k) && !isNaN(Number(v))) return money(v)
  if (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v)) return qty(v)
  return String(v)
}

function AlertsPanel() {
  const { branchId } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const [kind, setKind] = useState('')
  const [showDone, setShowDone] = useState(false)
  const q = useQuery({ queryKey: ['insights', branchId], queryFn: () => api.get<{ insights: R[]; method: string }>('/api/insights', { branch_id: branchId }) })
  const ack = useMutation({ mutationFn: (b: { key: string; status: string }) => api.post('/api/insights/ack', b), onSuccess: () => qc.invalidateQueries({ queryKey: ['insights'] }) })
  const items = useMemo(() => (q.data?.insights ?? []).filter((i) => (!kind || i.kind === kind) && (showDone || i.status === 'new')), [q.data, kind, showDone])
  const kinds = Array.from(new Set((q.data?.insights ?? []).map((i) => i.kind)))
  return (
    <div>
      <p className="mb-3 text-[13px] text-ink-2">Rule-based, evidence-linked review flags ({q.data?.method ?? 'rules-v1'}). Suggestions never act on their own; they only pre-fill drafts for the normal approval workflow.</p>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select aria-label="Type" className="w-auto" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All types</option>{kinds.map((k) => <option key={k} value={k}>{humanize(k)}</option>)}</Select>
        <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} className="size-4" />Show acknowledged & dismissed</label>
      </div>
      {q.isLoading ? <LoadingBlock rows={6} /> : q.error ? <ErrorBox error={q.error} /> : items.length ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {items.map((i) => (
            <Card key={i.key} className="flex flex-col p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5"><StatusChip status={i.severity} /><Badge>{humanize(i.kind)}</Badge>{i.branch && <Badge>{i.branch}</Badge>}{i.status !== 'new' && <StatusChip status={i.status} />}</div>
                <span className="text-[11.5px] text-muted">confidence: {i.confidence}</span>
              </div>
              <h3 className="mt-2 text-[14.5px] font-semibold leading-snug">{i.title}</h3>
              <p className="mt-1 text-[13px] text-ink-2">{i.detail}</p>
              {i.evidence && Object.keys(i.evidence).length > 0 && (
                <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg bg-surface-2 p-3 text-[12.5px]">
                  {Object.entries(i.evidence).map(([k, v]) => <div key={k} className="flex justify-between gap-2"><dt className="text-muted">{humanize(k)}</dt><dd className="tabular text-right font-medium">{fmtEvidence(k, v)}</dd></div>)}
                </dl>
              )}
              <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
                {i.suggested_action?.type === 'draft_requisition' && <Link to="/procurement"><Button size="sm" variant="subtle">Draft a requisition</Button></Link>}
                {i.suggested_action?.type === 'draft_transfer' && <Link to="/transfers"><Button size="sm" variant="subtle">Draft a transfer</Button></Link>}
                {i.suggested_action?.type === 'close_shift' && <Link to="/shifts"><Button size="sm" variant="subtle">Review shift</Button></Link>}
                {i.suggested_action?.product_id && <Link to={`/stock/products/${i.suggested_action.product_id}`} className="text-[12.5px] text-accent hover:underline">Open product</Link>}
                {i.status === 'new' && <><Button size="sm" variant="ghost" onClick={() => ack.mutate({ key: i.key, status: 'acknowledged' }, { onSuccess: () => toast('good', 'Acknowledged.') })}>Acknowledge</Button><Button size="sm" variant="ghost" onClick={() => ack.mutate({ key: i.key, status: 'dismissed' })}>Dismiss</Button></>}
                <span className="ml-auto text-[11px] text-muted">{i.method}</span>
              </div>
            </Card>
          ))}
        </div>
      ) : <Card><Empty title="No alerts to review" icon={<Sparkles className="size-6" />}>"Not enough data" is a valid answer. Alerts appear as history accumulates.</Empty></Card>}
      <div className="mt-6"><Notice>Flags about cashiers are review prompts, not accusations. Forecasts use simple moving averages over the stated window and are only as good as the history available.</Notice></div>
    </div>
  )
}

export default function InsightsPage() {
  return (
    <div>
      <PageHeader title="Alerts & insights" subtitle={<>What Motion AI has flagged for review. Questions? Use the <strong>Ask Motion AI</strong> button at the bottom right of any page.</>} />
      <AlertsPanel />
    </div>
  )
}
