import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeftRight, Banknote, ClipboardCheck, Coins, Landmark, PackageX, Percent, Receipt, ShoppingBasket,
  Sparkles, TrendingUp, Truck, Wallet,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { ROLE_LABELS, useAuth } from '../auth/auth'
import { BarList, TrendChart, type SeriesPoint } from '../components/charts'
import { Badge, Card, CardHeader, Empty, ErrorBox, Kpi, LoadingBlock, PageHeader, StatusChip, Table, Td, Th } from '../components/ui'
import { dateTime, humanize, money, num, pct, qty, relative } from '../lib/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Dash = Record<string, any>

function trend(today: unknown, base: unknown) {
  const b = num(base)
  if (!b) return null
  const ch = ((num(today) - b) / b) * 100
  return { value: `${Math.abs(ch).toFixed(0)}% vs 7-day avg`, up: ch >= 0 }
}

export default function DashboardPage() {
  const { me, branchId } = useAuth()
  const q = useQuery({ queryKey: ['dashboard', branchId], queryFn: () => api.get<Dash>('/api/dashboard', { branch_id: branchId }), refetchInterval: 120_000 })
  if (q.isLoading) return <LoadingBlock rows={8} />
  if (q.error) return <ErrorBox error={q.error} />
  const d = q.data!
  const role = d.role as string
  const greeting = `Good ${new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 17 ? 'afternoon' : 'evening'}, ${me?.full_name.split(' ')[0]}`
  return (
    <div>
      <PageHeader title={greeting} subtitle={<>{ROLE_LABELS[role]} overview · updated {dateTime(d.generated_at)}</>} />
      <TodayKpis d={d} />
      {role === 'owner' && <OwnerView d={d} />}
      {role === 'general_manager' && <GmView d={d} />}
      {role === 'accountant' && <AccountantView d={d} />}
      {role === 'operations_manager' && <OpsView d={d} />}
      {role === 'inventory_manager' && <InventoryView d={d} />}
      {role === 'branch_manager' && <BranchView d={d} />}
    </div>
  )
}

function TodayKpis({ d }: { d: Dash }) {
  const k = d.kpis
  if (!k) return null
  return (
    <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Kpi label="Sales today (incl. VAT)" value={money(k.today.gross_sales)} icon={<TrendingUp className="size-4" />} trend={trend(k.today.gross_sales, k.avg_daily_7d)} />
      <Kpi label="Transactions" value={num(k.today.sales_count).toLocaleString()} hint={`${num(k.yesterday.sales_count)} yesterday`} icon={<Receipt className="size-4" />} />
      <Kpi label="Average basket" value={money(k.today.avg_basket)} hint={`${money(k.yesterday.avg_basket)} yesterday`} icon={<ShoppingBasket className="size-4" />} />
      <Kpi label="Refunds · discounts today" value={`${money(k.today.refunds)}`} hint={`${money(k.today.discounts)} discounts`} tone={num(k.today.refunds) > 0 ? 'warn' : 'neutral'} icon={<Coins className="size-4" />} />
    </div>
  )
}

function Grid({ children }: { children: ReactNode }) {
  return <div className="grid gap-5 xl:grid-cols-3">{children}</div>
}

function SalesTrend({ d, span = 'xl:col-span-2' }: { d: Dash; span?: string }) {
  if (!d.series) return null
  return (
    <Card className={span}>
      <CardHeader title="Net sales & gross profit" subtitle="Last 30 days · posted ledger, ex-VAT" />
      <div className="px-4 pb-4 pt-2">
        <TrendChart data={d.series as SeriesPoint[]} series={[{ key: 'net_sales', label: 'Net sales' }, { key: 'gross_profit', label: 'Gross profit' }]} />
      </div>
    </Card>
  )
}

