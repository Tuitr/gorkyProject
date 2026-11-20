import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { Job, JobKind, State } from './domain'

/**
 * Состояние MVP в браузере: JSON — в localStorage, фото и файлы (data:URL) — в IndexedDB.
 * Когда появится бэкенд, сюда же встанут запросы к API.
 */

const LS_KEY = 'gorky-mvp-v1'

export type AuthUser = { id: string; name: string; lastName: string; firstName: string; middleName: string; email: string }

/** «Иванова Анна Сергеевна» → «Иванова А. С.» */
export const shortName = (u: AuthUser) =>
  u.lastName ? [u.lastName, ...[u.firstName, u.middleName].filter(Boolean).map((p) => `${p[0]}.`)].join(' ') : u.name

/** Демонстрационная работа (прежний seed) — удаляется из сохранённого состояния при загрузке вместе с её сдачами,
 *  спорными местами и работой над ошибками. Демо-классы 8А/8Б/8В не трогаем: в них могут быть работы учителя. */
const DEMO_ASSIGNMENTS = new Set(['a-vectors'])
function withoutDemo(s: State): State {
  const subs = s.submissions.filter((x) => !DEMO_ASSIGNMENTS.has(x.assignmentId))
  const keep = new Set(subs.map((x) => x.id))
  return {
    ...s,
    assignments: s.assignments.filter((a) => !DEMO_ASSIGNMENTS.has(a.id)),
    submissions: subs,
    review: s.review.filter((r) => keep.has(r.submissionId)),
    practices: s.practices.filter((p) => !DEMO_ASSIGNMENTS.has(p.assignmentId)),
  }
}

/** Делает учителя владельцем состояния: его id и имя, его классы (бывшие классы прежнего владельца). */
function asTeacher(s: State, user: AuthUser): State {
  const prev = s.teacher.id
  return {
    ...s,
    teacher: { id: user.id, name: user.name },
    classes: s.classes.map((c) => (c.ownerId === prev ? { ...c, ownerId: user.id } : c)),
  }
}

const emptyState = (user?: AuthUser): State => ({
  version: 1,
  teacher: user ? { id: user.id, name: user.name } : { id: 'teacher', name: 'Учитель' },
  classes: [],
  assignments: [],
  submissions: [],
  review: [],
  practices: [],
})
const DB_NAME = 'gorky-mvp'
const DB_STORE = 'blobs'

/* ---------- IndexedDB для крупных строк ---------- */
let dbPromise: Promise<IDBDatabase> | null = null
function db() {
  dbPromise ??= new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(DB_STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
  return dbPromise
}
async function idbPut(key: string, value: string) {
  const d = await db()
  await new Promise<void>((res, rej) => {
    const tx = d.transaction(DB_STORE, 'readwrite')
    tx.objectStore(DB_STORE).put(value, key)
    tx.oncomplete = () => res()
    tx.onerror = () => rej(tx.error)
  })
}
async function idbGet(key: string): Promise<string | undefined> {
  const d = await db()
  return new Promise((res, rej) => {
    const req = d.transaction(DB_STORE).objectStore(DB_STORE).get(key)
    req.onsuccess = () => res(req.result as string | undefined)
    req.onerror = () => rej(req.error)
  })
}

async function idbKeys(): Promise<string[]> {
  const d = await db()
  return new Promise((res, rej) => {
    const req = d.transaction(DB_STORE).objectStore(DB_STORE).getAllKeys()
    req.onsuccess = () => res(req.result.map(String))
    req.onerror = () => rej(req.error)
  })
}
async function idbDelete(keys: string[]) {
  if (!keys.length) return
  const d = await db()
  await new Promise<void>((res, rej) => {
    const tx = d.transaction(DB_STORE, 'readwrite')
    for (const k of keys) tx.objectStore(DB_STORE).delete(k)
    tx.oncomplete = () => res()
    tx.onerror = () => rej(tx.error)
  })
}

/**
 * Уборка IndexedDB: удаляем фото и файлы, на которые не ссылается ни одно сохранённое состояние
 * (у каждого учителя браузера — своё). Запускается при загрузке, до первых изменений.
 */
async function collectGarbage() {
  const used = new Set<string>()
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (!k?.startsWith(LS_KEY)) continue
    for (const m of (localStorage.getItem(k) ?? '').matchAll(/"(idb:[^"]+)"/g)) used.add(m[1])
  }
  await idbDelete((await idbKeys()).filter((k) => !used.has(k)))
}

