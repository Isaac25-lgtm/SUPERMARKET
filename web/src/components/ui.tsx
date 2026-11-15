import * as Dialog from '@radix-ui/react-dialog'
import clsx from 'clsx'
import { AlertTriangle, CheckCircle2, Info, Loader2, X } from 'lucide-react'
import {
  createContext, forwardRef, useCallback, useContext, useMemo, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import { ApiError } from '../api/client'
import { humanize } from '../lib/format'

// ---------------------------------------------------------------- buttons

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'subtle'
type Size = 'sm' | 'md' | 'lg' | 'xl'

const variants: Record<Variant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-strong shadow-sm dark:text-canvas',
  secondary: 'bg-surface text-ink border border-line-strong hover:bg-surface-2',
  ghost: 'text-ink-2 hover:bg-surface-2 hover:text-ink',
  subtle: 'bg-accent-soft text-accent-ink hover:brightness-95',
  danger: 'bg-bad text-white hover:brightness-95 shadow-sm',
}
const sizes: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5',
  md: 'h-9 px-3.5 text-sm gap-2',
  lg: 'h-11 px-5 text-[15px] gap-2',
  xl: 'h-14 px-6 text-base gap-2.5',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  icon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading, icon, className, children, disabled, ...rest }, ref,
) {
  return (
    <button
      ref={ref}
      className={clsx(
        'inline-flex select-none items-center justify-center rounded-lg font-medium transition',
        'disabled:cursor-not-allowed disabled:opacity-50', variants[variant], sizes[size], className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  )
})

// ---------------------------------------------------------------- layout primitives

export function Card({ className, children, ...rest }: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx('rounded-[var(--radius-card)] border border-line bg-surface shadow-[var(--shadow-card)]', className)} {...rest}>
      {children}
    </div>
  )
}

export function CardHeader({ title, subtitle, action, icon }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
      <div className="flex min-w-0 items-start gap-2.5">
        {icon && <span className="mt-0.5 text-muted">{icon}</span>}
        <div className="min-w-0">
          <h3 className="truncate text-[15px] font-semibold tracking-tight">{title}</h3>
          {subtitle && <p className="mt-0.5 text-[13px] text-muted">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Kpi({ label, value, hint, trend, tone = 'neutral', icon }: {
  label: string; value: ReactNode; hint?: ReactNode; trend?: { value: string; up: boolean } | null
  tone?: 'neutral' | 'good' | 'bad' | 'warn'; icon?: ReactNode
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between text-[13px] text-muted">
        <span>{label}</span>
        {icon && <span className="text-muted/80">{icon}</span>}
      </div>
      <div className={clsx('tabular mt-1.5 text-[22px] font-semibold tracking-tight',
        tone === 'good' && 'text-good', tone === 'bad' && 'text-bad', tone === 'warn' && 'text-warn')}>{value}</div>
      <div className="mt-1 flex items-center gap-2 text-[12.5px] text-muted">
        {trend && (
          <span className={clsx('rounded-md px-1.5 py-0.5 font-medium', trend.up ? 'bg-good-soft text-good' : 'bg-bad-soft text-bad')}>
            {trend.up ? '▲' : '▼'} {trend.value}
          </span>
        )}
        {hint}
      </div>
    </Card>
  )
}

const tones = {
  neutral: 'bg-surface-2 text-ink-2 border-line',
  good: 'bg-good-soft text-good border-transparent',
  warn: 'bg-warn-soft text-warn border-transparent',
  bad: 'bg-bad-soft text-bad border-transparent',
  info: 'bg-info-soft text-info border-transparent',
  accent: 'bg-accent-soft text-accent-ink border-transparent',
}
export type Tone = keyof typeof tones

export function Badge({ tone = 'neutral', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={clsx('inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[12px] font-medium', tones[tone], className)}>
      {children}
    </span>
  )
}

const STATUS_TONES: Record<string, Tone> = {
  pending: 'warn', approved: 'info', executed: 'good', rejected: 'bad', returned: 'warn', cancelled: 'neutral',
  open: 'info', closed: 'neutral', reconciled: 'good', interrupted: 'bad', requested: 'warn', dispatched: 'info',
  received: 'good', received_with_discrepancy: 'warn', posted: 'good', draft: 'neutral', paid: 'good', validated: 'info',
  success: 'good', denied: 'bad', failure: 'bad', resolved: 'good', pending_approval: 'warn', new: 'accent',
  acknowledged: 'neutral', dismissed: 'neutral', high: 'bad', medium: 'warn', low: 'neutral', critical: 'bad',
  posted_with_conflict: 'warn', active: 'good', suspended: 'bad',
}

export function StatusChip({ status, label }: { status: string | null | undefined; label?: string }) {
  if (!status) return null
  return <Badge tone={STATUS_TONES[status] ?? 'neutral'}>{label ?? humanize(status)}</Badge>
}

export function TestDataBadge() {
  return (
    <span title="All figures on this system are synthetic TEST DATA" className="inline-flex items-center rounded-md border border-warn/30 bg-warn-soft px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider text-warn">
      Test data
    </span>
  )
}

// ---------------------------------------------------------------- states

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('skeleton', className)} aria-hidden />
}

export function LoadingBlock({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2.5 p-5" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-5" />)}
    </div>
  )
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      {icon && <div className="mb-2 text-muted">{icon}</div>}
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 max-w-md text-[13px] text-muted">{children}</div>}
    </div>
  )
}