function PnlCard({ p, title = 'Month to date' }: { p: Dash | null | undefined; title?: string }) {
  if (!p) return null
  const rows: [string, unknown, string?][] = [
    ['Net sales', p.net_sales], ['Cost of goods sold', -num(p.cogs)], ['Gross profit', p.gross_profit, 'b'],
    ['Shrinkage', -num(p.shrinkage)], ['Operating expenses', -num(p.operating_expenses)], ['Other (cash over/short)', -num(p.other_operating)],
    ['Operating profit', p.operating_profit, 'b'],
  ]
  return (
    <Card>
      <CardHeader title={`Profit & loss — ${title}`} subtitle={<>Gross margin {pct(p.gross_margin_pct)} · <Link className="text-accent hover:underline" to="/finance">details</Link></>} icon={<Landmark className="size-4" />} />
      <dl className="divide-y divide-line px-5 py-1">
        {rows.map(([label, v, bold]) => (
          <div key={label} className={`flex justify-between py-2 text-[13.5px] ${bold ? 'font-semibold' : 'text-ink-2'}`}>
            <dt>{label}</dt><dd className={`tabular ${num(v) < 0 && bold ? 'text-bad' : ''}`}>{money(v)}</dd>
          </div>
        ))}
      </dl>
      <p className="px-5 pb-4 text-[12px] text-muted">Estimated net profit before income tax. Purchase-clearing timing differences are excluded.</p>
    </Card>
  )
}

function Approvals({ d }: { d: Dash }) {
  const a = d.approvals
  if (!a || a.actionable === undefined) return null
  return (
    <Card>
      <CardHeader title="Waiting for you" subtitle={`${a.actionable} to act on${a.overdue ? ` · ${a.overdue} overdue` : ''}`} icon={<ClipboardCheck className="size-4" />}
        action={<Link to="/approvals" className="text-[13px] font-medium text-accent hover:underline">Open inbox</Link>} />
      {a.top?.length ? (
        <ul className="divide-y divide-line">
          {a.top.map((r: Dash) => (
            <li key={r.id}>
              <Link to={`/approvals/${r.id}`} className="flex items-center justify-between gap-3 px-5 py-3 hover:bg-surface-2/60">
                <div className="min-w-0">
                  <div className="truncate text-[13.5px] font-medium">{r.title}</div>
                  <div className="text-[12px] text-muted">{r.ref} · {r.branch_name} · {r.status_label}</div>
                </div>
                <div className="text-right"><div className="tabular text-[13.5px] font-medium">{money(r.amount)}</div>{r.overdue && <Badge tone="bad">Overdue</Badge>}</div>
              </Link>
            </li>
          ))}
        </ul>
      ) : <Empty title="Nothing waiting" icon={<ClipboardCheck className="size-6" />}>New requests that need your decision will appear here.</Empty>}
    </Card>
  )
}

function Exceptions({ d }: { d: Dash }) {
  const e = d.exceptions
  if (!e) return null
  const items: { label: string; value: ReactNode; tone: 'bad' | 'warn' | 'neutral'; to: string }[] = [
    ...e.discrepancies.map((x: Dash) => ({ label: humanize(x.kind), value: `${x.n} open · ${money(x.value)}`, tone: 'warn' as const, to: x.kind === 'cash_variance' ? '/shifts' : '/stock?tab=discrepancies' })),
    { label: 'Interrupted shifts', value: e.interrupted_shifts.length, tone: e.interrupted_shifts.length ? 'bad' : 'neutral', to: '/shifts' },
    { label: 'Unreconciled cash variances', value: e.unreconciled_variances, tone: e.unreconciled_variances ? 'warn' : 'neutral', to: '/shifts' },
    { label: 'Shifts open now', value: e.open_shifts, tone: 'neutral', to: '/shifts' },
  ]
  return (
    <Card>
      <CardHeader title="Exceptions" subtitle="Items that need follow-up" icon={<AlertTriangle className="size-4" />} />
      <ul className="divide-y divide-line">
        {items.map((it) => (
          <li key={it.label}><Link to={it.to} className="flex items-center justify-between px-5 py-2.5 text-[13.5px] hover:bg-surface-2/60">
            <span className="text-ink-2">{it.label}</span>
            <span className={`tabular font-medium ${it.tone === 'bad' ? 'text-bad' : it.tone === 'warn' ? 'text-warn' : ''}`}>{it.value}</span>
          </Link></li>
        ))}
      </ul>
    </Card>
  )
}

