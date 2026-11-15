// Display formatting only. Money arithmetic happens on the server (Decimal);
// the API returns amounts as strings which are parsed here purely for display.

const ugx = new Intl.NumberFormat('en-UG', { maximumFractionDigits: 0 })
const compactFmt = new Intl.NumberFormat('en-UG', { notation: 'compact', maximumFractionDigits: 1 })
const TZ = 'Africa/Kampala'

export function num(v: unknown): number {
  if (v === null || v === undefined || v === '') return 0
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : 0
}

export function money(v: unknown, opts: { sign?: boolean; currency?: boolean } = {}): string {
  const n = num(v)
  const s = ugx.format(Math.round(Math.abs(n)))
  const prefix = opts.currency === false ? '' : 'UGX '
  const sign = n < 0 ? '−' : opts.sign && n > 0 ? '+' : ''
  return `${sign}${prefix}${s}`
}

export function compact(v: unknown): string {
  return compactFmt.format(num(v))
}

export function qty(v: unknown, uom?: string | null): string {
  const n = num(v)
  const isWeight = uom === 'kg' || uom === 'g' || uom === 'l'
  const s = isWeight
    ? n.toLocaleString('en-UG', { maximumFractionDigits: 3 })
    : n.toLocaleString('en-UG', { maximumFractionDigits: Number.isInteger(n) ? 0 : 3 })
  return uom && uom !== 'u' ? `${s} ${uom}` : s
}

export function pct(v: unknown, digits = 1): string {
  if (v === null || v === undefined) return '—'
  return `${num(v).toFixed(digits)}%`
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  return d.toLocaleString('en-GB', { timeZone: TZ, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function date(v: string | null | undefined): string {
  if (!v) return '—'
  const d = v.length === 10 ? new Date(`${v}T12:00:00`) : new Date(v)
  return d.toLocaleDateString('en-GB', { timeZone: TZ, day: '2-digit', month: 'short', year: 'numeric' })
}

export function shortDay(v: string): string {
  const d = new Date(`${v.slice(0, 10)}T12:00:00`)
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
}

export function relative(v: string | null | undefined): string {
  if (!v) return 'never'
  const diff = (Date.now() - new Date(v).getTime()) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`
  return `${Math.floor(diff / 86400)} d ago`
}

export function humanize(code: string | null | undefined): string {
  if (!code) return ''
  return code.replace(/[._]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function todayISO(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: TZ })
}
