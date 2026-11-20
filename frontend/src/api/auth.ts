import type { AuthUser } from '../store'

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

/** Текущий учитель или null, если вход не выполнен. */
export async function me(): Promise<AuthUser | null> {
  try {
    return await call('me')
  } catch {
    return null
  }
}

export const login = (email: string, password: string) => call('login', { email, password })
export const register = (fio: { lastName: string; firstName: string; middleName: string }, email: string, password: string) =>
  call('register', { ...fio, email, password })

export async function logout() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => undefined)
}
