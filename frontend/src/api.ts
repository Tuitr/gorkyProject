// Запросы к кабинету (/v1). Работы учеников уходят в POST /v1/assignments/{assignmentId}/uploads.

export type ClassItem = { classId: string; name: string }
export type Student = { studentId: string; name: string }
export type Assignment = { assignmentId: string; assignmentRev: number; assignmentDate: string; subject: string }
export type UploadFile = {
  fileId: string
  originalName: string
  status: 'pending_binding' | 'needs_confirmation' | 'matched' | 'bound' | 'rejected'
  candidates: Student[]
  reason?: string | null
  studentId?: string | null
  checkId?: string | null
}
export type Upload = { uploadId: string; files: UploadFile[] }

// Отчёт кабинета по ревизии варианта (contracts/v1/cabinet/reports.py).
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
export type Report = {
  assignmentId: string
  assignmentRev: number
  students: Student[]
  submittedCount: number
  notSubmitted: Student[]
  waiting: Student[]
  results: { studentId: string; checkId: string; resultRev: number }[]
  tasks: ReportTask[]
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: 'same-origin' }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'Content-Type': 'application/json' }
  }
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    throw new Error('Нет связи с сервером')
  }
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(explain(res.status, data?.message))
  return data as T
}

function explain(status: number, message?: string): string {
  if (status === 401) return 'Кабинет не пустил служебного учителя — проверьте CHECKER_TEACHER_EMAIL/PASSWORD в .env.local'
  if (status === 404) return 'Не найдено'
  if (status === 413) return 'Файл слишком большой'
  if (status === 422) return `Файл не принят: ${message ?? 'неверный формат'}`
  if (status === 429) return 'Слишком много попыток, подождите'
  if (status >= 500 && !message) return 'Бэкенд (кабинет) не отвечает — проверьте, что он запущен'
  return message ?? `Ошибка ${status}`
}

const e = encodeURIComponent

export const api = {
  classes: () => call<ClassItem[]>('GET', '/v1/classes'),
  students: (classId: string) => call<Student[]>('GET', `/v1/classes/${e(classId)}/students`),
  assignments: (classId: string) => call<Assignment[]>('GET', `/v1/classes/${e(classId)}/assignments`),
  // Задание (вариант контрольной) — POST /v1/classes/{classId}/assignments, без доп. требований.
  uploadTask: (classId: string, file: File, subject: string) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('requirements', '[]')
    fd.append('subject', subject)
    return call<{ assignmentId: string; assignmentRev: number }>('POST', `/v1/classes/${e(classId)}/assignments`, fd)
  },
  uploadWork: (a: Assignment, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('assignmentRev', String(a.assignmentRev))
    return call<{ uploadId: string }>('POST', `/v1/assignments/${e(a.assignmentId)}/uploads`, fd)
  },
  report: (a: Assignment) => call<Report>('GET', `/v1/assignments/${e(a.assignmentId)}/report?rev=${a.assignmentRev}`),
  upload: (uploadId: string) => call<Upload>('GET', `/v1/uploads/${e(uploadId)}`),
  bind: (uploadId: string, fileId: string, studentId: string) =>
    call<{ checkId: string }>('POST', `/v1/uploads/${e(uploadId)}/bindings`, { fileId, studentId }),
}
