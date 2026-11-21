// Запросы к кабинету (/v1). Работы учеников уходят в POST /v1/assignments/{assignmentId}/uploads.

export type ClassItem = { classId: string; name: string }
export type Student = { studentId: string; name: string }
export type Assignment = {
  assignmentId: string
  assignmentRev: number
  assignmentDate: string
  subject: string
  /** Название проверочной от учителя (null — не задано). */
  title?: string | null
}
export type Work = Assignment & { classId: string; className: string }
export type Submission = { studentId: string; checkId: string; attemptId: string; pageCount: number }
export type Requirement = { taskNumber: string | null; text: string }
export type TaskPoint = { taskNumber: string; maxScore: number }
export type UploadFile = {
  fileId: string
  originalName: string
  status: 'pending_binding' | 'needs_confirmation' | 'matched' | 'bound' | 'rejected'
  candidates: Student[]
  reason?: string | null
  studentId?: string | null
  checkId?: string | null
  isPhoto?: true | null
  groupId?: string
  pageIndex?: number
  reasonCode?: 'invalid_page_order'
}
export type Upload = { uploadId: string; files: UploadFile[] }

// Результат проверки (GET /v1/checks/{checkId}): пока нет taskResults — проверка идёт или не удалась.
export type TaskResult = {
  taskId: string
  answer: 'correct' | 'incorrect' | 'skipped' | 'unreadable'
  reasoning: string
  method: string
  issues: { issueId: string; stepId: string; category: string; comment: string; correction: string; causedByIssueId?: string | null }[]
  needsReview: boolean
}
export type PhotoBox = { x: number; y: number; w: number; h: number }
export type PhotoMark = { taskId: string; issueId: string; pageIndex: number; lineIndex: number; box?: PhotoBox | null }
export type TaskAssessment = { taskId: string; score?: number | null; maxScore: number; comment: string }
export type Grade = 2 | 3 | 4 | 5
type CheckIdentity = {
  checkId: string
  attemptId: string
  grade?: Grade | null
  photoPages?: { pageIndex: number; url: string; transcriptMarkdown?: string }[]
  transcriptMarkdown?: string | null
  ocrStatus?: 'readable' | 'needs_review' | 'unreadable'
  ocrReason?: string
}
export type ReadyCheck = CheckIdentity & {
  state: 'ready' | 'needs_review'
  resultRev: number
  publishedResultRev?: number | null
  taskResults: TaskResult[]
  overallReview: string
  taskAssessments?: TaskAssessment[]
  total?: { score: number; maxScore: number; pendingCount: number }
  recommendations?: { topic: string; text: string }[]
  teacherComment?: string | null
  photoMarks?: PhotoMark[]
}
export type Check = ReadyCheck | (CheckIdentity & {
  state: 'queued' | 'running' | 'waiting_reference' | 'needs_review' | 'failed'
  reason: string
})
export type ReviewChange =
  | { type: 'set_grade'; grade: Grade | null }
  | { type: 'set_task_score'; taskId: string; score: number | null }
  | { type: 'update_overall_review'; overallReview: string }

// Задания варианта с эталонами (contracts/v1/grading/assignmentreferences.py).
export type References = {
  assignmentId: string
  assignmentRev: number
  state: 'queued' | 'running' | 'waiting_reference' | 'ready' | 'needs_review' | 'failed'
  tasks: {
    taskId: string
    taskNumber: string
    statement: string
    state: 'missing' | 'pending' | 'ready' | 'unsupported' | 'validation_failed'
    answer?: string | null
  }[]
}

// Отчёт кабинета по ревизии варианта (contracts/v1/cabinet/reports.py).
export type ReportTask = {
  taskId: string
  correct: number
  incorrect: number
  skipped: number
  unchecked: number
  unreadable: number
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
  /** grade — оценка учителя 2–5 (null — ещё не выставлена). */
  results: { studentId: string; checkId: string; resultRev: number; grade?: number | null }[]
  tasks: ReportTask[]
  trends: { taskId: string; category: string; topic: string; exampleComment: string }[]
}

async function send(method: string, path: string, body?: unknown): Promise<Response> {
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
  if (!res.ok) {
    const data = await res.json().catch(() => null)
    throw new ApiError(res.status, explain(res.status, data?.message))
  }
  return res
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await send(method, path, body)
  if (response.status === 204) return undefined as T
  return response.json()
}

