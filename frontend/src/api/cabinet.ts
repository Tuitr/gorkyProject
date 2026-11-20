import type { AuthUser } from '../store'

/**
 * Бэкенд «кабинет» из ветки main (services/cabinet, публичный API /v1).
 * Включается переменной CHECKER_CABINET_URL на сервере Vite: он пробрасывает /v1 в кабинет (vite.config.ts).
 * Без неё фронтенд работает сам — вход через server/auth.ts, данные в браузере.
 *
 * Что кабинет умеет сейчас: вход по логину, классы и ученики (чтение и создание), сохранение работы (DOCX
 * + требования учителя, ревизии), загрузка работ учеников (DOCX или ZIP) с подбором ученика, чтение
 * статуса проверки. Проверки (сервиса grading) в main пока нет — результаты считает наш сервер (GigaChat).
 */

let mode: boolean | null = null

/** Включён ли режим кабинета (узнаётся у нашего сервера один раз при запуске). */
export async function loadMode(): Promise<boolean> {
  if (mode !== null) return mode
  try {
    const res = await fetch('/api/config')
    mode = res.ok ? !!((await res.json()) as { cabinet?: boolean }).cabinet : false
  } catch {
    mode = false
  }
  return mode
}
/** После loadMode(): работаем ли через кабинет. */
export const isCabinet = () => mode === true

/** Ошибка кабинета: { code, message, retryable, traceId } — сообщение переводим для учителя. */
export class CabinetError extends Error {
  status: number
  code: string
  /** номер запроса в логах кабинета — для разработчиков бэкенда */
  traceId?: string
  /** для 409 при правках: актуальная версия результата */
  resultRev?: number
  constructor(status: number, code: string, message: string, traceId?: string, resultRev?: number) {
    super(message)
    this.status = status
    this.code = code
    this.traceId = traceId
    this.resultRev = resultRev
  }
}

/** Сессия кабинета закончилась посреди работы — экран входа (слушает AuthGate). */
export const SESSION_EXPIRED_EVENT = 'cabinet:session-expired'

const MESSAGES: Record<string, string> = {
  unauthorized: 'Неверный логин или пароль.',
  rate_limited: 'Слишком много попыток. Повторите позже.',
  forbidden: 'Запрос отклонён сервером.',
  not_found: 'Не найдено на сервере.',
  conflict: 'Данные уже изменены. Обновите страницу.',
  invalid_input: 'Сервер не принял данные.',
  payload_too_large: 'Файл слишком большой.',
  unavailable: 'Сервер временно недоступен.',
  timeout: 'Сервер не ответил вовремя.',
}

async function call<T>(method: string, path: string, body?: BodyInit | object): Promise<T> {
  const res = await send(method, path, body)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Запрос к кабинету; при ошибке — CabinetError с понятным текстом и номером запроса. */
async function send(method: string, path: string, body?: BodyInit | object): Promise<Response> {
  const isForm = body instanceof FormData
  const res = await fetch(`/v1${path}`, {
    method,
    credentials: 'same-origin',
    headers: body && !isForm ? { 'Content-Type': 'application/json' } : undefined,
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  }).catch(() => {
    throw new CabinetError(0, 'unavailable', 'Сервер кабинета недоступен.')
  })
  if (res.ok) return res
  const data = (await res.json().catch(() => null)) as
    | { code?: string; message?: string; traceId?: string; resultRev?: number }
    | null
  const code = data?.code ?? (res.status >= 500 ? 'unavailable' : 'invalid_input')
  let msg = MESSAGES[code] ?? data?.message ?? `Ошибка сервера ${res.status}`
  if (code === 'unauthorized' && path !== '/sessions') {
    // сессия закончилась (живёт 8 часов) — на экран входа
    msg = 'Сессия закончилась — войдите снова.'
    if (path !== '/sessions/current') window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT))
  }
  const retry = Number(res.headers.get('Retry-After'))
  if (code === 'rate_limited' && retry > 0)
    msg = `Слишком много попыток. Повторите через ${retry >= 120 ? `${Math.ceil(retry / 60)} мин` : `${retry} с`}.`
  // номер запроса — к серверным сбоям, чтобы разработчик нашёл запрос в логах кабинета
  if (data?.traceId && res.status >= 500) msg += ` Код запроса: ${data.traceId.slice(0, 12)}.`
  throw new CabinetError(res.status, code, msg, data?.traceId, data?.resultRev)
}

/* ---------- вход ---------- */

type TeacherSession = { teacherId: string; displayName: string }
const toUser = (t: TeacherSession): AuthUser => ({
  id: t.teacherId,
  name: t.displayName,
  lastName: '',
  firstName: '',
  middleName: '',
  email: '',
})

export const cabinetLogin = async (login: string, password: string) =>
  toUser(await call<TeacherSession>('POST', '/sessions', { login, password }))
export async function cabinetMe(): Promise<AuthUser | null> {
  try {
    return toUser(await call<TeacherSession>('GET', '/sessions/current'))
  } catch {
    return null
  }
}
export const cabinetLogout = () => call<void>('DELETE', '/sessions/current').catch(() => undefined)

/* ---------- классы и ученики ---------- */

export type RemoteClass = { classId: string; name: string }
export type RemoteStudent = { studentId: string; name: string }

export const listClasses = () => call<RemoteClass[]>('GET', '/classes')
export const createClass = (name: string) => call<RemoteClass>('POST', '/classes', { name })
export const listStudents = (classId: string) => call<RemoteStudent[]>('GET', `/classes/${classId}/students`)
export const addStudent = (classId: string, name: string) =>
  call<RemoteStudent>('POST', `/classes/${classId}/students`, { name })