/** Быстрый хэш строки (FNV-1a) — по нему ловим повторно загруженные страницы. */
export function hashString(s: string) {
  let h = 0x811c9dc5
  const step = Math.max(1, Math.floor(s.length / 20000)) // для больших фото берём выборку символов
  for (let i = 0; i < s.length; i += step) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return `${(h >>> 0).toString(36)}-${s.length.toString(36)}`
}

const BIG = 4000 // строки длиннее (фото, PDF) — в IndexedDB

/** Вынимает большие data:URL в IndexedDB, в JSON оставляет ссылку idb:<hash>. */
function dehydrate(state: State, pending: Map<string, string>): string {
  return JSON.stringify(state, (_k, v: unknown) => {
    if (typeof v === 'string' && v.length > BIG && v.startsWith('data:')) {
      const key = `idb:${hashString(v)}`
      pending.set(key, v)
      return key
    }
    return v
  })
}

async function hydrate(raw: string): Promise<State> {
  const keys = new Set<string>()
  JSON.parse(raw, (_k, v: unknown) => {
    if (typeof v === 'string' && v.startsWith('idb:')) keys.add(v)
    return v
  })
  const blobs = new Map<string, string>()
  await Promise.all([...keys].map(async (k) => blobs.set(k, (await idbGet(k).catch(() => undefined)) ?? '')))
  return JSON.parse(raw, (_k, v: unknown) => (typeof v === 'string' && v.startsWith('idb:') ? blobs.get(v) ?? '' : v))
}

/* ---------- контекст ---------- */

type Toast = { id: number; text: string; kind: 'info' | 'err' }

type Store = {
  state: State
  update: (fn: (s: State) => State) => void
  /** Актуальное состояние для фоновых задач (замыкания не видят свежий state). */
  getState: () => State
  jobs: Record<string, Job>
  /** Запускает фоновую задачу; повторный вызов с тем же id, пока идёт первый, игнорируется. */
  runJob: (id: string, kind: JobKind, title: string, fn: () => Promise<void>) => Promise<void>
  toast: (text: string, kind?: Toast['kind']) => void
  /** вошедший учитель (без входа — в тестах/предпросмотре — undefined) */
  user?: AuthUser
  logout?: () => void
}

const Ctx = createContext<Store | null>(null)

