import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, ApiError, setUnauthenticatedHandler } from '../api/client'

export interface Branch { id: number; code: string; name: string }
export interface Register { id: number; name: string; branch_id: number }
export interface Me {
  id: number; username: string; full_name: string; roles: string[]; permissions: string[]
  all_branches: boolean; branch_ids: number[]; must_change_password: boolean
  branches: Branch[]; registers: Register[]
}
export interface Meta { brand: string; company: string; currency: string; timezone: string; test_data: boolean; ai_llm_enabled: boolean; open_demo?: boolean; session_idle_minutes?: number; deployment?: string }

interface AuthState {
  me: Me | null
  meta: Meta | null
  loading: boolean
  can: (perm: string) => boolean
  hasRole: (role: string) => boolean
  branchId: number | null
  setBranchId: (id: number | null) => void
  refresh: () => Promise<void>
  logout: (reason?: string) => Promise<void>
}

const Ctx = createContext<AuthState | null>(null)
const BRANCH_KEY = 'motion.branch'

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('/api/meta'), staleTime: Infinity, retry: 3 })
  const meQ = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api.get<Me>('/api/auth/me')
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    staleTime: 60_000,
    retry: false,
  })
  const [branchId, setBranchIdState] = useState<number | null>(() => {
    try {
      const v = localStorage.getItem(BRANCH_KEY)
      return v ? Number(v) : null
    } catch {
      return null
    }
  })
  const me = meQ.data ?? null

  useEffect(() => {
    // Session ended server-side (sign-out elsewhere, suspension, expiry): reset all client state.
    setUnauthenticatedHandler(() => {
      // Never wipe the cache on the sign-in page: an in-flight identity check would be orphaned.
      if (window.location.pathname.startsWith('/login')) return
      qc.clear()
      window.location.assign('/login')
    })
  }, [qc])

  useEffect(() => {
    // Keep the remembered branch only if it is still in scope.
    if (me && branchId !== null && !me.branches.some((b) => b.id === branchId)) setBranchIdState(null)
    if (me && me.branches.length === 1 && branchId !== me.branches[0].id) setBranchIdState(me.branches[0].id)
  }, [me, branchId])

  const setBranchId = useCallback((id: number | null) => {
    setBranchIdState(id)
    try {
      if (id === null) localStorage.removeItem(BRANCH_KEY)
      else localStorage.setItem(BRANCH_KEY, String(id))
    } catch { /* storage unavailable: selection just isn't remembered */ }
  }, [])

  const value = useMemo<AuthState>(() => ({
    me,
    meta: meta.data ?? null,
    loading: meQ.isLoading,
    can: (perm) => !!me?.permissions.includes(perm),
    hasRole: (role) => !!me?.roles.includes(role),
    branchId,
    setBranchId,
    refresh: async () => { await qc.invalidateQueries({ queryKey: ['me'] }) },
    logout: async (reason?: string) => {
      try { await api.post('/api/auth/logout') } catch { /* already signed out server-side */ } finally {
        // Full reload into the sign-in page: no cached screen or query survives sign-out.
        qc.clear()
        window.location.assign(reason ? `/login?reason=${encodeURIComponent(reason)}` : '/login')
      }
    },
  }), [me, meta.data, meQ.isLoading, branchId, setBranchId, qc])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth(): AuthState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}

export const ROLE_LABELS: Record<string, string> = {
  owner: 'Head Manager / CFO', general_manager: 'General Manager', accountant: 'Accountant',
  operations_manager: 'Operations Manager', inventory_manager: 'Inventory Manager', branch_manager: 'Branch Manager',
  cashier: 'Cashier', sysadmin: 'System Administrator',
}

export function primaryRole(roles: string[]): string {
  const order = ['owner', 'general_manager', 'accountant', 'operations_manager', 'inventory_manager', 'branch_manager', 'cashier', 'sysadmin']
  return order.find((r) => roles.includes(r)) ?? 'cashier'
}
