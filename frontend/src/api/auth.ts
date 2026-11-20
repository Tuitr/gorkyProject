import type { AuthUser } from '../store'
import { cabinetLogin, cabinetLogout, cabinetMe, cabinetRegister } from './cabinet'

/** Вход и регистрация учителя — только через кабинет (/v1). Сессия — в httpOnly cookie кабинета. */

export const me = (): Promise<AuthUser | null> => cabinetMe()

export const login = (email: string, password: string) => cabinetLogin(email, password)

/** Регистрация в кабинете и сразу вход: имя учителя — «Фамилия Имя Отчество». */
export async function register(
  fio: { lastName: string; firstName: string; middleName: string },
  email: string,
  password: string,
) {
  const displayName = [fio.lastName, fio.firstName, fio.middleName].map((p) => p.trim()).filter(Boolean).join(' ')
  await cabinetRegister(email, password, displayName)
  return { ...(await cabinetLogin(email, password)), ...fio, email }
}

export const logout = () => cabinetLogout()
