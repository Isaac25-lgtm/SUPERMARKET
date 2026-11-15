import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Download, Plus, XCircle } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { TrendChart } from '../components/charts'
import {
  Button, Card, CardHeader, Empty, ErrorBox, Field, Input, Kpi, LoadingBlock, Modal, Notice, PageHeader, Select, Table, Tabs, Td, Th,
  useToast,
} from '../components/ui'
import { date, money, num, pct, shortDay } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>
type Tab = 'pnl' | 'breakeven' | 'recovery' | 'cash' | 'ledger' | 'trial'

const PERIODS = [['today', 'Today'], ['yesterday', 'Yesterday'], ['week', 'This week'], ['month', 'Month to date'], ['last30', 'Last 30 days'], ['fytd', 'Year to date'], ['custom', 'Custom']]

function PeriodPicker({ period, setPeriod, from, to, setFrom, setTo }: { period: string; setPeriod: (v: string) => void; from: string; to: string; setFrom: (v: string) => void; setTo: (v: string) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select aria-label="Period" className="w-auto" value={period} onChange={(e) => setPeriod(e.target.value)}>{PERIODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
      {period === 'custom' && <><Input aria-label="From" type="date" className="w-auto" value={from} onChange={(e) => setFrom(e.target.value)} /><Input aria-label="To" type="date" className="w-auto" value={to} onChange={(e) => setTo(e.target.value)} /></>}
    </div>
  )
}

export default function FinancePage() {
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab) ?? 'pnl'
  const { can } = useAuth()
  const tabs: { value: Tab; label: string }[] = [
    { value: 'pnl', label: 'Profit & loss' }, { value: 'breakeven', label: 'Break-even' }, { value: 'recovery', label: 'Capital recovery' },
    { value: 'cash', label: 'Cash' }, ...(can('finance.ledger') ? [{ value: 'ledger' as Tab, label: 'Journal' }, { value: 'trial' as Tab, label: 'Trial balance' }] : []),
  ]
  return (
    <div>
      <PageHeader title="Profit, cash & performance" subtitle="All figures come from posted double-entry journal entries in the accounting engine. TEST DATA." />
      <div className="mb-5"><Tabs value={tab} onChange={(v) => setParams({ tab: v })} items={tabs} /></div>
      {tab === 'pnl' && <Pnl />}
      {tab === 'breakeven' && <BreakEven />}
      {tab === 'recovery' && <Recovery />}
      {tab === 'cash' && <Cash />}
      {tab === 'ledger' && <Ledger />}
      {tab === 'trial' && <Trial />}
    </div>
  )
}

const LINES: [string, string, boolean?][] = [
  ['net_sales', 'Net sales (ex-VAT)'], ['cogs', 'Cost of goods sold'], ['gross_profit', 'Gross profit', true], ['shrinkage', 'Shrinkage & write-offs'],
  ['operating_expenses', 'Operating expenses'], ['other_operating', 'Cash over/short & other'], ['operating_profit', 'Operating profit', true],
  ['estimated_net_profit', 'Estimated net profit (before tax)', true], ['purchase_clearing_timing', 'Purchase clearing (timing, excluded)'],
]