/* ---------- работа (вариант) ---------- */

export type Requirement = { text: string; taskNumber: string | null }

/** Сохраняет работу классу: DOCX + требования (+ дата ГГГГ-ММ-ДД). С assignmentId — новая ревизия той же работы. */
export function uploadAssignment(
  classId: string,
  docx: Blob,
  requirements: Requirement[],
  assignmentId?: string,
  assignmentDate?: string,
  fileName = 'assignment.docx',
) {
  const form = new FormData()
  form.append('file', docx, fileName)
  form.append('requirements', JSON.stringify(requirements))
  if (assignmentId) form.append('assignmentId', assignmentId)
  if (assignmentDate) form.append('assignmentDate', assignmentDate)
  return call<{ assignmentId: string; assignmentRev: number }>('POST', `/classes/${classId}/assignments`, form)
}

/* ---------- работы учеников ---------- */

export type UploadFileStatus = 'pending_binding' | 'needs_confirmation' | 'matched' | 'bound' | 'rejected'
export type UploadFile = {
  fileId: string
  originalName: string
  status: UploadFileStatus
  candidates?: RemoteStudent[]
  reason?: string
  studentId?: string
  attemptId?: string
  checkId?: string
}

export function uploadWork(assignmentId: string, assignmentRev: number, file: File) {
  const form = new FormData()
  form.append('file', file, file.name)
  form.append('assignmentRev', String(assignmentRev))
  return call<{ uploadId: string }>('POST', `/assignments/${assignmentId}/uploads`, form)
}
export const readUpload = (uploadId: string) =>
  call<{ uploadId: string; files: UploadFile[] }>('GET', `/uploads/${uploadId}`)
type Bound = { fileId: string; studentId: string; attemptId: string; checkId: string; status: 'bound' }
export const bindFile = (uploadId: string, fileId: string, studentId: string) =>
  call<Bound>('POST', `/uploads/${uploadId}/bindings`, { fileId, studentId })
/** Привязать работу к новому ученику — кабинет сам добавит его в класс. */
export const bindNewStudent = (uploadId: string, fileId: string, name: string) =>
  call<Bound>('POST', `/uploads/${uploadId}/bindings`, { fileId, newStudent: { name } })

/* ---------- проверка ---------- */

export type RemoteIssue = { issueId: string; stepId: string; category: string; comment: string; correction: string }
export type RemoteTaskResult = {
  taskId: string
  answer: 'correct' | 'incorrect' | 'skipped' | 'unreadable'
  reasoning: 'valid' | 'invalid' | 'not_shown' | 'unknown'
  method: 'met' | 'unmet' | 'unconfirmed' | 'optional'
  issues: RemoteIssue[]
  needsReview: boolean
}
export type RemoteCheck =
  | { checkId: string; attemptId: string; state: 'queued' | 'waiting_reference' | 'running' | 'needs_review' | 'failed'; reason: string }
  | {
      checkId: string
      attemptId: string
      state: 'ready'
      resultRev: number
      /** опубликованная версия; если меньше resultRev — текущий результат черновик */
      publishedResultRev?: number
      taskResults: RemoteTaskResult[]
      overallReview: string
    }
export type CheckState = RemoteCheck['state']
export const CHECK_STATE_LABEL: Record<CheckState, string> = {
  queued: 'в очереди на проверку',
  waiting_reference: 'ожидает эталонного решения',
  running: 'проверяется',
  needs_review: 'требует решения учителя',
  failed: 'проверка не удалась',
  ready: 'проверено',
}

export const readCheck = (checkId: string) => call<RemoteCheck>('GET', `/checks/${checkId}`)

/* ---------- правки учителя, публикация, отзыв ---------- */

export type ReviewChange =
  | { type: 'remove_issue'; issueId: string }
  | { type: 'update_issue'; issueId: string; comment: string; correction: string }
  | {
      type: 'update_verdict'
      taskId: string
      answer?: RemoteTaskResult['answer']
      reasoning?: RemoteTaskResult['reasoning']
      method?: RemoteTaskResult['method']
    }
  | { type: 'update_overall_review'; overallReview: string }

/** Правки результата поверх версии baseRev. 409 — результат уже изменён (в ошибке — актуальная версия). */
export const editReview = (checkId: string, baseRev: number, changes: ReviewChange[]) =>
  call<{ checkId: string; resultRev: number; editId: string }>('PATCH', `/checks/${checkId}/review`, {
    baseRev,
    editId: crypto.randomUUID(),
    changes,
  })
export const publishReview = (checkId: string, resultRev: number) =>
  call<{ checkId: string; resultRev: number }>('POST', `/checks/${checkId}/publish`, { resultRev })
/** Опубликованный отзыв (Markdown) — скачиваем файлом. */
export async function downloadFeedback(checkId: string, rev: number, fileName: string) {
  const res = await send('GET', `/checks/${checkId}/markdown?rev=${rev}`)
  const url = URL.createObjectURL(await res.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/* ---------- отчёт по классу ---------- */

export type ReportTask = {
  taskId: string
  correct: number
  incorrect: number
  skipped: number
  unchecked: number
  issuesByCategory: Record<string, number>
  correctShare: number | null
  incorrectShare: number | null
  skippedShare: number | null
}
export type AssignmentReport = {
  assignmentId: string
  assignmentRev: number
  students: RemoteStudent[]
  submittedCount: number
  notSubmitted: RemoteStudent[]
  waiting: RemoteStudent[]
  results: { studentId: string; checkId: string; resultRev: number }[]
  tasks: ReportTask[]
}
export const readReport = (assignmentId: string, rev: number) =>
  call<AssignmentReport>('GET', `/assignments/${assignmentId}/report?rev=${rev}`)