function InsightList({ items, title = 'Motion Intelligence' }: { items?: Dash[]; title?: string }) {
  if (!items) return null
  return (
    <Card>
      <CardHeader title={title} subtitle="Rule-based review flags with evidence" icon={<Sparkles className="size-4" />}
        action={<Link to="/insights" className="text-[13px] font-medium text-accent hover:underline">All insights</Link>} />
      {items.length ? (
        <ul className="divide-y divide-line">
          {items.map((i) => (
            <li key={i.key} className="px-5 py-3">
              <div className="flex items-start gap-2"><StatusChip status={i.severity} /><span className="text-[13.5px] font-medium leading-snug">{i.title}</span></div>
              <p className="mt-1 text-[12.5px] text-muted">{i.detail}</p>
            </li>
          ))}
        </ul>
      ) : <Empty title="No open insights">Everything looks normal for the data available.</Empty>}
    </Card>
  )
}

function Ranking({ d }: { d: Dash }) {
  if (!d.ranking) return null
  return (
    <Card>
      <CardHeader title="Branch ranking" subtitle="Month to date · net sales (ex-VAT)" />
      <div className="px-4 pb-4"><BarList data={d.ranking.map((b: Dash) => ({ name: b.name, value: num(b.net_sales) }))} label="Net sales" /></div>
      <Table>
        <thead><tr><Th>Branch</Th><Th align="right">Gross margin</Th><Th align="right">Operating profit</Th></tr></thead>
        <tbody>{d.ranking.map((b: Dash) => <tr key={b.code}><Td>{b.name}</Td><Td align="right">{pct(b.gross_margin_pct)}</Td><Td align="right" className={num(b.operating_profit) < 0 ? 'text-bad' : ''}>{money(b.operating_profit)}</Td></tr>)}</tbody>
      </Table>
    </Card>
  )
}

function CashCard({ cash }: { cash: Dash }) {
  if (!cash) return null
  return (
    <Card>
      <CardHeader title="Cash position" subtitle={`Total ${money(cash.total)}`} icon={<Banknote className="size-4" />} />
      <dl className="divide-y divide-line px-5 py-1">
        {cash.accounts.map((a: Dash) => <div key={a.code} className="flex justify-between gap-3 py-2 text-[13px]"><dt className="text-ink-2">{a.name}</dt><dd className="tabular font-medium">{money(a.balance)}</dd></div>)}
      </dl>
      <p className="px-5 pb-4 text-[12px] text-muted">{cash.note}</p>
    </Card>
  )
}

