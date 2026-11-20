import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { AuthGate } from './screens/Auth.tsx'
import { StoreProvider } from './store.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthGate>
      {(user, logout) => (
        // key — при смене учителя хранилище создаётся заново со своими данными
        <StoreProvider key={user.id} user={user} logout={logout}>
          <App />
        </StoreProvider>
      )}
    </AuthGate>
  </StrictMode>,
)
