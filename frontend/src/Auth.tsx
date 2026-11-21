import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { api, ApiError, SESSION_EXPIRED } from './api'
import { Brand, ErrorText, errorText } from './components'

// Имя учителя приходит только при входе (у кабинета нет GET «кто я»), поэтому запоминаем его в браузере.
const NAME_KEY = 'teacher-name'
const readName = () => { try { return localStorage.getItem(NAME_KEY) ?? '' } catch { return '' } }
const saveName = (name: string) => { try { if (name) localStorage.setItem(NAME_KEY, name); else localStorage.removeItem(NAME_KEY) } catch { /* без хранилища просто не помним имя */ } }

export type Account = { name: string; logout: () => void }

export function AuthGate({ children }: { children: (account: Account) => ReactNode }) {
  const [state, setState] = useState<'checking' | 'in' | 'out'>('checking')
  const [name, setName] = useState(readName)

  useEffect(() => {
    // Есть ли живая сессия: любой запрос кабинета ответит 401, если нет.
    api.classes().then(() => setState('in')).catch((cause) => {
      setState(cause instanceof ApiError && cause.status === 401 ? 'out' : 'in')
    })
    const expired = () => setState('out')
    window.addEventListener(SESSION_EXPIRED, expired)
    return () => window.removeEventListener(SESSION_EXPIRED, expired)
  }, [])

  function signedIn(displayName: string) {
    saveName(displayName)
    setName(displayName)
    setState('in')
  }

  async function logout() {
    await api.logout().catch(() => undefined)
    saveName('')
    setName('')
    setState('out')
  }

  if (state === 'checking') return <main className="auth-page"><p className="subtle" role="status">Загружаем…</p></main>
  if (state === 'out') return <LoginScreen onSignedIn={signedIn} />
  return <>{children({ name, logout })}</>
}

function LoginScreen({ onSignedIn }: { onSignedIn: (displayName: string) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const registering = mode === 'register'

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      // Регистрация сессию не открывает — сразу входим теми же данными.
      if (registering) await api.register(email.trim(), displayName.trim(), password)
      const teacher = await api.login(email.trim(), password)
      onSignedIn(teacher.displayName)
    } catch (cause) {
      setError(errorText(cause))
      setBusy(false)
    }
  }

  function switchTo(next: 'login' | 'register') {
    setMode(next)
    setError('')
  }

  return <main className="auth-page">
    <div className="auth-card">
      <Brand />
      <h1>{registering ? 'Регистрация' : 'Вход'}</h1>
      <div className="auth-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={!registering} onClick={() => switchTo('login')}>Вход</button>
        <button type="button" role="tab" aria-selected={registering} onClick={() => switchTo('register')}>Регистрация</button>
      </div>
      <form className="form auth-form" onSubmit={submit}>
        <label>Почта<input type="email" required autoFocus autoComplete="email" maxLength={254}
          value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        {registering && <label>Имя<input required autoComplete="name" maxLength={200} placeholder="Как вас показывать в кабинете"
          value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>}
        <label>Пароль{registering && <small>не короче 8 символов</small>}
          <input type="password" required minLength={registering ? 8 : 1} maxLength={1024}
            autoComplete={registering ? 'new-password' : 'current-password'}
            value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <ErrorText message={error} />
        <button className="button primary" disabled={busy}>
          {busy ? (registering ? 'Регистрируем…' : 'Входим…') : (registering ? 'Зарегистрироваться' : 'Войти')}
        </button>
      </form>
    </div>
  </main>
}

// Круглый значок учителя в шапке; по нажатию — имя и «Выйти».
export function AccountMenu({ account }: { account: Account }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const name = account.name || 'Учитель'

  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false) }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) }
  }, [open])

  return <div className="account" ref={box}>
    <button type="button" className="account-avatar" aria-haspopup="true" aria-expanded={open}
      aria-label={'Учётная запись: ' + name} title={name} onClick={() => setOpen(!open)}>
      {(account.name.trim()[0] ?? 'У').toUpperCase()}
    </button>
    {open && <div className="account-menu">
      <strong>{name}</strong>
      <button type="button" className="button" onClick={() => { setOpen(false); account.logout() }}>Выйти</button>
    </div>}
  </div>
}
