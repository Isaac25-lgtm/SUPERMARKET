import { useEffect, useRef } from 'react'
import { api } from '../api/client'

const ACTIVITY_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart', 'scroll'] as const

/**
 * Signs the user out after `minutes` without keyboard/mouse/touch activity.
 *
 * The server enforces the same idle limit on its side; while the user is active
 * this hook refreshes the session at most once a minute so the server-side
 * timer only expires when the person has really walked away.
 */
export function useIdleLogout(minutes: number | undefined, onIdle: () => void) {
  const lastActivity = useRef(Date.now())
  const lastPing = useRef(Date.now())
  const fired = useRef(false)

  useEffect(() => {
    if (!minutes || minutes <= 0) return
    const limitMs = minutes * 60_000
    const onActivity = () => {
      const now = Date.now()
      lastActivity.current = now
      if (now - lastPing.current > 60_000) {
        lastPing.current = now
        api.get('/api/auth/me').catch(() => { /* a 401 is handled by the global session handler */ })
      }
    }
    const check = () => {
      if (!fired.current && Date.now() - lastActivity.current >= limitMs) {
        fired.current = true
        onIdle()
      }
    }
    ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, onActivity, { passive: true }))
    document.addEventListener('visibilitychange', check)
    const timer = window.setInterval(check, 15_000)
    return () => {
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, onActivity))
      document.removeEventListener('visibilitychange', check)
      window.clearInterval(timer)
    }
  }, [minutes, onIdle])
}