function Pnl() {
  const { branchId, can } = useAuth()
  const [period, setPeriod] = useState('month')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const enabled = period !== 'custom' || (!!from && !!to)
  const q = useQuery({ queryKey: ['pnl', period, from, to, branchId], queryFn: () => api.get<R>('/api/finance/pnl', { period, date_from: from, date_to: to, branch_id: branchId }), enabled })
  const series = useQuery({ queryKey: ['series', branchId], queryFn: () => api.get<{ series: R[] }>('/api/finance/series', { days: 60, branch_id: branchId }) })
  const d = q.data
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PeriodPicker {...{ period, setPeriod, from, to, setFrom, setTo }} />
        {can('reports.export') && period !== 'custom' && <a href={api.url('/api/reports/pnl.csv', { period, branch_id: branchId })}><Button icon={<Download className="size-4" />}>Export CSV</Button></a>}
      </div>
      {q.isLoading ? <LoadingBlock rows={8} /> : q.error ? <ErrorBox error={q.error} /> : d && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Kpi label="Net sales" value={money(d.consolidated.net_sales)} hint={d.period.label} />
            <Kpi label="Gross profit" value={money(d.consolidated.gross_profit)} hint={`margin ${pct(d.consolidated.gross_margin_pct)}`} />
            <Kpi label="Operating profit" value={money(d.consolidated.operating_profit)} tone={num(d.consolidated.operating_profit) < 0 ? 'bad' : 'good'} />
            <Kpi label="Branch totals reconcile" value={d.reconciles ? 'Yes' : 'No'} tone={d.reconciles ? 'good' : 'bad'} icon={d.reconciles ? <CheckCircle2 className="size-4 text-good" /> : <XCircle className="size-4 text-bad" />} hint="Σ branches = consolidated" />
          </div>
          <Card>
            <CardHeader title={`Profit & loss by branch — ${d.period.label}`} subtitle={`${date(d.period.from)} – ${date(d.period.to)}. Entries without a branch are shown as Head office.`} />
            <Table>
              <thead><tr><Th>Line</Th>{d.branches.map((b: R) => <Th key={b.code} align="right">{b.name}</Th>)}<Th align="right">Consolidated</Th></tr></thead>
              <tbody>
                {LINES.map(([key, label, bold]) => (
                  <tr key={key} className={bold ? 'bg-surface-2/40 font-semibold' : key === 'purchase_clearing_timing' ? 'text-muted' : ''}>
                    <Td>{label}</Td>
                    {d.branches.map((b: R) => <Td key={b.code} align="right" className={bold && num(b[key]) < 0 ? 'text-bad' : ''}>{money(b[key])}</Td>)}
                    <Td align="right" className={bold && num(d.consolidated[key]) < 0 ? 'text-bad' : ''}>{money(d.consolidated[key])}</Td>
                  </tr>
                ))}
                <tr><Td className="text-muted">Gross margin</Td>{d.branches.map((b: R) => <Td key={b.code} align="right" className="text-muted">{pct(b.gross_margin_pct)}</Td>)}<Td align="right" className="text-muted">{pct(d.consolidated.gross_margin_pct)}</Td></tr>
              </tbody>
            </Table>
            <div className="p-4"><Notice>Gross profit ≠ operating profit ≠ cash. Inter-branch transfers create no revenue. Income tax is not modelled, so net profit is an estimate before tax. Tax rates in this system are illustrative only.</Notice></div>
          </Card>
          <Card>
            <CardHeader title="Accounts in this period" subtitle="Drill into the journal for source documents." />
            <Table><thead><tr><Th>Account</Th><Th>P&L line</Th><Th align="right">Amount</Th></tr></thead>
              <tbody>{d.accounts.map((a: R) => <tr key={a.code}><Td mono>{a.code}</Td><Td>{a.name}<div className="text-[12px] text-muted">{LINES.find((l) => l[0] === a.line)?.[1]}</div></Td><Td align="right">{money(a.amount)}</Td></tr>)}</tbody></Table>
          </Card>
        </>
      )}
      {series.data && (
        <Card>
          <CardHeader title="Daily net sales & gross profit" subtitle="Last 60 days" />
          <div className="px-4 pb-4 pt-2"><TrendChart data={series.data.series as never} series={[{ key: 'net_sales', label: 'Net sales' }, { key: 'gross_profit', label: 'Gross profit' }]} height={260} /></div>
        </Card>
      )}
    </div>
  )
}

function BreakEven() {
  const { branchId } = useAuth()
  const [period, setPeriod] = useState('last30')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const q = useQuery({ queryKey: ['breakeven', period, from, to, branchId], queryFn: () => api.get<R>('/api/finance/break-even', { period, date_from: from, date_to: to, branch_id: branchId }), enabled: period !== 'custom' || (!!from && !!to) })
  const d = q.data
  return (
    <div className="space-y-5">
      <PeriodPicker {...{ period, setPeriod, from, to, setFrom, setTo }} />
      {q.isLoading ? <LoadingBlock /> : q.error ? <ErrorBox error={q.error} /> : d && (d.status !== 'ok' ? <Card className="p-6"><Empty title="Break-even cannot be computed">{d.message}</Empty></Card> : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Kpi label="Break-even sales" value={money(d.break_even_sales)} hint={d.period.label} />
            <Kpi label="Needed per day" value={money(d.break_even_daily)} hint={`actual ${money(d.actual_daily)}/day`} tone={num(d.actual_daily) >= num(d.break_even_daily) ? 'good' : 'bad'} />
            <Kpi label="Contribution margin" value={pct(num(d.contribution_margin_ratio) * 100)} hint="(sales − variable costs) / sales" />
            <Kpi label="Margin of safety" value={money(d.margin_of_safety)} hint={pct(d.margin_of_safety_pct)} tone={num(d.margin_of_safety) >= 0 ? 'good' : 'bad'} />
          </div>
          <Card>
            <CardHeader title="How this is calculated" subtitle="Operating break-even for the selected period (not investment payback)" />
            <dl className="grid gap-x-8 gap-y-2 px-5 py-4 text-[13.5px] sm:grid-cols-3">
              <div><dt className="text-muted">Net sales</dt><dd className="tabular font-medium">{money(d.net_sales)}</dd></div>
              <div><dt className="text-muted">Variable costs (COGS + shrinkage)</dt><dd className="tabular font-medium">{money(d.variable_costs)}</dd></div>
              <div><dt className="text-muted">Fixed costs (posted operating expenses)</dt><dd className="tabular font-medium">{money(d.fixed_costs)}</dd></div>
            </dl>
            <ul className="list-disc space-y-1 px-9 pb-5 text-[13px] text-ink-2">{d.assumptions.map((a: string) => <li key={a}>{a}</li>)}</ul>
            {!d.fixed_costs_complete && <div className="px-5 pb-5"><Notice tone="warn">No operating expenses were posted in this period, so break-even is understated.</Notice></div>}
          </Card>
        </>
      ))}
    </div>
  )
}

