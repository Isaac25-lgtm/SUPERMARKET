import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Bot, Eraser, Maximize2, Minimize2, SendHorizontal, Sparkles, User, Wrench, X } from 'lucide-react'
import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import clsx from 'clsx'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, ErrorBox, Textarea } from './ui'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>

interface Turn { role: 'user' | 'assistant'; content: string; evidence?: R[]; links?: { label: string; href: string }[]; llm?: boolean; note?: string; model?: string | null }
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

export function ChatPanel({ onClose, expanded, onToggleExpand }: { onClose?: () => void; expanded?: boolean; onToggleExpand?: () => void } = {}) {
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
    onSuccess: (r) => setTurns((t) => [...t, { role: 'assistant', content: r.answer, evidence: r.evidence, links: r.links, llm: r.llm, note: r.note, model: r.model }]),
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
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
        <div className="flex items-center gap-2.5">
          <span className="grid size-8 place-items-center rounded-full bg-accent-soft text-accent-ink"><Bot className="size-4" /></span>
          <div>
            <p className="text-[14px] font-semibold">Motion AI</p>
            <p className="text-[12px] text-muted">{enabled ? 'Online · answers within your access rights' : 'Rule-based mode (AI provider off)'}</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {turns.length > 0 && <Button size="sm" variant="ghost" onClick={() => setTurns([])} aria-label="New chat"><Eraser className="size-4" />{expanded && <span>New chat</span>}</Button>}
          {onToggleExpand && <Button size="sm" variant="ghost" className="hidden sm:inline-flex" onClick={onToggleExpand} aria-label={expanded ? 'Smaller' : 'Larger'}>{expanded ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}</Button>}
          {onClose && <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close Motion AI"><X className="size-4" /></Button>}
        </div>
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
                  {t.links && t.links.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {t.links.map((l) => (
                        <Link key={l.href} to={l.href} onClick={() => onClose?.()}
                          className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2.5 py-1 text-[12.5px] font-medium text-accent-ink transition hover:bg-accent hover:text-white">
                          Open {l.label}<ArrowUpRight className="size-3.5" />
                        </Link>
                      ))}
                    </div>
                  )}
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
          placeholder="Ask about sales, stock, expiry, cash…" title="Enter to send, Shift+Enter for a new line"
          value={draft} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(draft) } }} />
        <Button type="submit" variant="primary" loading={send.isPending} disabled={draft.trim().length < 2} aria-label="Send"><SendHorizontal className="size-4" /></Button>
      </form>
      <p className="px-5 pb-3 text-[11.5px] text-muted">Motion AI can make mistakes. Check important figures in Reports before acting. Questions are logged for audit.</p>
    </div>
  )
}

/** Floating "Ask Motion AI" launcher, available on every back-office page. */
export function AskMotionFloater() {
  const { can } = useAuth()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  if (!can('insights.view') || location.pathname.startsWith('/pos')) return null
  return (
    <>
      {open && (
        <div role="dialog" aria-label="Ask Motion AI"
          className={clsx('fixed z-50 flex flex-col overflow-hidden border border-line bg-surface shadow-2xl',
            'inset-0 sm:inset-auto sm:bottom-24 sm:right-6 sm:rounded-2xl',
            expanded ? 'sm:h-[min(820px,calc(100vh-8rem))] sm:w-[min(760px,calc(100vw-3rem))]' : 'sm:h-[min(620px,calc(100vh-8rem))] sm:w-[420px]')}>
          <ChatPanel onClose={() => setOpen(false)} expanded={expanded} onToggleExpand={() => setExpanded((v) => !v)} />
        </div>
      )}
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className={clsx('fixed bottom-5 right-5 z-50 items-center gap-3 rounded-full border border-white/10 bg-[#0f1b2d] py-2 pl-2 pr-5 text-[15px] font-semibold text-white shadow-xl shadow-black/25 transition hover:-translate-y-0.5 hover:bg-[#13223a] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
          open ? 'hidden sm:flex' : 'flex')}>
        <span className="grid size-10 place-items-center rounded-full bg-gradient-to-br from-[#f2994a] via-[#c86dd7] to-[#4f8df7]">
          {open ? <X className="size-5" /> : <Sparkles className="size-5" />}
        </span>
        Ask Motion AI
      </button>
    </>
  )
}
