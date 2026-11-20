import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { login, logout, me, register } from '../api/auth'
import { isCabinet, SESSION_EXPIRED_EVENT } from '../api/cabinet'
import type { AuthUser } from '../store'

/** Пускает в приложение только после входа; children получает учителя и выход. */
export function AuthGate({ children }: { children: (user: AuthUser, onLogout: () => void) => ReactNode }) {
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined)

  useEffect(() => {
    let alive = true
    void me().then((u) => alive && setUser(u))
    // кабинет: сессия (8 часов) закончилась посреди работы — экран входа
    const expired = () => setUser(null)
    window.addEventListener(SESSION_EXPIRED_EVENT, expired)
    return () => {
      alive = false
      window.removeEventListener(SESSION_EXPIRED_EVENT, expired)
    }
  }, [])

  if (user === undefined) {
    return (
      <div className="boot">
        <div className="spinner" aria-hidden="true" />
      </div>
    )
  }
  if (!user) return <AuthScreen onDone={setUser} />
  return (
    <>
      {children(user, () => {
        void logout().then(() => {
          setUser(null)
          location.hash = '#/'
        })
      })}
    </>
  )
}

function AuthScreen({ onDone }: { onDone: (u: AuthUser) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [lastName, setLastName] = useState('')
  const [firstName, setFirstName] = useState('')
  const [middleName, setMiddleName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // В режиме кабинета этот экран показывает форму входа.
  const cabinet = isCabinet()
  const isReg = mode === 'register' && !cabinet

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    if (isReg && password !== password2) return setError('Пароли не совпадают.')
    setBusy(true)
    try {
      onDone(isReg ? await register({ lastName, firstName, middleName }, email, password) : await login(email, password))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось выполнить вход.')
    } finally {
      setBusy(false)
    }
  }

  const switchTo = (m: 'login' | 'register') => {
    setMode(m)
    setError('')
  }

  return (
    <div className="auth">
      <div className="auth__brand">
        <span className="auth__logo" aria-hidden="true">
          &gt;_
        </span>
        <b>Spirit Code</b>
      </div>

      <form className="auth__card" onSubmit={submit} noValidate>
        {cabinet ? (
          <h2 className="auth__title">Вход</h2>
        ) : (
        <div className="segmented auth__tabs" role="tablist" aria-label="Вход или регистрация">
          <button type="button" role="tab" aria-selected={!isReg} onClick={() => switchTo('login')}>
            Вход
          </button>
          <button type="button" role="tab" aria-selected={isReg} onClick={() => switchTo('register')}>
            Регистрация
          </button>
        </div>
        )}

        {isReg && (
          <>
            <label className="field">
              <span>Фамилия</span>
              <input
                className="input"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                autoComplete="family-name"
                required
              />
            </label>
            <label className="field">
              <span>Имя</span>
              <input
                className="input"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                autoComplete="given-name"
                required
              />
            </label>
            <label className="field">
              <span>Отчество</span>
              <input
                className="input"
                value={middleName}
                onChange={(e) => setMiddleName(e.target.value)}
                autoComplete="additional-name"
                placeholder="при наличии"
              />
            </label>
          </>
        )}
        <label className="field">
          <span>Электронная почта</span>
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete={cabinet ? 'username' : 'email'}
            required
          />
        </label>
        <label className="field">
          <span>Пароль</span>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={isReg ? 'new-password' : 'current-password'}
            minLength={isReg ? 8 : undefined}
            required
          />
          {isReg && <span className="muted small">Не менее 8 символов</span>}
        </label>
        {isReg && (
          <label className="field">
            <span>Повторите пароль</span>
            <input
              className="input"
              type="password"
              value={password2}
              onChange={(e) => setPassword2(e.target.value)}
              autoComplete="new-password"
              required
            />
          </label>
        )}

        {error && (
          <p className="auth__error" role="alert">
            {error}
          </p>
        )}

        <button className="btn btn--primary auth__submit" disabled={busy}>
          {busy ? 'Подождите…' : isReg ? 'Зарегистрироваться' : 'Войти'}
        </button>
      </form>
    </div>
  )
}