function Recovery() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({ queryKey: ['recovery'], queryFn: () => api.get<R>('/api/finance/capital-recovery') })
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ label: '', amount: '', invested_on: '', note: '' })
  const m = useMutation({ mutationFn: () => api.post('/api/finance/investments', f), onSuccess: () => { toast('good', 'Investment recorded.'); setOpen(false); qc.invalidateQueries({ queryKey: ['recovery'] }) } })
  if (q.isLoading) return <LoadingBlock />
  if (q.error) return <ErrorBox error={q.error} />
  const d = q.data!
  return (
    <div className="space-y-5">
      {d.status === 'no_investment' ? <Card className="p-6"><Empty title="No investment recorded">{d.message}</Empty></Card> : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Kpi label="Invested" value={money(d.investment_total)} hint={d.test_data ? 'TEST DATA' : undefined} />
            <Kpi label="Recovered (net cash)" value={money(d.recovered)} hint={pct(d.recovered_pct)} />
            <Kpi label="Still to recover" value={money(d.unrecovered)} />
            <Kpi label="Indicative recovery" value={d.status === 'projected' ? date(d.estimated_recovery_date) : 'Not enough data'} hint={d.status === 'projected' ? `${d.weeks_to_recover} weeks at ${money(d.avg_weekly_net_cash)}/wk` : d.message} tone={d.status === 'projected' ? 'neutral' : 'warn'} />
          </div>
          {d.series.length > 0 && (
            <Card>
              <CardHeader title="Cumulative net cash generated vs investment" subtitle={`Weekly · history available from ${date(d.data_from)} (${d.weeks_of_data} weeks)`} />
              <div className="px-4 pb-4 pt-2"><TrendChart data={d.series.map((w: R) => ({ day: w.week, cumulative: w.cumulative }))} series={[{ key: 'cumulative', label: 'Cumulative net cash' }]} height={240} /></div>
              <Table><thead><tr><Th>Week of</Th><Th align="right">Net cash</Th><Th align="right">Cumulative</Th><Th align="right">Unrecovered</Th></tr></thead>
                <tbody>{d.series.slice(-10).map((w: R) => <tr key={w.week}><Td>{shortDay(w.week)}</Td><Td align="right" className={num(w.net_cash) < 0 ? 'text-bad' : ''}>{money(w.net_cash)}</Td><Td align="right">{money(w.cumulative)}</Td><Td align="right">{money(w.unrecovered)}</Td></tr>)}</tbody></Table>
            </Card>
          )}
          <Card className="p-5"><div className="text-[14px] font-semibold">Assumptions</div><ul className="mt-2 list-disc space-y-1 pl-5 text-[13px] text-ink-2">{d.assumptions.map((a: string) => <li key={a}>{a}</li>)}</ul>{d.uncertainty && <p className="mt-2 text-[13px] font-medium text-warn">Uncertainty: {d.uncertainty}</p>}</Card>
        </>
      )}
      {can('finance.investment') && <Button icon={<Plus className="size-4" />} onClick={() => setOpen(true)}>Record investment</Button>}
      <Modal open={open} onOpenChange={setOpen} title="Record capital invested" description="Used only for the capital-recovery view; audited."
        footer={<><Button onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!f.label || !f.amount || !f.invested_on} onClick={() => m.mutate()}>Save</Button></>}>
        <div className="space-y-3">
          <Field label="Description" htmlFor="il"><Input id="il" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></Field>
          <Field label="Amount (UGX)" htmlFor="ia"><Input id="ia" inputMode="numeric" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value.replace(/[^\d]/g, '') })} /></Field>
          <Field label="Date invested" htmlFor="id"><Input id="id" type="date" value={f.invested_on} onChange={(e) => setF({ ...f, invested_on: e.target.value })} /></Field>
          <ErrorBox error={m.error} />
        </div>
      </Modal>
    </div>
  )
}