export function ErrorBox({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null
  const msg = error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error)
  const forbidden = error instanceof ApiError && error.status === 403
  return (
    <div role="alert" className={clsx('flex items-start gap-2 rounded-lg border px-3 py-2.5 text-[13px]',
      forbidden ? 'border-warn/30 bg-warn-soft text-warn' : 'border-bad/25 bg-bad-soft text-bad', className)}>
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{msg}</span>
    </div>
  )
}

export function Notice({ tone = 'info', children }: { tone?: 'info' | 'warn' | 'good'; children: ReactNode }) {
  const Icon = tone === 'good' ? CheckCircle2 : tone === 'warn' ? AlertTriangle : Info
  return (
    <div className={clsx('flex items-start gap-2 rounded-lg px-3 py-2.5 text-[13px]', tones[tone])}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>{children}</div>
    </div>
  )
}

// ---------------------------------------------------------------- forms

export function Field({ label, error, hint, children, htmlFor, className }: {
  label: string; error?: string; hint?: ReactNode; children: ReactNode; htmlFor?: string; className?: string
}) {
  return (
    <div className={clsx('flex flex-col gap-1.5', className)}>
      <label htmlFor={htmlFor} className="text-[13px] font-medium text-ink-2">{label}</label>
      {children}
      {error ? <p className="text-[12.5px] text-bad">{error}</p> : hint ? <p className="text-[12.5px] text-muted">{hint}</p> : null}
    </div>
  )
}

const control = 'w-full rounded-lg border border-line-strong bg-surface px-3 text-sm text-ink placeholder:text-muted/70 transition focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 disabled:opacity-60'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={clsx(control, 'h-9', className)} {...rest} />
})

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, children, ...rest }, ref) {
  return <select ref={ref} className={clsx(control, 'h-9 pr-8', className)} {...rest}>{children}</select>
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={clsx(control, 'min-h-[84px] py-2', className)} {...rest} />
})

// ---------------------------------------------------------------- modal

export function Modal({ open, onOpenChange, title, description, children, footer, wide }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: string; description?: ReactNode
  children: ReactNode; footer?: ReactNode; wide?: boolean
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/35 backdrop-blur-[2px]" />
        <Dialog.Content
          className={clsx('fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-line bg-surface shadow-[var(--shadow-pop)]',
            wide ? 'max-w-3xl' : 'max-w-lg')}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div>
              <Dialog.Title className="text-base font-semibold">{title}</Dialog.Title>
              {description && <Dialog.Description className="mt-1 text-[13px] text-muted">{description}</Dialog.Description>}
            </div>
            <Dialog.Close className="rounded-md p-1 text-muted hover:bg-surface-2" aria-label="Close"><X className="size-4" /></Dialog.Close>
          </div>
          <div className="px-5 py-4">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3.5">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

// ---------------------------------------------------------------- tables

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx('overflow-x-auto', className)}>
      <table className="w-full border-collapse text-[13.5px]">{children}</table>
    </div>
  )
}
export function Th({ children, className, align = 'left' }: { children?: ReactNode; className?: string; align?: 'left' | 'right' | 'center' }) {
  return (
    <th scope="col" className={clsx('whitespace-nowrap border-b border-line bg-surface-2/60 px-4 py-2.5 text-[12px] font-semibold uppercase tracking-wide text-muted',
      align === 'right' && 'text-right', align === 'center' && 'text-center', className)}>{children}</th>
  )
}
export function Td({ children, className, align = 'left', mono, title }: { children?: ReactNode; className?: string; align?: 'left' | 'right' | 'center'; mono?: boolean; title?: string }) {
  return (
    <td title={title} className={clsx('border-b border-line px-4 py-2.5 align-middle', align === 'right' && 'text-right tabular', align === 'center' && 'text-center', mono && 'font-mono text-[12.5px]', className)}>{children}</td>
  )
}

// ---------------------------------------------------------------- tabs

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { value: T; label: string; count?: number }[] }) {
  return (
    <div role="tablist" className="inline-flex flex-wrap gap-1 rounded-xl border border-line bg-surface p-1">
      {items.map((it) => (
        <button key={it.value} role="tab" aria-selected={value === it.value} onClick={() => onChange(it.value)}
          className={clsx('rounded-lg px-3 py-1.5 text-[13px] font-medium transition',
            value === it.value ? 'bg-accent-soft text-accent-ink' : 'text-ink-2 hover:bg-surface-2')}>
          {it.label}
          {it.count !== undefined && <span className="ml-1.5 rounded-full bg-surface-2 px-1.5 text-[11px] text-muted">{it.count}</span>}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- toasts

interface Toast { id: number; tone: 'good' | 'bad' | 'info'; text: string }
const ToastCtx = createContext<(tone: Toast['tone'], text: string) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((tone: Toast['tone'], text: string) => {
    const id = Date.now() + Math.random()
    setItems((x) => [...x, { id, tone, text }])
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4500)
  }, [])
  const value = useMemo(() => push, [push])
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(380px,calc(100vw-32px))] flex-col gap-2" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={clsx('pointer-events-auto rounded-xl border px-4 py-3 text-sm shadow-[var(--shadow-pop)]',
            t.tone === 'good' && 'border-good/20 bg-surface text-ink', t.tone === 'bad' && 'border-bad/30 bg-bad-soft text-bad',
            t.tone === 'info' && 'border-line bg-surface text-ink')}>
            <div className="flex items-start gap-2">
              {t.tone === 'good' ? <CheckCircle2 className="mt-0.5 size-4 text-good" /> : t.tone === 'bad' ? <AlertTriangle className="mt-0.5 size-4" /> : <Info className="mt-0.5 size-4 text-info" />}
              <span>{t.text}</span>
            </div>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export function useToast() {
  return useContext(ToastCtx)
}

export function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : 'Something went wrong'
}
