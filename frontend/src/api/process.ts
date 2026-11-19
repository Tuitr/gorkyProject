import type {
  Assignment,
  ClassGroup,
  MistakeCategory,
  Page,
  PageLine,
  ReviewItem,
  StepMistake,
  Submission,
  TaskResult,
  Technique,
} from '../domain'
import { CATEGORIES, uid } from '../domain'
import type { Box } from '../types'
import { recognize } from './ocr'

/**
 * Обработка сдачи ученика: OCR каждой страницы → одна проверка GigaChat по всем страницам →
 * результаты по номерам, рамки ошибок на нужной странице, элементы очереди просмотра.
 */

type Raw = Record<string, unknown>
const str = (v: unknown) => (typeof v === 'string' ? v : v == null ? '' : String(v))
const nums = (v: unknown) => (Array.isArray(v) ? v.map(Number).filter((n) => Number.isInteger(n)) : [])
const arr = (v: unknown): Raw[] => (Array.isArray(v) ? (v as Raw[]) : [])

/** «№2», «N 2», «2)» → «2» */
export const normNum = (label: string) => label.replace(/^[^\dа-яa-z]*/i, '').replace(/[).\s]+$/, '').trim().toLowerCase()

export function unionBox(boxes: Box[]): Box | null {
  const bs = boxes.filter((b) => b.w > 0)
  if (!bs.length) return null
  const pad = 0.008
  const x0 = Math.max(0, Math.min(...bs.map((b) => b.x)) - pad)
  const y0 = Math.max(0, Math.min(...bs.map((b) => b.y)) - pad)
  const x1 = Math.min(1, Math.max(...bs.map((b) => b.x + b.w)) + pad)
  const y1 = Math.min(1, Math.max(...bs.map((b) => b.y + b.h)) + pad)
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

export async function callCheck(lines: string[], task?: string, notes?: string): Promise<Raw> {
  const res = await fetch('/api/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines, task, notes }),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(data?.message ?? `Проверка: ошибка ${res.status}`)
  return (data ?? {}) as Raw
}

/** Текст задания для модели: условия выбранного варианта, ключ и максимум баллов. */
export function taskBrief(a: Assignment, variantId: string | null) {
  const v = a.variants.find((x) => x.id === variantId) ?? a.variants[0]
  return a.tasks
    .map((t) => {
      const tv = v?.tasks.find((x) => x.taskId === t.id)
      const text = tv?.text || t.text
      return `№${t.num} (макс. ${t.max}${t.manual ? ', ручная проверка' : ''}): ${text}${tv?.answer ? `\n   Ответ: ${tv.answer}` : ''}`
    })
    .join('\n')
}

export function notesFor(a: Assignment, classId: string) {
  return [a.notes, a.classNotes[classId]].filter((s) => s?.trim()).join('\n')
}

const STATUS: Record<string, TaskResult['status']> = {
  нечитаемо: 'unreadable',
  'нет решения': 'insufficient',
  'недостаточно записи': 'insufficient',
}
const TECH: Record<string, Technique> = { показан: 'shown', 'не показан': 'not_shown', 'не требуется': 'not_required' }

export type ProcessOutput = {
  pages: Page[]
  results: TaskResult[]
  review: ReviewItem[]
  summary: string
  studentName: string
}

export async function processSubmission(
  a: Assignment,
  s: Submission,
  cls: ClassGroup | undefined,
  onStage?: (stage: 'ocr' | 'check') => void,
): Promise<ProcessOutput> {
  if (!s.pages.length) throw new Error('Нет ни одной страницы — снимите работу.')

  // 1. OCR (уже распознанные страницы не гоняем повторно)
  onStage?.('ocr')
  const pages: Page[] = []
  for (const p of s.pages) {
    if (p.lines?.length) pages.push(p)
    else {
      const ocr = await recognize(p.image)
      pages.push({ ...p, lines: ocr.lines.map((l) => ({ text: l.text, box: l.box })) })
    }
  }

  // 2. Все строки всех страниц в один список: глобальный номер → (страница, строка)
  const index: { page: number; line: number }[] = []
  const texts: string[] = []
  pages.forEach((p, pi) =>
    (p.lines ?? []).forEach((l, li) => {
      index.push({ page: pi, line: li })
      texts.push(pages.length > 1 && li === 0 ? `[стр. ${pi + 1}] ${l.text}` : l.text)
    }),
  )
  if (!texts.length) throw new Error('На фото не найден текст — переснимите страницу ровнее и ближе.')

  onStage?.('check')
  const raw = await callCheck(texts, taskBrief(a, s.variantId), notesFor(a, s.classId))

  const locate = (ids: number[]) => {
    const refs = ids.map((i) => index[i]).filter(Boolean)
    const page = refs[0]?.page ?? 0
    const own = refs.filter((r) => r.page === page)
    const box = unionBox(own.map((r) => (pages[page].lines as PageLine[])[r.line].box))
    return { page, lines: own.map((r) => r.line), box }
  }

  const byNum = new Map(a.tasks.map((t) => [normNum(t.num), t]))
  const rawTasks = arr(raw.tasks)
  const rawMistakes = arr(raw.mistakes)

  const results: TaskResult[] = a.tasks.map((t) => {
    const rt = rawTasks.find((x) => normNum(str(x.label)) === normNum(t.num))
    const ms: StepMistake[] = rawMistakes
      .filter((m) => normNum(str(m.task)) === normNum(t.num))
      .map((m) => {
        const loc = locate(nums(m.lines))
        const cat = str(m.category) as MistakeCategory
        return {
          id: uid('m'),
          title: str(m.title) || 'Ошибка',
          category: CATEGORIES.includes(cat) ? cat : 'другое',
          where: str(m.where),
          studentAnswer: str(m.studentAnswer) || '—',
          correctSolution: str(m.correctSolution) || '—',
          explanation: str(m.explanation),
          rule: str(m.rule) || undefined,
          page: loc.page,
          lines: loc.lines,
          box: loc.box ?? { x: 0, y: 0, w: 0, h: 0 },
        }
      })
    if (!rt && !ms.length) {
      return { taskId: t.id, status: 'missing', answer: 'none', technique: 'not_shown', mistakes: [] }
    }
    const st = STATUS[str(rt?.status)]
    const ok = rt?.ok === true && !ms.length
    const score = Number(rt?.score)
    return {
      taskId: t.id,
      status: t.manual ? 'manual' : st ?? (ok ? 'ok' : 'error'),
      answer: str(rt?.answer) === 'верно' ? 'correct' : str(rt?.answer) === 'нет' ? 'none' : ok ? 'correct' : 'wrong',
      technique: TECH[str(rt?.technique)] ?? (ok ? 'shown' : 'not_shown'),
      aiScore: Number.isFinite(score) ? Math.max(0, Math.min(t.max, score)) : ok ? t.max : 0,
      mistakes: ms,
    }
  })

  // 3. Очередь просмотра: сомнительные прочтения и нераспознанный ученик
  const review: ReviewItem[] = []
  for (const d of arr(raw.doubts)) {
    const t = byNum.get(normNum(str(d.task)))
    const loc = locate(nums([d.line]))
    const options = Array.isArray(d.options) ? d.options.map(str).filter(Boolean) : []
    if (!options.length) continue
    const chosen = str(d.chosen) || options[0]
    review.push({
      id: uid('r'),
      submissionId: s.id,
      kind: 'sign',
      taskId: t?.id,
      page: loc.page,
      box: loc.box ?? undefined,
      note: str(d.note) || 'Символ читается неуверенно.',
      readings: options,
      chosen,
      auto: true,
      resolved: false,
    })
    const r = results.find((x) => x.taskId === t?.id)
    if (r) r.autoReading = `Прочитано как «${chosen}» (варианты: ${options.join(', ')})`
  }
  for (const r of results) {
    if (r.status === 'unreadable') {
      review.push({
        id: uid('r'),
        submissionId: s.id,
        kind: 'reshoot',
        taskId: r.taskId,
        page: 0,
        note: `№${a.tasks.find((t) => t.id === r.taskId)?.num}: запись не читается — переснимите или добавьте страницу.`,
        readings: [],
        auto: false,
        resolved: false,
      })
    }
  }
  const studentName = str(raw.studentName)
  if (!s.studentId) {
    const first = studentName.toLowerCase().split(/\s+/)[0]
    const guess = first ? cls?.students.find((st) => st.name.toLowerCase().includes(first)) : undefined
    review.push({
      id: uid('r'),
      submissionId: s.id,
      kind: 'student',
      page: 0,
      note: studentName ? `На листе подписано: «${studentName}».` : 'Подпись на листе не найдена.',
      readings: guess ? [guess.id] : [],
      chosen: guess?.id,
      auto: !!guess,
      resolved: false,
    })
  }

  return { pages, results, review, summary: str(raw.summary), studentName }
}