function Recovery({ r }: { r: Dash }) {
  if (!r) return null
  return (
    <Card>
      <CardHeader title="Capital recovery" subtitle="Cash generated vs original investment" icon={<Wallet className="size-4" />}
        action={<Link to="/finance?tab=recovery" className="text-[13px] font-medium text-accent hover:underline">Details</Link>} />
      <div className="px-5 py-4">
        {r.status === 'no_investment' ? <p className="text-sm text-muted">{r.message}</p> : (
          <>
            <div className="flex items-baseline justify-between"><span className="text-[13px] text-muted">Recovered</span><span className="tabular text-lg font-semibold">{pct(r.recovered_pct)}</span></div>
            <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuenow={num(r.recovered_pct)} aria-valuemin={0} aria-valuemax={100} aria-label="Investment recovered">
              <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, num(r.recovered_pct)))}%` }} />
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-[13px]">
              <div><div className="text-muted">Invested</div><div className="tabular font-medium">{money(r.investment_total)}</div></div>
              <div><div className="text-muted">Still to recover</div><div className="tabular font-medium">{money(r.unrecovered)}</div></div>
            </div>
            <p className="mt-3 text-[12.5px] text-muted">{r.status === 'projected' ? `Indicative recovery date ${r.estimated_recovery_date} (high uncertainty).` : r.message}</p>
            {r.test_data && <p className="mt-1 text-[12px] font-medium text-warn">Investment figure is TEST DATA.</p>}
          </>
        )}
      </div>
    </Card>
  )
}

function LowStock({ items, title = 'Low stock' }: { items?: Dash[]; title?: string }) {
  if (!items) return null
  return (
    <Card>
      <CardHeader title={title} subtitle="At or below reorder point" icon={<PackageX className="size-4" />}
        action={<Link to="/stock?low=1" className="text-[13px] font-medium text-accent hover:underline">Stock</Link>} />
      {items.length ? (
        <Table><thead><tr><Th>Product</Th><Th>Branch</Th><Th align="right">On hand</Th><Th align="right">Reorder at</Th></tr></thead>
          <tbody>{items.map((i) => <tr key={`${i.branch}-${i.product_id}`}><Td><Link className="hover:underline" to={`/stock/products/${i.product_id}`}>{i.name}</Link></Td><Td>{i.branch}</Td><Td align="right" className={num(i.on_hand) <= 0 ? 'font-semibold text-bad' : ''}>{qty(i.on_hand, i.uom)}</Td><Td align="right">{qty(i.reorder_point, i.uom)}</Td></tr>)}</tbody>
        </Table>
      ) : <Empty title="Stock is healthy">No product is at its reorder point.</Empty>}
    </Card>
  )
}

function Transfers({ items }: { items?: Dash[] }) {
  if (!items) return null
  return (
    <Card>
      <CardHeader title="Transfers in progress" icon={<ArrowLeftRight className="size-4" />}
        action={<Link to="/transfers" className="text-[13px] font-medium text-accent hover:underline">All transfers</Link>} />
      {items.length ? (
        <ul className="divide-y divide-line">{items.map((t) => (
          <li key={t.id}><Link to={`/transfers/${t.id}`} className="flex items-center justify-between px-5 py-2.5 hover:bg-surface-2/60">
            <div><div className="text-[13.5px] font-medium">{t.ref}</div><div className="text-[12px] text-muted">{t.from_branch} → {t.to_branch}</div></div>
            <StatusChip status={t.status} />
          </Link></li>))}</ul>
      ) : <Empty title="No transfers in progress" />}
    </Card>
  )
}

function OwnerView({ d }: { d: Dash }) {
  return (
    <div className="space-y-5">
      <Grid><SalesTrend d={d} /><PnlCard p={d.pnl_month} /></Grid>
      <Grid><Ranking d={d} /><CashCard cash={d.cash} /><Recovery r={d.recovery} /></Grid>
      <Grid><Approvals d={d} /><Exceptions d={d} /><InsightList items={d.insights} /></Grid>
      {d.security?.length > 0 && (
        <Card>
          <CardHeader title="Security alerts" subtitle="Last 24 hours – 7 days" icon={<AlertTriangle className="size-4" />}
            action={<Link to="/admin/users" className="text-[13px] font-medium text-accent hover:underline">Users & access</Link>} />
          <ul className="divide-y divide-line">{d.security.map((s: Dash, i: number) => (
            <li key={i} className="flex items-center justify-between gap-3 px-5 py-2.5 text-[13.5px]"><span className="flex items-center gap-2"><StatusChip status={s.severity} />{s.title}</span><span className="text-[12px] text-muted">{relative(s.at)}</span></li>
          ))}</ul>
        </Card>
      )}
    </div>
  )
}

function GmView({ d }: { d: Dash }) {
  return (
    <div className="space-y-5">
      <Grid><SalesTrend d={d} /><PnlCard p={d.pnl_month} /></Grid>
      <Grid><Ranking d={d} /><Approvals d={d} /><Exceptions d={d} /></Grid>
      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader title="Cashier performance" subtitle="Last 7 days" />
          <Table><thead><tr><Th>Cashier</Th><Th align="right">Sales</Th><Th align="right">Value</Th><Th align="right">Refunds</Th><Th align="right">Discounts</Th></tr></thead>
            <tbody>{(d.cashiers ?? []).map((c: Dash) => <tr key={c.cashier}><Td>{c.cashier.replace(/\s*\(TEST.*\)/, '')}</Td><Td align="right">{c.sales}</Td><Td align="right">{money(c.amount)}</Td><Td align="right">{c.refunds}</Td><Td align="right">{money(c.discounts)}</Td></tr>)}</tbody>
          </Table>
        </Card>
        <InsightList items={d.insights} />
      </div>
    </div>
  )
}

function AccountantView({ d }: { d: Dash }) {
  return (
    <div className="space-y-5">
      <Grid><SalesTrend d={d} /><PnlCard p={d.pnl_month} /></Grid>
      <Grid>
        <CashCard cash={d.cash} />
        <Card>
          <CardHeader title="Payables" subtitle={`Supplier balance ${money(d.payables?.supplier_balance)}`} icon={<Wallet className="size-4" />}
            action={<Link to="/payables" className="text-[13px] font-medium text-accent hover:underline">Open</Link>} />
          <dl className="divide-y divide-line px-5 py-1">
            {(d.payables?.by_state ?? []).map((s: Dash) => <div key={s.state} className="flex justify-between py-2 text-[13px]"><dt><StatusChip status={s.state} /> <span className="text-muted">× {s.n}</span></dt><dd className="tabular font-medium">{money(s.total)}</dd></div>)}
          </dl>
          {d.drafts?.length > 0 && <div className="border-t border-line px-5 py-3 text-[13px]"><b>{d.drafts.length}</b> draft supplier bill(s) awaiting verification and posting.</div>}
          <div className="border-t border-line px-5 py-3 text-[12.5px] text-muted">Unverified mobile money/card sales (7 days): <b className="text-ink">{d.unverified?.n}</b> · {money(d.unverified?.v)}</div>
        </Card>
        <Approvals d={d} />
      </Grid>
      <Exceptions d={d} />
    </div>
  )
}

function OpsView({ d }: { d: Dash }) {
  const pipe = d.pipeline
  return (
    <div className="space-y-5">
      <Grid>
        <Card className="xl:col-span-2">
          <CardHeader title="Procurement & transfer pipeline" subtitle={`Last 60 days · average approval time ${pipe?.avg_approval_hours ?? '—'} h`} icon={<Truck className="size-4" />}
            action={<Link to="/procurement" className="text-[13px] font-medium text-accent hover:underline">Procurement</Link>} />
          <Table><thead><tr><Th>Type</Th><Th>Status</Th><Th>Fulfilment</Th><Th align="right">Count</Th><Th align="right">Value</Th></tr></thead>
            <tbody>{(pipe?.by_status ?? []).map((r: Dash, i: number) => <tr key={i}><Td>{humanize(r.action_type)}</Td><Td><StatusChip status={r.status} /></Td><Td>{r.fulfilment ? <StatusChip status={r.fulfilment} /> : '—'}</Td><Td align="right">{r.n}</Td><Td align="right">{money(r.amount)}</Td></tr>)}</tbody>
          </Table>
        </Card>
        <Approvals d={d} />
      </Grid>
      <Grid><Transfers items={d.transfers} /><LowStock items={d.low_stock} /><InsightList items={d.insights} title="Rebalancing & reorder suggestions" /></Grid>
    </div>
  )
}

function InventoryView({ d }: { d: Dash }) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {d.stock?.branches.map((b: Dash) => <Kpi key={b.code} label={`${b.name} stock value`} value={b.value === null ? '—' : money(b.value)} hint={`${b.sku_count} products in stock${b.negative_lines ? ` · ${b.negative_lines} negative` : ''}`} />)}
      </div>
      <Grid><LowStock items={d.low_stock} /><Transfers items={d.transfers} /><Approvals d={d} /></Grid>
      <InsightList items={d.insights} title="Expiry, reorder and slow-moving stock" />
    </div>
  )
}

function BranchView({ d }: { d: Dash }) {
  return (
    <div className="space-y-5">
      <Grid><SalesTrend d={d} /><PnlCard p={d.pnl_month} title="Branch, month to date" /></Grid>
      <Grid>
        <Card>
          <CardHeader title="Shifts" subtitle="Most recent" action={<Link to="/shifts" className="text-[13px] font-medium text-accent hover:underline">All shifts</Link>} />
          <ul className="divide-y divide-line">{(d.shifts ?? []).slice(0, 8).map((s: Dash) => (
            <li key={s.id} className="flex items-center justify-between gap-2 px-5 py-2.5 text-[13px]">
              <div><div className="font-medium">{s.register_name} · {s.cashier_name.replace(/\s*\(TEST.*\)/, '')}</div><div className="text-[12px] text-muted">{dateTime(s.opened_at)}</div></div>
              <div className="flex items-center gap-2">{num(s.variance) !== 0 && <span className={`tabular text-[12.5px] ${num(s.variance) < 0 ? 'text-bad' : 'text-warn'}`}>{money(s.variance, { sign: true })}</span>}<StatusChip status={s.interrupted ? 'interrupted' : s.status} /></div>
            </li>))}</ul>
        </Card>
        <Approvals d={d} />
        <Exceptions d={d} />
      </Grid>
      <LowStock items={d.low_stock} />
      {!d.pnl_month && <Card className="p-5 text-sm text-muted"><Percent className="mr-1 inline size-4" />Branch financials are hidden for your role.</Card>}
    </div>
  )
}
