import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ApiError } from './api/client'
import { App } from './app/App'
import { AuthProvider } from './auth/auth'
import { ToastProvider } from './components/ui'
import './index.css'

try {
  document.documentElement.dataset.theme = localStorage.getItem('motion.theme') ?? 'light'
} catch {
  document.documentElement.dataset.theme = 'light'
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 20_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)
