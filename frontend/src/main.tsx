import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary.tsx'
import { AuthGate } from './screens/Auth.tsx'
import { StoreProvider } from './store.tsx'

// Не загрузился модуль (обычно после обновления сайта или перезапуска dev-сервера) — один раз перезагружаем страницу.
window.addEventListener('vite:preloadError', (e) => {
  if (sessionStorage.getItem('reloaded-after-preload-error')) return
  sessionStorage.setItem('reloaded-after-preload-error', '1')
  e.preventDefault()
  location.reload()
})
setTimeout(() => sessionStorage.removeItem('reloaded-after-preload-error'), 10_000)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary full>
    <AuthGate>
      {(user, logout) => (
        // key — при смене учителя хранилище создаётся заново со своими данными
        <StoreProvider key={user.id} user={user} logout={logout}>
          <App />
        </StoreProvider>
      )}
    </AuthGate>
    </ErrorBoundary>
  </StrictMode>,
)
