import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, ErrorBox, Field, Input, Notice, PageHeader, useToast } from '../components/ui'

const schema = z.object({
  current_password: z.string().min(1, 'Required'),
  new_password: z.string().min(10, 'At least 10 characters').regex(/[A-Za-z]/, 'Include letters').regex(/\d/, 'Include digits'),
  confirm: z.string(),
}).refine((v) => v.new_password === v.confirm, { path: ['confirm'], message: 'Passwords do not match' })
type Form = z.infer<typeof schema>

export function ChangePasswordPage() {
  const { me, refresh } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const [error, setError] = useState<unknown>(null)
  const { register, handleSubmit, formState, reset } = useForm<Form>({ resolver: zodResolver(schema) })
  const onSubmit = handleSubmit(async ({ current_password, new_password }) => {
    setError(null)
    try {
      await api.post('/api/auth/change-password', { current_password, new_password })
      reset()
      toast('good', 'Password changed. Other devices were signed out.')
      await refresh()
      navigate('/')
    } catch (e) {
      setError(e)
    }
  })
  return (
    <div className="mx-auto max-w-lg">
      <PageHeader title="Change password" subtitle="Choose a password only you know. It must be at least 10 characters with letters and digits." />
      {me?.must_change_password && <div className="mb-4"><Notice tone="warn">You are using a temporary password. Set your own to continue.</Notice></div>}
      <Card className="p-5">
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <Field label="Current password" htmlFor="cur" error={formState.errors.current_password?.message}>
            <Input id="cur" type="password" autoComplete="current-password" {...register('current_password')} />
          </Field>
          <Field label="New password" htmlFor="new" error={formState.errors.new_password?.message}>
            <Input id="new" type="password" autoComplete="new-password" {...register('new_password')} />
          </Field>
          <Field label="Confirm new password" htmlFor="conf" error={formState.errors.confirm?.message}>
            <Input id="conf" type="password" autoComplete="new-password" {...register('confirm')} />
          </Field>
          <ErrorBox error={error} />
          <Button type="submit" variant="primary" loading={formState.isSubmitting}>Update password</Button>
        </form>
      </Card>
    </div>
  )
}
