import type { AuthUser } from '../store'

/**
 * Бэкенд «кабинет» (services/cabinet, публичный API /v1) — единственный сервер фронтенда.
 * Сервер Vite пробрасывает /v1 в кабинет (CHECKER_CABINET_URL, по умолчанию http://127.0.0.1:18080).
 * Через кабинет идут вход, классы и ученики, работы (DOCX + требования), загрузка работ учеников
 * (DOCX или ZIP), результаты проверки grading, эталоны реестра, правки учителя, отчёт и Markdown.
 */

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
  if (path === '/teachers/register' && code === 'conflict') msg = 'Учитель с такой почтой уже зарегистрирован.'
  if (path === '/teachers/register' && code === 'invalid_input') msg = 'Проверьте почту и пароль (не менее 8 символов).'
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

export const cabinetRegister = (email: string, password: string, displayName: string) =>
  call<TeacherSession>('POST', '/teachers/register', { email, password, displayName })
export const cabinetLogin = async (email: string, password: string) =>
  toUser(await call<TeacherSession>('POST', '/sessions', { email, password }))
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
/** Удаление в кабинете мягкое: класс, ученик, работа или сдача пропадают из списков, история остаётся в базе. */
export const deleteClass = (classId: string) => call<void>('DELETE', `/classes/${classId}`)
export const deleteStudent = (classId: string, studentId: string) =>
  call<void>('DELETE', `/classes/${classId}/students/${studentId}`)
/** Работа удаляется вместе со всеми сдачами — кабинет требует подтверждения (confirm=true). */
export const deleteAssignment = (assignmentId: string) => call<void>('DELETE', `/assignments/${assignmentId}?confirm=true`)
export const deleteAttempt = (attemptId: string) => call<void>('DELETE', `/attempts/${attemptId}?confirm=true`)

/* ---------- работа (вариант) ---------- */

export type Requirement = { text: string; taskNumber: string | null }

/** Сохраняет работу классу: DOCX + требования (+ дата ГГГГ-ММ-ДД, предмет). С assignmentId — новая ревизия той же работы. */
export function uploadAssignment(
  classId: string,
  docx: Blob,
  requirements: Requirement[],
  assignmentId?: string,
  assignmentDate?: string,
  fileName = 'assignment.docx',
  subject: 'math' | 'physics' = 'math',
) {
  const form = new FormData()
  form.append('file', docx, fileName)
  form.append('requirements', JSON.stringify(requirements))
  form.append('subject', subject)
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
/** Результата ещё нет (или проверка не удалась): состояние и причина. */
export type RemoteCheckPending = {
  checkId: string
  attemptId: string
  state: 'queued' | 'waiting_reference' | 'running' | 'needs_review' | 'failed'
  reason: string
}
/** Результат есть; needs_review — у части заданий стоит needsReview, их решает учитель. */
export type RemoteCheckResult = {
  checkId: string
  attemptId: string
  state: 'ready' | 'needs_review'
  resultRev: number
  /** опубликованная версия; если меньше resultRev — текущий результат черновик */
  publishedResultRev?: number | null
  taskResults: RemoteTaskResult[]
  overallReview: string
}
export type RemoteCheck = RemoteCheckPending | RemoteCheckResult
export const hasResult = (c: RemoteCheck): c is RemoteCheckResult => 'taskResults' in c
export type CheckState = RemoteCheck['state']
export const CHECK_STATE_LABEL: Record<CheckState, string> = {
  queued: 'в очереди на проверку',
  waiting_reference: 'ожидает эталонного решения',
  running: 'проверяется',
  needs_review: 'требует решения учителя',
  failed: 'проверка не удалась',
  ready: 'проверено',
}
/** Причины grading (коды) — словами для учителя. */
const REASON_LABEL: Record<string, string> = {
  queued_for_grading: 'Работа ждёт проверки.',
  awaiting_reference: 'Готовится эталонное решение.',
  grading_in_progress: 'Работа проверяется.',
  awaiting_artifact: 'Готовится отзыв.',
  grading_failed: 'Проверка не удалась после нескольких попыток.',
  assignment_failed: 'Не удалось разобрать вариант: задания не найдены. Проверьте нумерацию «1.», «2.» в DOCX.',
  manual_review: 'Работу нужно проверить вручную.',
  reference_unavailable: 'Для задания нет эталонного ответа: его проверяет учитель.',
  // почему у задания нет эталона (реестр решателей, разработчик № 3)
  unknown_task: 'Для такого задания нет готового эталона.',
  generation_failed: 'ГигаЧат не смог написать работающий солвер для задания.',
  independent_tests_failed: 'Независимые солверы дали разные ответы: эталон не принят.',
  generation_timeout: 'Солвер для задания не успели подготовить.',
  provider_error: 'ГигаЧат отказал во входе: проверьте ключ и GIGACHAT_SCOPE, затем пересохраните работу.',
  personal_data_suspected: 'В условии похоже на персональные данные: в ГигаЧат оно не отправлялось.',
  solver_failed: 'Солвер для задания снят с использования.',
}
export const reasonText = (reason: string) => REASON_LABEL[reason] ?? reason

export const readCheck = (checkId: string) => call<RemoteCheck>('GET', `/checks/${checkId}`)

/* ---------- эталоны реестра (разработчик № 3), которые grading подготовил к варианту ---------- */

export type TaskReference = {
  taskId: string
  taskNumber: string
  statement: string
  state: 'missing' | 'pending' | 'ready' | 'unsupported' | 'validation_failed'
  /** готовый ответ scalar.v1 строкой, например «13» или «-3/4» */
  answer?: string | null
  reasonCode?: string | null
}
export type AssignmentReferences = {
  assignmentId: string
  assignmentRev: number
  state: 'queued' | 'running' | 'waiting_reference' | 'ready' | 'needs_review' | 'failed'
  tasks: TaskReference[]
}
export const readReferences = (assignmentId: string, rev: number) =>
  call<AssignmentReferences>('GET', `/assignments/${assignmentId}/references?rev=${rev}`)

/* ---------- правки учителя, публикация, отзыв ---------- */

/** issueId уникален внутри задания, поэтому к замечанию передаём и taskId задания (как его назвал grading). */
export type ReviewChange =
  | { type: 'remove_issue'; issueId: string; taskId?: string }
  | { type: 'update_issue'; issueId: string; taskId?: string; comment: string; correction: string }
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
