import { Table2, LineChart as LineIcon } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { compact, money, shortDay } from '../lib/format'
import { Table, Td, Th } from './ui'

// Recharts writes colours as SVG attributes, which cannot resolve CSS variables,
// so read the (validated) tokens and re-read them when the theme changes.
const TOKENS = ['chart-1', 'chart-2', 'chart-3', 'chart-4', 'chart-5', 'line', 'muted', 'surface', 'ink'] as const
type Palette = Record<(typeof TOKENS)[number], string>

function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement)
  return Object.fromEntries(TOKENS.map((t) => [t, cs.getPropertyValue(`--color-${t}`).trim() || '#888'])) as Palette
}

export function useChartPalette(): Palette {
  const [p, setP] = useState<Palette>(() => readPalette())
  useEffect(() => {
    const obs = new MutationObserver(() => setP(readPalette()))
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => obs.disconnect()
  }, [])
  return p
}

function TooltipBox({ title, rows }: { title: string; rows: { label: string; value: string; color: string }[] }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] shadow-[var(--shadow-pop)]">
      <div className="mb-1 font-medium text-ink">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2 text-ink-2">
          <span className="inline-block size-2.5 rounded-sm" style={{ background: r.color }} aria-hidden />
          <span className="min-w-[90px]">{r.label}</span>
          <span className="tabular ml-auto font-medium text-ink">{r.value}</span>
        </div>
      ))}
    </div>
  )
}

export function ChartFrame({ children, table, height = 240 }: { children: ReactNode; table: ReactNode; height?: number }) {
  const [asTable, setAsTable] = useState(false)
  return (
    <div className="relative">
      <button onClick={() => setAsTable((v) => !v)} className="absolute -top-1 right-0 z-10 inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12px] text-muted hover:bg-surface-2 hover:text-ink"
        aria-pressed={asTable} aria-label={asTable ? 'Show chart' : 'Show as table'}>
        {asTable ? <LineIcon className="size-3.5" /> : <Table2 className="size-3.5" />}{asTable ? 'Chart' : 'Table'}
      </button>
      {asTable ? <div className="max-h-[320px] overflow-y-auto pt-6">{table}</div> : <div style={{ height }} className="pt-6">{children}</div>}
    </div>
  )
}

export interface SeriesPoint { day: string; [k: string]: string | number | Record<string, unknown> }

/** Daily trend for up to 2 money series sharing one axis (UGX). */
export function TrendChart({ data, series, height = 240 }: {
  data: SeriesPoint[]; series: { key: string; label: string }[]; height?: number
}) {
  const p = useChartPalette()
  const colors = [p['chart-1'], p['chart-2']]
  const rows = data.map((d) => ({ day: d.day, ...Object.fromEntries(series.map((s) => [s.key, Number(d[s.key] ?? 0)])) }))
  return (
    <>
      {series.length > 1 && (
        <div className="mb-1 flex gap-4 text-[12.5px] text-ink-2" aria-label="Legend">
          {series.map((s, i) => (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-4 rounded" style={{ background: colors[i] }} />{s.label}
            </span>
          ))}
        </div>
      )}
      <ChartFrame height={height} table={
        <Table><thead><tr><Th>Day</Th>{series.map((s) => <Th key={s.key} align="right">{s.label}</Th>)}</tr></thead>
          <tbody>{rows.map((r) => <tr key={r.day}><Td>{shortDay(r.day)}</Td>{series.map((s) => <Td key={s.key} align="right">{money((r as Record<string, unknown>)[s.key])}</Td>)}</tr>)}</tbody>
        </Table>}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={rows} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
            <defs>
              {series.map((s, i) => (
                <linearGradient key={s.key} id={`g-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colors[i]} stopOpacity={i === 0 ? 0.18 : 0.06} />
                  <stop offset="100%" stopColor={colors[i]} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid vertical={false} stroke={p.line} strokeDasharray="3 4" />
            <XAxis dataKey="day" tickFormatter={shortDay} tick={{ fill: p.muted, fontSize: 11.5 }} tickLine={false} axisLine={false} minTickGap={24} />
            <YAxis tickFormatter={(v) => compact(v)} tick={{ fill: p.muted, fontSize: 11.5 }} tickLine={false} axisLine={false} width={44} />
            <Tooltip cursor={{ stroke: p.muted, strokeWidth: 1, strokeDasharray: '3 3' }}
              content={(props) => props.active && props.payload?.length ? (
                <TooltipBox title={shortDay(String(props.label))}
                  rows={props.payload.map((pl, i) => ({ label: series.find((s) => s.key === pl.dataKey)?.label ?? String(pl.dataKey), value: money(pl.value), color: colors[i] }))} />
              ) : null} />
            {series.map((s, i) => (
              <Area key={s.key} type="monotone" dataKey={s.key} stroke={colors[i]} strokeWidth={2} fill={`url(#g-${s.key})`}
                dot={false} activeDot={{ r: 4, stroke: p.surface, strokeWidth: 2 }} isAnimationActive={false} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </ChartFrame>
    </>
  )
}

/** Horizontal bars: one money measure per category (single series -> no legend). */
export function BarList({ data, label, height }: { data: { name: string; value: number }[]; label: string; height?: number }) {
  const p = useChartPalette()
  const h = height ?? Math.max(120, data.length * 38 + 20)
  return (
    <ChartFrame height={h} table={
      <Table><thead><tr><Th>Name</Th><Th align="right">{label}</Th></tr></thead>
        <tbody>{data.map((d) => <tr key={d.name}><Td>{d.name}</Td><Td align="right">{money(d.value)}</Td></tr>)}</tbody></Table>}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 64, left: 0, bottom: 0 }} barCategoryGap={10}>
          <CartesianGrid horizontal={false} stroke={p.line} strokeDasharray="3 4" />
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="name" width={130} tick={{ fill: p.ink, fontSize: 12.5 }} tickLine={false} axisLine={false} />
          <Tooltip cursor={{ fill: p.line, opacity: 0.35 }}
            content={(props) => props.active && props.payload?.length ? (
              <TooltipBox title={String(props.label)} rows={[{ label, value: money(props.payload[0].value), color: p['chart-1'] }]} />
            ) : null} />
          <Bar dataKey="value" fill={p['chart-1']} radius={[0, 4, 4, 0]} isAnimationActive={false}
            label={{ position: 'right', fill: p.muted, fontSize: 12, formatter: (v: unknown) => compact(v) }} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}