/** initial — готовое состояние (для тестов/предпросмотра); без него читаем из браузера. */
export function StoreProvider({
  children,
  initial,
  user,
  logout,
}: {
  children: ReactNode
  initial?: State
  user?: AuthUser
  logout?: () => void
}) {
  // у каждого учителя свои данные в браузере
  const key = user ? `${LS_KEY}:${user.id}` : LS_KEY
  const [state, setState] = useState<State | null>(initial ?? null)
  const [jobs, setJobs] = useState<Record<string, Job>>({})
  const [toasts, setToasts] = useState<Toast[]>([])
  const running = useRef(new Set<string>())
  const lastSaved = useRef<string | null>(null)
  const saveErrorAt = useRef(0)
  const toastRef = useRef<((text: string, kind?: Toast['kind']) => void) | null>(null)
  const stateRef = useRef<State | null>(null)
  useLayoutEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => {
    if (initial) return
    const fresh = () => emptyState(user)
    let raw: string | null = null
    try {
      // классы и ученики приходят из кабинета; старые данные браузера без входа не подхватываем
      raw = localStorage.getItem(key)
    } catch {
      /* хранилище недоступно — работаем на пустых/демо-данных */
    }
    if (!raw) return setState(fresh())
    lastSaved.current = raw
    hydrate(raw)
      .then((s) => setState(s.version !== 1 ? fresh() : withoutDemo(user ? asTeacher(s, user) : s)))
      .catch(() => setState(fresh()))
      .finally(() => void collectGarbage().catch(() => undefined))
  }, [initial, key, user])

  // другая вкладка сохранила свои изменения — подхватываем их, а не затираем своим следующим сохранением
  useEffect(() => {
    if (initial) return
    const onStorage = (e: StorageEvent) => {
      if (e.key !== key || !e.newValue || e.newValue === lastSaved.current) return
      lastSaved.current = e.newValue
      void hydrate(e.newValue).then((s) => s.version === 1 && setState(s))
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [initial, key])

  // сохранение с небольшой задержкой
  useEffect(() => {
    if (!state) return
    const t = setTimeout(() => {
      const pending = new Map<string, string>()
      const json = dehydrate(state, pending)
      if (json === lastSaved.current) return // ничего не изменилось (или пришло из другой вкладки)
      Promise.all([...pending].map(([k, v]) => idbPut(k, v)))
        .then(() => {
          localStorage.setItem(key, json)
          lastSaved.current = json
        })
        .catch((e) => {
          console.warn('Не удалось сохранить состояние', e)
          // учителю — понятное сообщение (не чаще раза в минуту)
          if (Date.now() - saveErrorAt.current > 60_000) {
            saveErrorAt.current = Date.now()
            const full = e instanceof DOMException && /quota/i.test(e.name + e.message)
            toastRef.current?.(
              full
                ? 'Не удалось сохранить: в браузере закончилось место. Удалите ненужные работы.'
                : 'Не удалось сохранить изменения в браузере.',
              'err',
            )
          }
        })
    }, 400)
    return () => clearTimeout(t)
  }, [state, key])

  const toast = useCallback((text: string, kind: Toast['kind'] = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, text, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'err' ? 7000 : 3500)
  }, [])
  useEffect(() => {
    toastRef.current = toast
  }, [toast])

  const runJob = useCallback<Store['runJob']>(async (id, kind, title, fn) => {
    if (running.current.has(id)) return
    running.current.add(id)
    setJobs((j) => ({ ...j, [id]: { id, kind, title, status: 'running', startedAt: Date.now() } }))
    try {
      await fn()
      setJobs((j) => ({ ...j, [id]: { ...j[id], status: 'done' } }))
    } catch (e) {
      const error = e instanceof Error ? e.message : 'Неизвестная ошибка'
      setJobs((j) => ({ ...j, [id]: { ...j[id], status: 'error', error } }))
    } finally {
      running.current.delete(id)
    }
  }, [])

  const update = useCallback((fn: (s: State) => State) => setState((s) => (s ? fn(s) : s)), [])
  const getState = useCallback(() => stateRef.current as State, [])

  const value = useMemo(
    () => (state ? { state, update, getState, jobs, runJob, toast, user, logout } : null),
    [state, update, getState, jobs, runJob, toast, user, logout],
  )

  if (!value) {
    return (
      <div className="boot">
        <div className="spinner" aria-hidden="true" />
      </div>
    )
  }
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.kind === 'err' ? ' toast--err' : ''}`}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  )
}

export function useStore() {
  const s = useContext(Ctx)
  if (!s) throw new Error('useStore вне StoreProvider')
  return s
}

/* ---------- маленькие помощники для неизменяемых обновлений ---------- */
export const patch = <T extends { id: string }>(list: T[], id: string, fn: (x: T) => T): T[] =>
  list.map((x) => (x.id === id ? fn(x) : x))
