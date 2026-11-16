import { AlertTriangle } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button } from './ui'

function isLoadFailure(error: unknown): boolean {
  const msg = error instanceof Error ? `${error.name} ${error.message}` : String(error)
  return /dynamically imported module|Importing a module script failed|ChunkLoadError|Loading chunk|error loading dynamically/i.test(msg)
}

/**
 * Keeps one page's failure from blanking the whole app: the navigation stays usable and the
 * user gets a retry. A failed page download is cached by React, so "Try again" reloads.
 * Mounted with key={pathname}, so navigating elsewhere clears the error.
 */
export class PageErrorBoundary extends Component<{ children: ReactNode }, { error: unknown }> {
  state: { error: unknown } = { error: null }

  static getDerivedStateFromError(error: unknown) {
    return { error }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error('Page failed to render', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    const loadFailure = isLoadFailure(this.state.error)
    return (
      <div role="alert" className="mx-auto mt-16 max-w-md rounded-[var(--radius-card)] border border-line-strong bg-surface p-6 text-center shadow-[var(--shadow-card)]">
        <AlertTriangle className="mx-auto size-8 text-warn" aria-hidden />
        <h2 className="mt-3 text-lg font-semibold">{loadFailure ? 'This page could not be loaded' : 'Something went wrong on this page'}</h2>
        <p className="mt-2 text-sm text-muted">
          {loadFailure
            ? 'The connection dropped or the system was just updated. Check your connection, then try again.'
            : 'Nothing was saved from the step that failed. Try again; if it keeps happening, tell your manager.'}
        </p>
        <div className="mt-5 flex justify-center gap-2">
          <Button variant="primary" onClick={() => {
            try { sessionStorage.removeItem('motion.chunk-reload') } catch { /* storage unavailable */ }
            window.location.reload()
          }}>Try again</Button>
          <Button onClick={() => window.location.assign('/')}>Go to start</Button>
        </div>
      </div>
    )
  }
}
