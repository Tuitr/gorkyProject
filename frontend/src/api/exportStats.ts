import {
  activeMistakes,
  CATEGORIES,
  CATEGORY_LABEL,
  submissionTotal,
  SUBMISSION_STATUS_LABEL,
  taskScore,
  WORK_TYPE_LABEL,
  type Assignment,
  type ClassGroup,
  type State,
  type Submission,
} from '../domain'
import { makeXlsx, type XCell, type XSheet } from '../lib/xlsx'

/**
 * Excel со статистикой по всем контрольным и домашним заданиям учителя.
 * Итоговый балл — как в ведомости: балл учителя, если он его поставил, иначе сумма по номерам.
 */

const isoDate = (a: Assignment) => a.date ?? new Date(a.createdAt - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 10)
const ruDate = (iso: string) => iso.split('-').reverse().join('.')

/** Сданная работа ученика: проверенная важнее остальных. */
function submissionOf(subs: Submission[], studentId: string) {
  const own = subs.filter((s) => s.studentId === studentId)
  return own.find((s) => s.status === 'processed') ?? own[0]
}

type Final = { score: number; max: number; preliminary: boolean }
function finalOf(a: Assignment, s: Submission | undefined): Final | null {
  if (!s || s.status !== 'processed') return null
  const t = submissionTotal(a, s)
  return { score: s.teacherTotal ?? t.sum, max: t.max, preliminary: s.teacherTotal == null && t.preliminary }
}

const pct = (f: Final | null): XCell => (f && f.max > 0 ? { pct: f.score / f.max } : null)
const avg = (xs: number[]) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null)
const mistakesOf = (s: Submission) => s.results.reduce((n, r) => n + activeMistakes(r).length, 0)

function submitted(s: Submission | undefined) {
  return !!s && s.status !== 'none'
}

