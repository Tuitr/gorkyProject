/**
 * Модель данных MVP «Подготовка и проверка работ».
 * Пока бэкенда нет — всё живёт в браузере (store.tsx), формат совпадает с будущим API.
 */
import type { Box } from './types'

export type Id = string

export type Student = { id: Id; name: string }

export type ClassGroup = {
  id: Id
  name: string // «8А»
  ownerId: Id // фото и отчёты видит только владелец класса
  students: Student[]
}

export type WorkType = 'control' | 'homework'
export const WORK_TYPE_LABEL: Record<WorkType, string> = { control: 'Контрольная', homework: 'Домашнее задание' }

/** Один номер исходника. */
export type SourceTask = {
  id: Id
  num: string // «1», «2а»
  text: string
  manual: boolean // устарело: ручная проверка номера убрана из интерфейса, флаг не учитывается
  max: number // максимальный балл
}

export type TaskVersion = { taskId: Id; text: string; answer: string }

export type Variant = { id: Id; name: string; tasks: TaskVersion[] }

/** Признак равнозначности номера в разных вариантах. soft — мягкий, «на границе» (жёлтым). */
export type EquivFeature = { name: string; values: string[]; soft: boolean }

export type SourceFile = { name: string; kind: 'docx' | 'pdf' | 'image' | 'text'; html?: string; dataUrl?: string }

export type Assignment = {
  id: Id
  title: string
  type: WorkType
  createdAt: number
  file?: SourceFile // первый файл (для старых данных)
  files?: SourceFile[] // все файлы с заданиями по порядку
  classIds: Id[]
  notes: string // общие заметки «как проверять»
  classNotes: Record<Id, string> // заметки для конкретного класса
  tasks: SourceTask[]
  variantCount: number
  variants: Variant[] // [0] — вариант А (исходник)
  features: Record<Id, EquivFeature[]> // по taskId
  status: 'draft' | 'approved'
  /** дата работы ГГГГ-ММ-ДД (уходит в кабинет; по умолчанию — день создания) */
  date?: string
  /** задания правили после распознавания — исходный DOCX учителя уже не совпадает с ними */
  sourceEdited?: boolean
  /** работа в кабинете (бэкенд из main): по классу — id работы, ревизия и отпечаток отправленного содержимого */
  remote?: Record<Id, RemoteAssignment>
}

export type RemoteAssignment = { assignmentId: string; rev: number; hash: string }

/** Распознанная строка фото (уже обычным текстом). */
export type PageLine = { text: string; box: Box; fixed?: boolean }

export type Page = {
  id: Id
  image: string // URL или data:URL (в localStorage заменяется на idb:<id>)
  hash: string // защита от повторной загрузки
  lines?: PageLine[]
  /** 'text' — работа сдана документом (DOCX через кабинет): фото нет, только строки текста */
  kind?: 'text'
}

export type MistakeCategory = 'вычислительная' | 'знак' | 'правило' | 'логика' | 'запись' | 'неполное решение' | 'другое'
export const CATEGORIES: MistakeCategory[] = ['вычислительная', 'знак', 'правило', 'логика', 'запись', 'неполное решение', 'другое']
/** Подписи категорий для интерфейса (сами значения используются в запросах к GigaChat). */
export const CATEGORY_LABEL: Record<MistakeCategory, string> = {
  вычислительная: 'вычислительная ошибка',
  знак: 'ошибка в знаке',
  правило: 'неверное применение правила',
  логика: 'логическая ошибка',
  запись: 'ошибка оформления',
  'неполное решение': 'неполное решение',
  другое: 'прочее',
}

export type StepMistake = {
  id: Id
  title: string
  category: MistakeCategory
  where: string
  studentAnswer: string
  correctSolution: string
  explanation: string
  rule?: string // применённое правило из заметок
  page: number
  lines: number[] // номера строк страницы
  box: Box
  dismissed?: boolean
}

export type TaskStatus = 'ok' | 'error' | 'manual' | 'unreadable' | 'missing' | 'stopped' | 'insufficient'
export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  ok: 'верно',
  error: 'есть ошибки',
  manual: 'ручная проверка',
  unreadable: 'неразборчиво',
  missing: 'не сдано',
  stopped: 'проверка остановлена',
  insufficient: 'недостаточно записей для оценки',
}

export type Technique = 'shown' | 'not_shown' | 'not_required'
export const TECHNIQUE_LABEL: Record<Technique, string> = {
  shown: 'применён',
  not_shown: 'не применён',
  not_required: 'не требуется',
}

export type TaskResult = {
  taskId: Id
  status: TaskStatus
  answer: 'correct' | 'wrong' | 'none'
  technique: Technique
  aiScore?: number // предложенный балл
  score?: number // балл учителя (если менял)
  scoreComment?: string
  mistakes: StepMistake[]
  autoReading?: string // «прочтение выбрано автоматически»
  /** результат пришёл из кабинета: номер задания там и вердикты (для правок учителя) */
  remote?: {
    taskId: string
    answer: 'correct' | 'incorrect' | 'skipped' | 'unreadable'
    reasoning: 'valid' | 'invalid' | 'not_shown' | 'unknown'
    method: 'met' | 'unmet' | 'unconfirmed' | 'optional'
  }
}