function Cash() {
  const q = useQuery({ queryKey: ['cash'], queryFn: () => api.get<R>('/api/finance/cash') })
  if (q.isLoading) return <LoadingBlock />
  if (q.error) return <ErrorBox error={q.error} />
  return (
    <Card>
      <CardHeader title="Cash & cash equivalents" subtitle={`Total ${money(q.data!.total)}`} />
      <Table><thead><tr><Th>Account</Th><Th align="right">Balance</Th></tr></thead><tbody>{q.data!.accounts.map((a: R) => <tr key={a.code}><Td><span className="font-mono text-[12.5px] text-muted">{a.code}</span> {a.name}</Td><Td align="right">{money(a.balance)}</Td></tr>)}</tbody></Table>
      <div className="p-4"><Notice tone="warn">{q.data!.note}</Notice></div>
    </Card>
  )
}

function Ledger() {
  const { branchId } = useAuth()
  const [account, setAccount] = useState('')
  const [days, setDays] = useState(7)
  const q = useQuery({ queryKey: ['ledger', account, days, branchId], queryFn: () => api.get<{ lines: R[] }>('/api/finance/ledger', { account_code: account || undefined, days, branch_id: branchId }) })
  return (
    <Card>
      <div className="flex flex-wrap gap-2 border-b border-line p-3">
        <Input aria-label="Account code" className="w-48" placeholder="Account code e.g. 4.1" value={account} onChange={(e) => setAccount(e.target.value)} />
        <Select aria-label="Days" className="w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}>{[1, 7, 14, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}</Select>
      </div>
      {q.isLoading ? <LoadingBlock /> : q.data?.lines.length ? (
        <Table><thead><tr><Th>Date</Th><Th>Entry</Th><Th>Journal</Th><Th>Branch</Th><Th>Account</Th><Th>Source</Th><Th align="right">Debit</Th><Th align="right">Credit</Th></tr></thead>
          <tbody>{q.data.lines.map((l, i) => <tr key={i}><Td>{date(l.date)}</Td><Td mono>{l.number}</Td><Td>{l.journal}</Td><Td>{l.branch_code ?? 'HQ'}</Td><Td><span className="font-mono text-[12px] text-muted">{l.account_code}</span> {l.account_name}</Td><Td className="text-[12px] text-muted">{l.origin?.split(',')[0]}{l.party ? ` · ${l.party}` : ''}</Td><Td align="right">{num(l.debit) ? money(l.debit, { currency: false }) : ''}</Td><Td align="right">{num(l.credit) ? money(l.credit, { currency: false }) : ''}</Td></tr>)}</tbody></Table>
      ) : <Empty title="No journal lines" />}
      <p className="p-4 text-[12.5px] text-muted">Posted entries are immutable in the accounting engine; corrections are made with reversing entries, never edits.</p>
    </Card>
  )
}

function Trial() {
  const q = useQuery({ queryKey: ['trial'], queryFn: () => api.get<R>('/api/finance/trial-balance') })
  if (q.isLoading) return <LoadingBlock />
  if (q.error) return <ErrorBox error={q.error} />
  const d = q.data!
  return (
    <Card>
      <CardHeader title="Trial balance (all posted entries)" subtitle={d.balanced ? 'Debits equal credits ✓' : 'NOT BALANCED — investigate'} />
      <Table><thead><tr><Th>Account</Th><Th align="right">Debit</Th><Th align="right">Credit</Th><Th align="right">Balance</Th></tr></thead>
        <tbody>{d.accounts.map((a: R) => <tr key={a.code}><Td><span className="font-mono text-[12.5px] text-muted">{a.code}</span> {a.name}</Td><Td align="right">{money(a.debit, { currency: false })}</Td><Td align="right">{money(a.credit, { currency: false })}</Td><Td align="right">{money(num(a.debit) - num(a.credit))}</Td></tr>)}
          <tr className="font-semibold"><Td>Total</Td><Td align="right">{money(d.total_debit)}</Td><Td align="right">{money(d.total_credit)}</Td><Td /></tr></tbody></Table>
    </Card>
  )
}
