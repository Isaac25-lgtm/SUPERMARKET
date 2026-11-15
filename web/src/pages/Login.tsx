import { zodResolver } from '@hookform/resolvers/zod'
import { useQueryClient } from '@tanstack/react-query'
import { Lock, ShieldCheck, Store, WifiOff } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Navigate, useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, ErrorBox, Field, Input, TestDataBadge } from '../components/ui'

const schema = z.object({
  username: z.string().trim().min(1, 'Enter your username'),
  password: z.string().min(1, 'Enter your password'),
})
type Form = z.infer<typeof schema>

export function LoginPage() {
  const { me, meta } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [error, setError] = useState<unknown>(null)
  const { register, handleSubmit, formState } = useForm<Form>({ resolver: zodResolver(schema) })
  if (me) return <Navigate to="/" replace />
  const onSubmit = handleSubmit(async (values) => {
    setError(null)
    try {
      await api.post('/api/auth/login', values)
      await qc.invalidateQueries({ queryKey: ['me'] })
      navigate('/', { replace: true })
    } catch (e) {
      setError(e)
    }
  })
  return (
    <div className="grid min-h-full lg:grid-cols-[1.05fr_1fr]">
      <section className="relative hidden overflow-hidden bg-accent-ink p-12 text-white lg:flex lg:flex-col">
        <div className="absolute -right-24 -top-24 size-[420px] rounded-full bg-white/5" />
        <div className="absolute -bottom-32 -left-16 size-[360px] rounded-full bg-white/5" />
        <div className="relative flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="size-10" />
          <div>
            <div className="text-lg font-semibold">{meta?.brand ?? 'Motion Retail'}</div>
            <div className="text-[13px] text-white/70">Operations & intelligence platform by Motion Imprints</div>
          </div>
        </div>
        <div className="relative mt-auto max-w-md">
          <h2 className="text-[30px] font-semibold leading-tight tracking-tight">Every branch, every till, every shilling — accounted for.</h2>
          <ul className="mt-8 space-y-4 text-[14.5px] text-white/85">
            <li className="flex gap-3"><Store className="mt-0.5 size-5 shrink-0 text-emerald-300" />Fast checkout with stock and ledger updated in the same transaction.</li>
            <li className="flex gap-3"><ShieldCheck className="mt-0.5 size-5 shrink-0 text-emerald-300" />Maker-checker approvals and a tamper-resistant audit trail.</li>
            <li className="flex gap-3"><WifiOff className="mt-0.5 size-5 shrink-0 text-emerald-300" />Branch tills keep selling through internet outages and sync later.</li>
          </ul>
        </div>
      </section>
      <section className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-[380px]">
          <div className="mb-8 lg:hidden">
            <img src="/favicon.svg" alt="" className="size-10" />
            <div className="mt-3 text-lg font-semibold">{meta?.brand ?? 'Motion Retail'}</div>
          </div>
          <div className="flex items-center justify-between">
            <h1 className="text-[22px] font-semibold tracking-tight">Sign in</h1>
            {meta?.test_data && <TestDataBadge />}
          </div>
          <p className="mt-1 text-sm text-muted">Use the account your manager created for you.</p>
          <form onSubmit={onSubmit} className="mt-7 space-y-4" noValidate>
            <Field label="Username" htmlFor="username" error={formState.errors.username?.message}>
              <Input id="username" autoComplete="username" autoCapitalize="none" spellCheck={false} autoFocus {...register('username')} />
            </Field>
            <Field label="Password" htmlFor="password" error={formState.errors.password?.message}>
              <Input id="password" type="password" autoComplete="current-password" {...register('password')} />
            </Field>
            <ErrorBox error={error} />
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={formState.isSubmitting} icon={<Lock className="size-4" />}>
              Sign in
            </Button>
          </form>
          <p className="mt-8 text-[12.5px] leading-relaxed text-muted">
            Forgot your password? Ask the Head Manager to issue a temporary one — for security, resets are done in person, not by email.
            Repeated failed attempts temporarily lock the account.
          </p>
        </div>
      </section>
    </div>
  )
}