export type SubmissionStatus = 'none' | 'submitted' | 'processing' | 'processed' | 'error'
export const SUBMISSION_STATUS_LABEL: Record<SubmissionStatus, string> = {
  none: 'Не сдано',
  submitted: 'Сдано',
  processing: 'На проверке',
  processed: 'Проверено',
  error: 'Ошибка проверки',
}

export type Submission = {
  id: Id
  assignmentId: Id
  classId: Id
  studentId: Id | null // null — ученик не определён (очередь «Указать ученика»)
  variantId: Id | null
  pages: Page[]
  status: SubmissionStatus
  error?: string
  results: TaskResult[]
  teacherTotal?: number // итог учителя
  approved: boolean
  summary?: string
  /** работа загружена через кабинет (бэкенд из main) */
  remote?: {
    uploadId: string
    fileId: string
    fileName: string
    attemptId?: string
    checkId?: string
    /** ревизия работы, к которой сдано */
    assignmentRev?: number
    /** состояние проверки в кабинете и причина, если не готово */
    checkState?: string
    checkReason?: string
    /** версия результата кабинета и опубликованная версия */
    resultRev?: number
    publishedRev?: number
  }
}

export type ReviewKind = 'student' | 'sign' | 'method' | 'reshoot'
export const REVIEW_LABEL: Record<ReviewKind, string> = {
  student: 'Указать ученика',
  sign: 'Уточнить распознанный символ',
  method: 'Проверить другой способ решения',
  reshoot: 'Заменить или добавить фото',
}

export type ReviewItem = {
  id: Id
  submissionId: Id
  kind: ReviewKind
  taskId?: Id
  page: number
  box?: Box
  note: string
  readings: string[]
  chosen?: string
  auto: boolean // прочтение выбрано автоматически
  resolved: boolean
}

export type PracticeMode = 'board' | 'groups' | 'individual'
export type PracticeTask = { id: Id; text: string; answer: string; topic: string }
export type PracticeGroup = { id: Id; name: string; topic: string; studentIds: Id[]; tasks: PracticeTask[] }
export type Practice = {
  id: Id
  assignmentId: Id
  classId: Id
  mode: PracticeMode
  board?: { example: string; task: PracticeTask }
  groups: PracticeGroup[]
  approved: boolean
}

/** Фоновая задача: генерация, OCR, проверка. */
export type JobKind = 'generate' | 'ocr' | 'check' | 'practice' | 'sync'
export type Job = {
  id: Id // ключ по объекту — повторный запуск того же не создаёт дубль
  kind: JobKind
  title: string
  status: 'running' | 'error' | 'done'
  error?: string
  startedAt: number
}

export type Teacher = { id: Id; name: string }

export type State = {
  version: 1
  teacher: Teacher
  classes: ClassGroup[]
  assignments: Assignment[]
  submissions: Submission[]
  review: ReviewItem[]
  practices: Practice[]
  /** загрузки работ учеников в кабинет — чтобы вернуться к ним и досмотреть привязки */
  uploads?: CabinetUploadRecord[]
}

export type CabinetUploadRecord = {
  uploadId: string
  assignmentId: Id
  classId: Id
  name: string
  createdAt: number
  /** ревизия работы в кабинете, к которой сдано */
  rev: number
  /** текст DOCX по имени файла — для нашей проверки, пока в кабинете её нет */
  texts: Record<string, string>
}

/* ---------- расчёты ---------- */

export const uid = (p = 'id') => `${p}${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`

/** Балл за номер: учитель > ИИ. undefined — номер не оценён (не показываем как 0). */
export function taskScore(r: TaskResult | undefined): number | undefined {
  if (!r) return undefined
  if (r.score != null) return r.score
  if (r.status === 'manual' || r.status === 'unreadable' || r.status === 'missing') return undefined
  return r.aiScore
}

/** Номер ждёт учителя (сумма — «предварительная»). */
export const needsManual = (r: TaskResult | undefined) =>
  !!r && r.score == null && (r.status === 'manual' || r.status === 'unreadable' || r.status === 'stopped' || !!r.autoReading)

export function submissionTotal(a: Assignment, s: Submission) {
  let sum = 0
  let preliminary = false
  let graded = 0
  for (const t of a.tasks) {
    const r = s.results.find((x) => x.taskId === t.id)
    const v = taskScore(r)
    if (v != null) {
      sum += v
      graded++
    }
    if (needsManual(r)) preliminary = true
  }
  const max = a.tasks.reduce((m, t) => m + t.max, 0)
  return { sum, max, preliminary, graded }
}

export const activeMistakes = (r: TaskResult) => r.mistakes.filter((m) => !m.dismissed)

/** Все файлы исходника: новые работы хранят files, старые — один file. */
export const sourceFiles = (a: Pick<Assignment, 'file' | 'files'>): SourceFile[] => a.files ?? (a.file ? [a.file] : [])
