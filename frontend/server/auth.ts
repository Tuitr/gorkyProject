import { createHmac, randomBytes, randomUUID, scrypt as scryptCb, timingSafeEqual } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { join } from 'node:path'
import { promisify } from 'node:util'
import type { Plugin } from 'vite'

/**
 * Базовая регистрация учителей (плагин Vite — работает в dev и preview, как server/gigachat.ts).
 *
 *   POST /api/auth/register { lastName, firstName, middleName?, email, password } → { user } + cookie сессии
 *   POST /api/auth/login    { email, password }       → { user } + cookie сессии
 *   POST /api/auth/logout                             → cookie стирается
 *   GET  /api/auth/me                                 → { user } или 401
 *
 * Учётные записи — в .data/users.json (не коммитится). Пароль хранится только как scrypt-хэш с солью.
 * Сессия — подписанная HMAC cookie (httpOnly, SameSite=Lax, 30 дней); ключ подписи — CHECKER_AUTH_SECRET
 * или генерируется один раз в .data/auth-secret.
 * Остальные /api/* (OCR, GigaChat) без входа отвечают 401 — чтобы ключи API не тратил кто угодно.
 */

const scrypt = promisify(scryptCb) as (pwd: string, salt: Buffer, len: number) => Promise<Buffer>

type UserRecord = {
  id: string
  lastName: string
  firstName: string
  middleName: string
  /** полное ФИО одной строкой */
  name: string
  email: string
  salt: string
  hash: string
  createdAt: string
}
export type PublicUser = { id: string; name: string; lastName: string; firstName: string; middleName: string; email: string }

const COOKIE = 'gorky_session'
const SESSION_DAYS = 30
const DATA_DIR = join(process.cwd(), '.data')
const USERS_FILE = join(DATA_DIR, 'users.json')
const SECRET_FILE = join(DATA_DIR, 'auth-secret')
const REVOKED_FILE = join(DATA_DIR, 'revoked.json')

/** Отозванные при выходе сессии: номер сессии → когда она и так истекла бы (потом запись не нужна). */
function loadRevoked(): Record<string, number> {
  try {
    return JSON.parse(readFileSync(REVOKED_FILE, 'utf8')) as Record<string, number>
  } catch {
    return {}
  }
}
function revoke(sid: string, exp: number) {
  const now = Date.now()
  const list = Object.fromEntries(Object.entries(loadRevoked()).filter(([, e]) => e > now))
  list[sid] = exp
  ensureDir()
  writeFileSync(REVOKED_FILE, JSON.stringify(list), 'utf8')
}

function ensureDir() {
  if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true })
}

function loadUsers(): UserRecord[] {
  try {
    return (JSON.parse(readFileSync(USERS_FILE, 'utf8')) as { users?: UserRecord[] }).users ?? []
  } catch {
    return []
  }
}

function saveUsers(users: UserRecord[]) {
  ensureDir()
  // запись через временный файл — чтобы при сбое не остался обрезанный JSON
  const tmp = `${USERS_FILE}.tmp`
  writeFileSync(tmp, JSON.stringify({ users }, null, 2), 'utf8')
  renameSync(tmp, USERS_FILE)
}

function loadSecret(env: Record<string, string>): string {
  if (env.CHECKER_AUTH_SECRET) return env.CHECKER_AUTH_SECRET
  ensureDir()
  if (existsSync(SECRET_FILE)) return readFileSync(SECRET_FILE, 'utf8').trim()
  const s = randomBytes(32).toString('hex')
  writeFileSync(SECRET_FILE, s, 'utf8')
  return s
}

const publicUser = (u: UserRecord): PublicUser => ({
  id: u.id,
  name: u.name,
  lastName: u.lastName ?? '',
  firstName: u.firstName ?? '',
  middleName: u.middleName ?? '',
  email: u.email,
})
/** часть ФИО: пробелы схлопнуты; буквы (в т. ч. ё), дефис, апостроф, пробел */
const cleanPart = (v: unknown) => String(v ?? '').trim().replace(/\s+/g, ' ')
const NAME_RE = /^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё' -]*$/
const normEmail = (e: string) => e.trim().toLowerCase()
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