export function buildStatsSheets(state: State): XSheet[] {
  const classById = new Map(state.classes.map((c) => [c.id, c]))
  const works = [...state.assignments].sort((a, b) => isoDate(a).localeCompare(isoDate(b)) || a.createdAt - b.createdAt)
  const classesOf = (a: Assignment) => a.classIds.map((id) => classById.get(id)).filter((c): c is ClassGroup => !!c)
  const subsOf = (a: Assignment, c: ClassGroup) => state.submissions.filter((s) => s.assignmentId === a.id && s.classId === c.id)
  const openReview = (subs: Submission[]) =>
    state.review.filter((r) => !r.resolved && subs.some((s) => s.id === r.submissionId)).length

  /* 1. Сводка: работа × класс */
  const summary: XCell[][] = []
  for (const a of works)
    for (const c of classesOf(a)) {
      const subs = subsOf(a, c)
      const finals = c.students.map((st) => finalOf(a, submissionOf(subs, st.id))).filter((f): f is Final => !!f)
      const scores = finals.map((f) => f.score)
      const max = a.tasks.reduce((m, t) => m + t.max, 0)
      const handed = c.students.filter((st) => submitted(submissionOf(subs, st.id))).length
      const approved = c.students.filter((st) => submissionOf(subs, st.id)?.approved).length
      const avgScore = avg(scores)
      summary.push([
        a.title,
        WORK_TYPE_LABEL[a.type],
        { date: isoDate(a) },
        c.name,
        a.tasks.length,
        max,
        c.students.length,
        handed,
        finals.length,
        approved,
        avgScore == null ? null : { num: avgScore },
        avgScore == null || !max ? null : { pct: avgScore / max },
        scores.length ? Math.min(...scores) : null,
        scores.length ? Math.max(...scores) : null,
        subs.reduce((n, s) => n + mistakesOf(s), 0),
        openReview(subs),
      ])
    }

  /* 2. Ученики × работы (процент выполнения) */
  const ownClasses = state.classes.filter((c) => c.ownerId === state.teacher.id).sort((x, y) => x.name.localeCompare(y.name, 'ru', { numeric: true }))
  const matrixWorks = works.filter((a) => classesOf(a).length)
  const matrix: XCell[][] = []
  for (const c of ownClasses)
    for (const st of c.students) {
      const cells: XCell[] = []
      const pcts: number[] = []
      let handed = 0
      let missed = 0
      for (const a of matrixWorks) {
        if (!a.classIds.includes(c.id)) {
          cells.push(null)
          continue
        }
        const s = submissionOf(subsOf(a, c), st.id)
        const f = finalOf(a, s)
        if (f && f.max > 0) {
          pcts.push(f.score / f.max)
          cells.push({ pct: f.score / f.max })
        } else cells.push(submitted(s) ? 'не проверено' : 'не сдано')
        if (submitted(s)) handed++
        else missed++
      }
      const mean = avg(pcts)
      matrix.push([c.name, st.name, ...cells, mean == null ? null : { pct: mean }, handed, missed])
    }

  /* 3. Результаты: каждая сданная работа */
  const results: XCell[][] = []
  /* 4. Задания: по каждому номеру */
  const tasks: XCell[][] = []
  /* 5. Ошибки */
  const mistakes: XCell[][] = []
  for (const a of works)
    for (const c of classesOf(a)) {
      const subs = subsOf(a, c).filter(submitted)
      const nameOf = (s: Submission) => c.students.find((st) => st.id === s.studentId)?.name ?? 'ученик не указан'
      for (const s of subs) {
        const f = finalOf(a, s)
        const variant = a.variants.find((v) => v.id === s.variantId)?.name ?? (a.variants.length ? a.variants[0].name : '')
        results.push([
          c.name,
          nameOf(s),
          a.title,
          WORK_TYPE_LABEL[a.type],
          { date: isoDate(a) },
          variant,
          SUBMISSION_STATUS_LABEL[s.status],
          f?.score ?? null,
          f?.max ?? null,
          pct(f),
          s.approved ? 'да' : 'нет',
          f?.preliminary ? 'да' : '',
          s.status === 'processed' ? mistakesOf(s) : null,
          s.status === 'processed'
            ? a.tasks
                .map((t) => {
                  const v = taskScore(s.results.find((r) => r.taskId === t.id))
                  return `№${t.num}: ${v ?? '—'}/${t.max}`
                })
                .join('; ')
            : '',
        ])
        for (const r of s.results) {
          const t = a.tasks.find((x) => x.id === r.taskId)
          for (const m of activeMistakes(r))
            mistakes.push([
              a.title,
              { date: isoDate(a) },
              c.name,
              nameOf(s),
              t ? `№${t.num}` : '',
              m.title,
              CATEGORY_LABEL[m.category] ?? m.category,
              m.where,
              m.studentAnswer,
              m.correctSolution,
              m.explanation,
            ])
        }
      }
      const checked = subs.filter((s) => s.status === 'processed')
      for (const t of a.tasks) {
        const rs = checked.map((s) => s.results.find((r) => r.taskId === t.id))
        const scores = rs.map(taskScore).filter((v): v is number => v != null)
        const mean = avg(scores)
        tasks.push([
          a.title,
          { date: isoDate(a) },
          c.name,
          `№${t.num}`,
          t.text,
          t.max,
          scores.length,
          mean == null ? null : { num: mean },
          mean == null || !t.max ? null : { pct: mean / t.max },
          rs.filter((r) => r?.status === 'ok').length,
          rs.filter((r) => r?.status === 'error').length,
          rs.filter((r) => !r || r.status === 'missing' || r.status === 'insufficient').length,
          rs.reduce((n, r) => n + (r ? activeMistakes(r).length : 0), 0),
        ])
      }
    }

  /* 6. Категории ошибок по работам */
  const byCat: XCell[][] = []
  const total = CATEGORIES.map(() => 0)
  for (const a of works) {
    const counts = CATEGORIES.map(() => 0)
    for (const s of state.submissions)
      if (s.assignmentId === a.id)
        for (const r of s.results)
          for (const m of activeMistakes(r)) {
            const i = Math.max(0, CATEGORIES.indexOf(m.category))
            counts[i]++
            total[i]++
          }
    byCat.push([a.title, WORK_TYPE_LABEL[a.type], { date: isoDate(a) }, ...counts, counts.reduce((x, y) => x + y, 0)])
  }
  if (works.length) byCat.push([{ bold: 'Итого' }, null, null, ...total, total.reduce((x, y) => x + y, 0)])

  return [
    {
      name: 'Сводка',
      header: ['Работа', 'Тип', 'Дата', 'Класс', 'Заданий', 'Макс. балл', 'Учеников', 'Сдано', 'Проверено', 'Утверждено', 'Средний балл', 'Средний %', 'Мин. балл', 'Лучший балл', 'Ошибок', 'Ждёт проверки учителем'],
      widths: [30, 18, 12, 8, 9, 10, 10, 8, 11, 12, 13, 11, 10, 12, 9, 14],
      rows: summary,
    },
    {
      name: 'Ученики',
      header: ['Класс', 'Ученик', ...matrixWorks.map((a) => `${a.title} (${ruDate(isoDate(a))})`), 'Средний %', 'Сдано работ', 'Не сдано'],
      widths: [8, 28, ...matrixWorks.map(() => 16), 11, 12, 10],
      rows: matrix,
    },
    {
      name: 'Результаты',
      header: ['Класс', 'Ученик', 'Работа', 'Тип', 'Дата', 'Вариант', 'Статус', 'Балл', 'Макс.', '%', 'Утверждено', 'Предварительный балл', 'Ошибок', 'Баллы по заданиям'],
      widths: [8, 28, 30, 18, 12, 9, 14, 7, 7, 8, 12, 14, 9, 50],
      rows: results,
    },
    {
      name: 'Задания',
      header: ['Работа', 'Дата', 'Класс', '№', 'Условие', 'Макс.', 'Оценено работ', 'Средний балл', 'Средний %', 'Верно', 'С ошибками', 'Нет решения', 'Ошибок'],
      widths: [30, 12, 8, 6, 60, 7, 12, 12, 11, 8, 11, 12, 9],
      rows: tasks,
      wrap: [4],
    },
    {
      name: 'Ошибки',
      header: ['Работа', 'Дата', 'Класс', 'Ученик', '№', 'Ошибка', 'Категория', 'Где', 'Ответ ученика', 'Верное решение', 'Пояснение'],
      widths: [30, 12, 8, 28, 6, 30, 24, 30, 30, 40, 40],
      rows: mistakes,
      wrap: [5, 7, 8, 9, 10],
    },
    {
      name: 'Категории ошибок',
      header: ['Работа', 'Тип', 'Дата', ...CATEGORIES.map((c) => CATEGORY_LABEL[c]), 'Всего'],
      widths: [30, 18, 12, ...CATEGORIES.map(() => 16), 9],
      rows: byCat,
    },
  ]
}

/** Скачать Excel со статистикой по всем работам. */
export function downloadStats(state: State) {
  const blob = makeXlsx(buildStatsSheets(state))
  const today = new Date()
  const name = `Статистика ${String(today.getDate()).padStart(2, '0')}.${String(today.getMonth() + 1).padStart(2, '0')}.${today.getFullYear()}.xlsx`
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
