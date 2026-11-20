import { activeMistakes, taskScore, type Assignment, type MistakeCategory, type StepMistake, type Submission } from './domain'

/** Аналитика по классу: типовые ошибки и темы для повторения. */

export type Example = { submissionId: string; studentId: string | null; taskId: string; mistake: StepMistake }

export type TypicalMistake = {
  category: MistakeCategory
  students: Set<string>
  taskIds: Set<string>
  examples: Example[]
}

export type Topic = {
  taskId: string
  name: string
  share: number // доля учеников с ошибкой среди сдавших номер
  failed: string[] // submissionId
  total: number
  examples: Example[]
  categories: MistakeCategory[]
}

const short = (s: string, n = 70) => (s.length > n ? `${s.slice(0, n - 1).trim()}…` : s)

export function analyzeClass(a: Assignment, subs: Submission[]) {
  const done = subs.filter((s) => s.status === 'processed')
  const typical = new Map<MistakeCategory, TypicalMistake>()
  const topics: Topic[] = []

  for (const t of a.tasks) {
    const failed: string[] = []
    const examples: Example[] = []
    const cats = new Set<MistakeCategory>()
    let total = 0
    for (const s of done) {
      const r = s.results.find((x) => x.taskId === t.id)
      if (!r || r.status === 'missing') continue
      total++
      const ms = activeMistakes(r)
      const sc = taskScore(r)
      const bad = ms.length > 0 || (sc != null && sc < t.max) || r.status === 'insufficient'
      if (bad) failed.push(s.id)
      for (const m of ms) {
        const key = s.studentId ?? s.id
        cats.add(m.category)
        const ex: Example = { submissionId: s.id, studentId: s.studentId, taskId: t.id, mistake: m }
        examples.push(ex)
        const g = typical.get(m.category) ?? { category: m.category, students: new Set(), taskIds: new Set(), examples: [] }
        g.students.add(key)
        g.taskIds.add(t.id)
        g.examples.push(ex)
        typical.set(m.category, g)
      }
    }
    if (failed.length)
      topics.push({
        taskId: t.id,
        name: `№${t.num}. ${short(t.text.split('\n')[0])}`,
        share: total ? failed.length / total : 0,
        failed,
        total,
        examples,
        categories: [...cats],
      })
  }

  return {
    processed: done.length,
    typical: [...typical.values()].sort((x, y) => y.students.size - x.students.size),
    topics: topics.sort((x, y) => y.share - x.share || y.failed.length - x.failed.length),
  }
}
