// Thin fetch client. Same-origin cookies carry the session; the CSRF token is
// read from the non-HttpOnly motion_csrf cookie and echoed in a header on every
// state-changing request (the server checks both against the session).

export class ApiError extends Error {
  status: number
  code: string
  details?: Record<string, unknown>
  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

function csrfToken(): string | undefined {
  const m = document.cookie.match(/(?:^|;\s*)motion_csrf=([^;]+)/)
  return m ? decodeURIComponent(m[1]) : undefined
}

type Query = Record<string, string | number | boolean | null | undefined>

function withQuery(path: string, query?: Query): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

let onUnauthenticated: (() => void) | null = null
export function setUnauthenticatedHandler(fn: () => void) {
  onUnauthenticated = fn
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query, download = false): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') {
    const token = csrfToken()
    if (token) headers['X-CSRF-Token'] = token
  }
  let res: Response
  try {
    res = await fetch(withQuery(path, query), {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'network', 'Cannot reach the server. Check your connection.')
  }
  if (download && res.ok) {
    if (!res.headers.get('content-type')?.startsWith('text/csv')) {
      throw new ApiError(res.status, 'invalid_response', 'The server did not provide a CSV file.')
    }
    return await res.blob() as T
  }
  const text = await res.text()
  const data = text ? safeJson(text) : null
  if (!res.ok) {
    const err = (data ?? {}) as { error?: string; message?: string; details?: Record<string, unknown> }
    // A 401 from the identity/sign-in endpoints is an answer, not an expired session.
    const authProbe = /\/api\/auth\/(me|login|demo-login|demo-accounts)$/.test(path)
    if (res.status === 401 && onUnauthenticated && !authProbe) onUnauthenticated()
    throw new ApiError(res.status, err.error ?? 'error', err.message ?? `Request failed (${res.status})`, err.details)
  }
  return data as T
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return { message: text.slice(0, 200) }
  }
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>('GET', path, undefined, query),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body ?? {}),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body ?? {}),
  url: withQuery,
  downloadCsv: async (path: string, filename: string, query?: Query) => {
    const blob = await request<Blob>('POST', path, undefined, query, true)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
}

export function uuid(): string {
  if (crypto && 'randomUUID' in crypto) return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}
