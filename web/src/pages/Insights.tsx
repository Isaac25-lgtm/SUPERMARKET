import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Eraser, SendHorizontal, Sparkles, User, Wrench } from 'lucide-react'
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Badge, Button, Card, Empty, ErrorBox, LoadingBlock, Notice, PageHeader, Select, StatusChip, Tabs, Textarea, useToast } from '../components/ui'
import { humanize, money, qty } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>
interface Turn { role: 'user' | 'assistant'; content: string; evidence?: R[]; llm?: boolean; note?: string; model?: string | null }
interface AiStatus { enabled: boolean; model: string | null; provider: string | null; hourly_limit: number; suggestions: string[] }

// The conversation survives navigating away and back within this page load; signing out
// reloads the page, so it never carries over to the next person.
const conversations = new Map<number, Turn[]>()

const TOOL_LABELS: Record<string, string> = {
  list_branches: 'Branches', get_sales_summary: 'Sales summary', get_profit_and_loss: 'Profit & loss',
  get_sales_trend: 'Daily trend', find_products: 'Product search', get_stock_alerts: 'Alerts',
  get_cash_position: 'Cash position', get_pending_approvals: 'Approvals', get_break_even: 'Break-even',
  get_shift_variances: 'Shift variances',
}

/** Small, safe renderer for the assistant's markdown-ish replies (no HTML injection). */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part.replace(/`/g, '')}</Fragment>)
}

function Rich({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: string[] } | null = null
  const flush = () => {
    if (!list) return
    const Tag = list.ordered ? 'ol' : 'ul'
    blocks.push(<Tag key={blocks.length} className={list.ordered ? 'list-decimal space-y-0.5 pl-5' : 'list-disc space-y-0.5 pl-5'}>{list.items.map((it, i) => <li key={i}>{inline(it)}</li>)}</Tag>)
    list = null
  }
  let table: string[][] | null = null
  const flushTable = () => {
    if (!table) return
    const [head, ...body] = table
    blocks.push(
      <div key={blocks.length} className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-[13px]">
          <thead><tr className="border-b border-line bg-surface-2 text-left">{head.map((c, i) => <th key={i} className="px-3 py-1.5 font-semibold">{inline(c)}</th>)}</tr></thead>
          <tbody>{body.map((r, i) => <tr key={i} className="border-b border-line last:border-0">{r.map((c, j) => <td key={j} className={j ? 'tabular px-3 py-1.5 text-right' : 'px-3 py-1.5'}>{inline(c)}</td>)}</tr>)}</tbody>
        </table>
      </div>)
    table = null
  }
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flush()
      const cells = line.trim().slice(1, -1).split('|').map((c) => c.trim())
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue
      ;(table ??= []).push(cells)
      continue
    }
    flushTable()
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/)
    const num = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (bullet || num) {
      const ordered = Boolean(num)
      if (!list || list.ordered !== ordered) { flush(); list = { ordered, items: [] } }
      list.items.push((bullet ?? num)![1])
      continue
    }
    flush()
    if (!line.trim()) continue
    const h = line.match(/^#{1,4}\s+(.*)$/)
    blocks.push(h ? <p key={blocks.length} className="font-semibold">{inline(h[1])}</p> : <p key={blocks.length}>{inline(line)}</p>)
  }
  flush()
  flushTable()
  return <div className="space-y-2 text-[14px] leading-relaxed">{blocks}</div>
}

function ChatPanel() {
  const { me, meta } = useAuth()
  const uid = me?.id ?? 0
  const status = useQuery({ queryKey: ['ai-status'], queryFn: () => api.get<AiStatus>('/api/ai/status'), staleTime: 300_000 })
  const [turns, setTurns] = useState<Turn[]>(() => conversations.get(uid) ?? [])
  const [draft, setDraft] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { conversations.set(uid, turns); endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [turns, uid])
  const send = useMutation({
    mutationFn: (message: string) => api.post<R>('/api/ai/chat', {
      message, history: turns.slice(-8).map(({ role, content }) => ({ role, content: content.slice(0, 4000) })),
    }),
    onSuccess: (r) => setTurns((t) => [...t, { role: 'assistant', content: r.answer, evidence: r.evidence, llm: r.llm, note: r.note, model: r.model }]),
    onError: () => setTurns((t) => t.slice(0, -1)),
  })
  const submit = (text: string) => {
    const message = text.trim()
    if (message.length < 2 || send.isPending) return
    setTurns((t) => [...t, { role: 'user', content: message }])
    setDraft('')
    send.mutate(message)
  }
  const enabled = status.data?.enabled ?? meta?.ai_llm_enabled
  return (
    <Card className="flex min-h-[60vh] flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
        <div className="flex items-center gap-2.5">
          <span className="grid size-8 place-items-center rounded-full bg-accent-soft text-accent-ink"><Bot className="size-4" /></span>
          <div>
            <p className="text-[14px] font-semibold">Motion AI</p>
            <p className="text-[12px] text-muted">{enabled ? `Online · answers from your authorised data · ${status.data?.provider ?? 'DeepSeek'}` : 'Rule-based mode (AI provider switched off)'}</p>
          </div>
        </div>
        {turns.length > 0 && <Button size="sm" variant="ghost" onClick={() => setTurns([])}><Eraser className="size-4" />New chat</Button>}
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4" aria-live="polite">
        {turns.length === 0 && (
          <div className="mx-auto max-w-xl py-6 text-center">
            <Sparkles className="mx-auto size-7 text-accent" />
            <p className="mt-2 text-[15px] font-semibold">Ask Motion AI about your supermarket</p>
            <p className="mt-1 text-[13px] text-ink-2">Sales, margins, stock that may run out, expiring food, cash, approvals. It only sees what your role and branches allow, and it cannot change anything.</p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {(status.data?.suggestions ?? []).map((s) => (
                <button key={s} onClick={() => submit(s)} className="rounded-full border border-line bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 transition hover:border-accent hover:text-accent">{s}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={t.role === 'user' ? 'flex justify-end' : 'flex gap-2.5'}>
            {t.role === 'assistant' && <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-accent-ink"><Bot className="size-3.5" /></span>}
            <div className={t.role === 'user' ? 'max-w-[85%] rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-[14px] text-white' : 'max-w-[90%] min-w-0'}>
              {t.role === 'user' ? <span className="whitespace-pre-wrap">{t.content}</span> : (
                <>
                  <div className="rounded-2xl rounded-tl-md bg-surface-2 px-4 py-3"><Rich text={t.content} /></div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-muted">
                    {t.evidence?.map((e, j) => (
                      <span key={j} title={JSON.stringify(e.args)} className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5">
                        <Wrench className="size-3" />{TOOL_LABELS[e.tool] ?? e.tool}{e.args?.period ? ` · ${e.args.period}` : ''}{e.args?.branch_code ? ` · ${e.args.branch_code}` : ''}{e.result !== 'ok' && ' · no access'}
                      </span>
                    ))}
                    {t.note && <span>{t.note}</span>}
                  </div>
                </>
              )}
            </div>
            {t.role === 'user' && <span className="sr-only"><User /> you</span>}
          </div>
        ))}
        {send.isPending && (
          <div className="flex gap-2.5">
            <span className="grid size-7 place-items-center rounded-full bg-accent-soft text-accent-ink"><Bot className="size-3.5" /></span>
            <div className="rounded-2xl rounded-tl-md bg-surface-2 px-4 py-3 text-[13px] text-muted"><span className="animate-pulse">Motion AI is checking the figures…</span></div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <ErrorBox error={send.error} className="mx-5 mb-3" />
      <form className="flex items-end gap-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); submit(draft) }}>
        <Textarea aria-label="Message Motion AI" rows={1} maxLength={1000} className="max-h-40 min-h-[44px] flex-1 resize-y"
          placeholder="Ask about sales, stock, expiry, cash or approvals…  (Enter to send, Shift+Enter for a new line)"
          value={draft} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(draft) } }} />
        <Button type="submit" variant="primary" loading={send.isPending} disabled={draft.trim().length < 2} aria-label="Send"><SendHorizontal className="size-4" /></Button>
      </form>
      <p className="px-5 pb-3 text-[11.5px] text-muted">Motion AI can make mistakes. Check important figures in Reports before acting. Questions are logged for audit.</p>
    </Card>
  )
}

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
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'alerts' ? 'alerts' : 'chat'
  return (
    <div>
      <PageHeader title="Ask Motion AI" subtitle="Your supermarket assistant: ask questions in plain language, and review the alerts it raises." />
      <div className="mb-4">
        <Tabs value={tab} onChange={(v) => setParams(v === 'chat' ? {} : { tab: v }, { replace: true })}
          items={[{ value: 'chat', label: 'Chat' }, { value: 'alerts', label: 'Alerts' }]} />
      </div>
      {tab === 'chat' ? <ChatPanel /> : <AlertsPanel />}
    </div>
  )
}
