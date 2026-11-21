import { api, type Check, type ReadyCheck, type Report, type Requirement, type Student, type Submission, type TaskPoint, type Work } from './api'

export type Row = { student: Student; submission?: Submission; check?: Check }
export type TaskColumn = { id: string; number: string }

// Платформа только для математики.
export const subjectName = () => 'Математика'
export const workName = (work: Work) => work.title || 'Контрольная по математике'
export const taskNumber = (taskId: string) => taskId.replace(/^[Tt](?=\d)/, '')
export const number = (n: number) => n.toLocaleString('ru-RU', { maximumFractionDigits: 4 })
export const ready = (check?: Check): ReadyCheck | undefined => check && 'taskResults' in check ? check : undefined

export function dateLabel(value: string) {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(value))
}

export function today() {
  const now = new Date()
  return [now.getFullYear(), now.getMonth() + 1, now.getDate()].map((value) => String(value).padStart(2, '0')).join('-')
}

export const categories: Record<string, string> = {
  sign: 'Ошибки в знаках', sign_error: 'Ошибки в знаках', arithmetic: 'Вычисления',
  formula: 'Выбор формулы', substitution: 'Подстановка', notation: 'Запись решения',
  missing_stage: 'Пропущенный шаг', logic: 'Ход рассуждения', units: 'Единицы измерения', other: 'Другие ошибки',
}

export function rowStatus(row: Row) {
  const check = row.check
  if (!row.submission) return { label: 'Не сдано', tone: 'muted' }
  if (check?.ocrStatus === 'unreadable') return { label: 'Ручная проверка', tone: 'danger' }
  if (check?.state === 'failed') return { label: 'Ошибка проверки', tone: 'danger' }
  if (check?.state === 'needs_review' || check?.ocrStatus === 'needs_review' || ready(check)?.total?.pendingCount) {
    return { label: ready(check) ? 'Проверить фрагмент' : 'Ручная проверка', tone: 'warning' }
  }
  if (check?.state === 'ready') return { label: 'Проверено', tone: 'success' }
  if (check?.state === 'waiting_reference') return { label: 'Ожидает эталон', tone: 'muted' }
  return { label: 'Проверяется', tone: 'muted' }
}

export function taskCell(check: Check | undefined, taskId: string) {
  const result = ready(check)
  const assessment = result?.taskAssessments?.find((item) => item.taskId === taskId)
  const task = result?.taskResults.find((item) => item.taskId === taskId)
  return {
    score: assessment?.score,
    maxScore: assessment?.maxScore,
    comment: assessment?.comment ?? task?.issues[0]?.comment ?? '',
  }
}

export function requirementsOf(notes: string): { requirements: Requirement[]; taskPoints: TaskPoint[]; error?: string } {
  const requirements: Requirement[] = []
  const maxima = new Map<string, number>()
  let error: string | undefined
  for (const entry of notes.split(/(?:\r?\n|;\s*|\.\s+(?=№))/)) {
    const text = entry.trim()
    if (!text) continue
    const task = /^№\s*([1-9][0-9]*)\b/.exec(text)?.[1] ?? null
    const points = /^№\s*([1-9][0-9]*)\s*(?:[-–—=:]\s*)?([0-9]+(?:[,.][0-9]+)?)\s*балл(?:а|ов)?(?:[.\s]|$)/i.exec(text)
    if (points) {
      const value = Number(points[2].replace(',', '.'))
      if (!Number.isFinite(value) || value <= 0) error = 'Максимальный балл должен быть больше нуля.'
      else if (maxima.has(points[1]) && maxima.get(points[1]) !== value) error = 'У задания № ' + points[1] + ' указаны разные максимальные баллы.'
      else maxima.set(points[1], value)
      const rest = text.slice(points[0].length).trim()
      if (rest) requirements.push({ taskNumber: task, text: rest })
    } else {
      requirements.push({ taskNumber: task, text })
    }
  }
  return { requirements, taskPoints: [...maxima].map(([taskNumber, maxScore]) => ({ taskNumber, maxScore })), error }
}

export function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename.replace(/[<>:"/\\|?*]/g, '–')
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export async function exportFeedback(check: ReadyCheck, student: Student) {
  if (check.publishedResultRev == null) return
  download(await api.feedback(check.checkId, check.publishedResultRev), student.name + ' — отзыв.md')
}

export async function exportWorkbook(work: Work, rows: Row[], tasks: TaskColumn[], report: Report | undefined, mode: 'table' | 'report') {
  const { makeXlsx } = await import('./lib/xlsx')
  const grade = (row: Row) => row.check?.grade === undefined
    ? report?.results.find((result) => result.checkId === row.submission?.checkId)?.grade
    : row.check.grade
  const table = {
    name: 'Ведомость',
    header: ['Ученик', ...tasks.flatMap((task) => ['№ ' + task.number + ' · балл', '№ ' + task.number + ' · максимум', '№ ' + task.number + ' · комментарий']), 'Сумма', 'Максимум', 'Оценка', 'Статус'],
    widths: [28, ...tasks.flatMap(() => [14, 14, 44]), 12, 12, 12, 22],
    wrap: tasks.map((_, index) => 3 * index + 3),
    rows: rows.map((row) => {
      const total = ready(row.check)?.total
      return [row.student.name, ...tasks.flatMap((task) => {
        const value = taskCell(row.check, task.id)
        return [value.score, value.maxScore, value.comment]
      }), total?.score, total?.maxScore, grade(row), rowStatus(row).label]
    }),
  }
  const statistics = {
    name: 'Отчёт класса',
    header: ['Задание', 'Верно', 'Неверно', 'Пропущено', 'Неразборчиво', 'Доля неверных'],
    widths: [18, 14, 14, 16, 17, 18],
    rows: (report?.tasks ?? []).map((task) => [
      '№ ' + taskNumber(task.taskId), task.correct, task.incorrect, task.skipped, task.unreadable,
      task.incorrectShare == null ? null : { pct: task.incorrectShare },
    ]),
  }
  const trends = {
    name: 'Темы для повторения',
    header: ['Задание', 'Ошибка', 'Ученики', 'Тема', 'Пример'],
    widths: [16, 24, 15, 34, 50],
    wrap: [3, 4],
    rows: (report?.trends ?? []).map((trend) => [
      '№ ' + taskNumber(trend.taskId), categories[trend.category] ?? trend.category,
      report?.tasks.find((task) => task.taskId === trend.taskId)?.issuesByCategory[trend.category],
      trend.topic, trend.exampleComment,
    ]),
  }
  download(makeXlsx(mode === 'table' ? [table] : [statistics, trends]), workName(work) + ' — ' + (mode === 'table' ? 'ведомость' : 'отчёт') + '.xlsx')
}

export async function readWorks() {
  const classes = await api.classes()
  const workGroups = await Promise.all(classes.map(async (item) => (await api.assignments(item.classId)).map((a) => ({
    ...a, classId: item.classId, className: item.name,
  }))))
  const works = workGroups.flat().sort((left, right) => right.assignmentDate.localeCompare(left.assignmentDate)
    || workName(left).localeCompare(workName(right), 'ru'))
  return { classes, works }
}
