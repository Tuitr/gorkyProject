import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { AuthGate } from './Auth'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthGate>{(account) => <App account={account} />}</AuthGate>
  </StrictMode>,
)
