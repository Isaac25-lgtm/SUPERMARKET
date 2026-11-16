import { useMutation } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import type { ReactNode } from 'react'
import { api } from '../api/client'
import { Button, useToast } from './ui'

type Query = Record<string, string | number | boolean | null | undefined>

/** Audited CSV export: a CSRF-protected POST that downloads the file (exports are not GET links). */
export function CsvButton({ path, filename, query, children = 'Export CSV' }: {
  path: string; filename: string; query?: Query; children?: ReactNode
}) {
  const toast = useToast()
  const m = useMutation({
    mutationFn: () => api.downloadCsv(path, filename, query),
    onError: (e: Error) => toast('bad', e.message || 'The export failed.'),
  })
  return <Button onClick={() => m.mutate()} loading={m.isPending} icon={<Download className="size-4" />}>{children}</Button>
}