async function hashPassword(password: string, salt: Buffer) {
  return (await scrypt(password, salt, 64)).toString('hex')
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let s = ''
    req.setEncoding('utf8')
    req.on('data', (c: string) => {
      s += c
      if (s.length > 10_000) req.destroy() // форма входа маленькая
    })
    req.on('end', () => resolve(s))
    req.on('error', reject)
  })
}

function getCookie(req: IncomingMessage, name: string) {
  for (const part of (req.headers.cookie ?? '').split(';')) {
    const [k, ...v] = part.trim().split('=')
    if (k === name) return decodeURIComponent(v.join('='))
  }
  return undefined
}

/** Простой ограничитель попыток входа: не больше 10 неудачных за 10 минут с одного адреса. */
const failures = new Map<string, number[]>()
const WINDOW_MS = 10 * 60 * 1000
function tooMany(ip: string) {
  const now = Date.now()
  const list = (failures.get(ip) ?? []).filter((t) => now - t < WINDOW_MS)
  failures.set(ip, list)
  return list.length >= 10
}
function addFailure(ip: string) {
  failures.set(ip, [...(failures.get(ip) ?? []), Date.now()])
}

/**
 * Режим кабинета (CHECKER_CABINET_URL): вход и сессия — у бэкенда (POST /v1/sessions, cookie __Host-cabinet),
 * регистрации нет. Наши /api/* пускаем, если кабинет подтверждает сессию по cookie запроса.
 */
const cabinetSessions = new Map<string, number>() // cookie → до какого времени считаем сессию действующей
async function cabinetSessionOk(cabinetUrl: string, cookie: string | undefined) {
  if (!cookie) return false
  const until = cabinetSessions.get(cookie)
  if (until && until > Date.now()) return true
  try {
    const res = await fetch(`${cabinetUrl}/v1/sessions/current`, { headers: { cookie } })
    if (!res.ok) return false
    if (cabinetSessions.size > 1000) cabinetSessions.clear()
    cabinetSessions.set(cookie, Date.now() + 60_000)
    return true
  } catch {
    return false
  }
}

