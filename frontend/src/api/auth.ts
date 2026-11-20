import type { AuthUser } from '../store'
import { cabinetLogin, cabinetLogout, cabinetMe, isCabinet, loadMode } from './cabinet'

/** Запросы к server/auth.ts. Сессия — в httpOnly cookie, браузерный код её не видит. */

async function call(path: string, body?: unknown): Promise<AuthUser> {
  const res = await fetch(`/api/auth/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  })
  const data = (await res.json().catch(() => null)) as { user?: AuthUser; message?: string } | null
  if (!res.ok || !data?.user) throw new Error(data?.message ?? `Ошибка ${res.status}`)
  return data.user
}

/** Текущий учитель или null, если вход не выполнен. В режиме кабинета — сессия бэкенда из main. */
export async function me(): Promise<AuthUser | null> {
  if (await loadMode()) return cabinetMe()
  try {
    return await call('me')
  } catch {
    return null
  }
}

export const login = (email: string, password: string) =>
  isCabinet() ? cabinetLogin(email, password) : call('login', { email, password })
export const register = (fio: { lastName: string; firstName: string; middleName: string }, email: string, password: string) =>
  call('register', { ...fio, email, password })

export async function logout() {
  if (isCabinet()) return void (await cabinetLogout())
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => undefined)
}