function explain(status: number, message?: string): string {
  if (status === 401) return 'Кабинет не пустил служебного учителя — проверьте CHECKER_TEACHER_EMAIL/PASSWORD в .env.local'
  if (status === 404) return 'Не найдено'
  if (status === 413) return 'Файл слишком большой'
  if (status === 422) return message ?? 'Проверьте введённые данные'
  if (status === 429) return 'Слишком много попыток, подождите'
  if (status >= 500 && !message) return 'Бэкенд (кабинет) не отвечает — проверьте, что он запущен'
  return message ?? `Ошибка ${status}`
}

const e = encodeURIComponent

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

function feedbackUrl(checkId: string, rev: number) {
  return '/v1/checks/' + e(checkId) + '/markdown?rev=' + rev
}

export const api = {
  classes: () => call<ClassItem[]>('GET', '/v1/classes'),
  createClass: (name: string) => call<ClassItem>('POST', '/v1/classes', { name }),
  addStudent: (classId: string, name: string) => call<Student>('POST', `/v1/classes/${e(classId)}/students`, { name }),
  students: (classId: string) => call<Student[]>('GET', `/v1/classes/${e(classId)}/students`),
  assignments: (classId: string) => call<Assignment[]>('GET', `/v1/classes/${e(classId)}/assignments`),
  uploadTask: (classId: string, file: File, subject: string, title: string,
    options: { assignmentDate: string; requirements: Requirement[]; taskPoints: TaskPoint[] }) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('requirements', JSON.stringify(options.requirements))
    fd.append('assignmentDate', options.assignmentDate)
    fd.append('taskPoints', JSON.stringify(options.taskPoints))
    fd.append('subject', subject)
    fd.append('title', title)
    return call<{ assignmentId: string; assignmentRev: number }>('POST', `/v1/classes/${e(classId)}/assignments`, fd)
  },
  // Удаляет вариант со всеми ревизиями, ответами учеников и результатами.
  deleteAssignment: (assignmentId: string) => call<void>('DELETE', `/v1/assignments/${e(assignmentId)}?confirm=true`),
  uploadWork: (a: Assignment, files: File[]) => {
    const fd = new FormData()
    for (const file of files) fd.append('file', file)
    fd.append('assignmentRev', String(a.assignmentRev))
    return call<{ uploadId: string }>('POST', `/v1/assignments/${e(a.assignmentId)}/uploads`, fd)
  },
  references: (a: Assignment) =>
    call<References>('GET', `/v1/assignments/${e(a.assignmentId)}/references?rev=${a.assignmentRev}`),
  report: (a: Assignment) => call<Report>('GET', `/v1/assignments/${e(a.assignmentId)}/report?rev=${a.assignmentRev}`),
  check: (checkId: string) => call<Check>('GET', `/v1/checks/${e(checkId)}`),
  submissions: (a: Assignment) =>
    call<{ submissions: Submission[] }>('GET', '/v1/assignments/' + e(a.assignmentId) + '/submissions?rev=' + a.assignmentRev),
  deleteStudent: (classId: string, studentId: string) =>
    call<void>('DELETE', '/v1/classes/' + e(classId) + '/students/' + e(studentId)),
  editReview: (checkId: string, baseRev: number, editId: string, changes: ReviewChange[]) =>
    call<{ checkId: string; resultRev: number; editId: string }>('PATCH', '/v1/checks/' + e(checkId) + '/review', {
      baseRev, editId, changes,
    }),
  publish: (checkId: string, resultRev: number) =>
    call<{ checkId: string; resultRev: number }>('POST', `/v1/checks/${e(checkId)}/publish`, { resultRev }),
  feedback: async (checkId: string, rev: number) => (await send('GET', feedbackUrl(checkId, rev))).blob(),
  upload: (uploadId: string) => call<Upload>('GET', `/v1/uploads/${e(uploadId)}`),
  bind: (uploadId: string, fileId: string, target: { studentId: string } | { newStudent: { name: string } }) =>
    call<{ checkId: string }>('POST', '/v1/uploads/' + e(uploadId) + '/bindings', { fileId, ...target }),
}