export function authPlugin(env: Record<string, string>): Plugin {
  const cabinetUrl = (env.CHECKER_CABINET_URL ?? '').replace(/\/$/, '')
  let secret = ''
  const sign = (payload: string) => createHmac('sha256', secret).update(payload).digest('base64url')

  // у каждой сессии свой номер (s): при выходе он попадает в список отозванных
  const makeToken = (userId: string) => {
    const payload = Buffer.from(
      JSON.stringify({ u: userId, s: randomUUID(), e: Date.now() + SESSION_DAYS * 864e5 }),
    ).toString('base64url')
    return `${payload}.${sign(payload)}`
  }
  const parseToken = (token: string | undefined): { u: string; s?: string; e: number } | null => {
    if (!token) return null
    const [payload, sig] = token.split('.')
    if (!payload || !sig) return null
    const good = Buffer.from(sign(payload))
    const got = Buffer.from(sig)
    if (good.length !== got.length || !timingSafeEqual(good, got)) return null
    try {
      const t = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8')) as { u: string; s?: string; e: number }
      if (t.e <= Date.now()) return null
      if (t.s && loadRevoked()[t.s]) return null
      return t
    } catch {
      return null
    }
  }
  const currentUser = (req: IncomingMessage) => {
    const id = parseToken(getCookie(req, COOKIE))?.u
    return id ? loadUsers().find((u) => u.id === id) ?? null : null
  }

  const setSession = (res: ServerResponse, userId: string | null) => {
    const value = userId ? makeToken(userId) : ''
    const maxAge = userId ? SESSION_DAYS * 86400 : 0
    res.setHeader('Set-Cookie', `${COOKIE}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}`)
  }

  const handler = async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const url = (req.url ?? '').split('?')[0]
    // выход из кабинета идёт через наш прокси — сразу забываем подтверждённую сессию
    if (url === '/v1/sessions/current' && req.method === 'DELETE' && req.headers.cookie) cabinetSessions.delete(req.headers.cookie)
    if (!url.startsWith('/api/')) return next()
    const send = (status: number, data: unknown) => {
      res.statusCode = status
      res.setHeader('Content-Type', 'application/json; charset=utf-8')
      res.end(JSON.stringify(data))
    }

    // режим работы для браузера: свой вход или кабинет из main
    if (url === '/api/config' && req.method === 'GET') return send(200, { cabinet: !!cabinetUrl })

    if (cabinetUrl) {
      if (url.startsWith('/api/auth/')) return send(404, { message: 'Вход выполняется через кабинет' })
      return (await cabinetSessionOk(cabinetUrl, req.headers.cookie)) ? next() : send(401, { message: 'Требуется вход в систему' })
    }

    if (!url.startsWith('/api/auth/')) {
      // остальные API — только после входа
      return currentUser(req) ? next() : send(401, { message: 'Требуется вход в систему' })
    }

    try {
      if (url === '/api/auth/me' && req.method === 'GET') {
        const u = currentUser(req)
        return u ? send(200, { user: publicUser(u) }) : send(401, { message: 'Вход не выполнен' })
      }
      if (url === '/api/auth/logout' && req.method === 'POST') {
        // сессию отзываем на сервере — скопированная cookie после выхода тоже перестаёт работать
        const t = parseToken(getCookie(req, COOKIE))
        if (t?.s) revoke(t.s, t.e)
        setSession(res, null)
        return send(200, { ok: true })
      }
      if (req.method !== 'POST' || (url !== '/api/auth/register' && url !== '/api/auth/login')) return next()

      const ip = req.socket.remoteAddress ?? '?'
      if (tooMany(ip)) return send(429, { message: 'Слишком много попыток. Повторите через 10 минут.' })
      const body = JSON.parse((await readBody(req)) || '{}') as Record<string, unknown>
      const email = normEmail(String(body.email ?? ''))
      const password = String(body.password ?? '')
      const users = loadUsers()

      if (url === '/api/auth/register') {
        const lastName = cleanPart(body.lastName)
        const firstName = cleanPart(body.firstName)
        const middleName = cleanPart(body.middleName)
        const bad = (v: string) => !NAME_RE.test(v) || v.length > 60
        if (!lastName || bad(lastName)) return send(400, { message: 'Укажите фамилию (только буквы, дефис или пробел).' })
        if (!firstName || bad(firstName)) return send(400, { message: 'Укажите имя (только буквы, дефис или пробел).' })
        if (middleName && bad(middleName)) return send(400, { message: 'Отчество может содержать только буквы, дефис или пробел.' })
        const name = [lastName, firstName, middleName].filter(Boolean).join(' ')
        if (!EMAIL_RE.test(email) || email.length > 120) return send(400, { message: 'Укажите корректный адрес электронной почты.' })
        if (password.length < 8 || password.length > 200) return send(400, { message: 'Пароль должен содержать не менее 8 символов.' })
        if (users.some((u) => u.email === email)) {
          addFailure(ip)
          return send(409, { message: 'Учётная запись с этой почтой уже существует.' })
        }
        const salt = randomBytes(16)
        const user: UserRecord = {
          id: `t-${randomUUID()}`,
          lastName,
          firstName,
          middleName,
          name,
          email,
          salt: salt.toString('hex'),
          hash: await hashPassword(password, salt),
          createdAt: new Date().toISOString(),
        }
        saveUsers([...users, user])
        setSession(res, user.id)
        return send(201, { user: publicUser(user) })
      }

      // вход
      const user = users.find((u) => u.email === email)
      const hash = await hashPassword(password, user ? Buffer.from(user.salt, 'hex') : randomBytes(16))
      const ok = !!user && timingSafeEqual(Buffer.from(hash, 'hex'), Buffer.from(user.hash, 'hex'))
      if (!ok) {
        addFailure(ip)
        return send(401, { message: 'Неверная почта или пароль.' })
      }
      setSession(res, user.id)
      return send(200, { user: publicUser(user) })
    } catch (e) {
      console.warn('[auth]', e)
      send(500, { message: 'Ошибка сервера' })
    }
  }

  return {
    name: 'teacher-auth',
    // middleware подключается до прокси Vite (/api/ocr) и других плагинов — проверка входа идёт первой
    configureServer: (server) => {
      secret = loadSecret(env)
      server.middlewares.use(handler)
    },
    configurePreviewServer: (server) => {
      secret = loadSecret(env)
      server.middlewares.use(handler)
    },
  }
}
