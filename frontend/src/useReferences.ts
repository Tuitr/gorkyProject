import { useEffect, useState } from 'react'
import { readReferences, type AssignmentReferences, type TaskReference } from './api/cabinet'
import { normNum } from './api/tasks'
import type { Assignment } from './domain'

/**
 * Эталонные ответы работы на сервере: grading разбирает сохранённый DOCX и берёт ответы из реестра
 * решателей (разработчик № 3). Пока вариант готовится, спрашиваем кабинет раз в 5 секунд.
 */

const DONE = new Set<AssignmentReferences['state']>(['ready', 'needs_review', 'failed'])
const POLL_MS = 5000
const POLL_LIMIT = 200 // ~16 минут: сборка солвера ГигаЧатом ограничена 15 минутами (SOLVER_BUILD_DEADLINE_S)

export type References = {
  data: AssignmentReferences | null
  error?: string
  /** эталон задания по его номеру у нас («№3» → эталон t3) */
  of: (num: string) => TaskReference | undefined
}

/** Эталоны работы на сервере (первого класса: у всех классов один и тот же DOCX). */
export function useReferences(a: Assignment | undefined): References {
  const remote = a ? Object.values(a.remote ?? {})[0] : undefined
  const key = remote ? `${remote.assignmentId}:${remote.rev}` : ''
  // ответ помним вместе с ключом: сменилась ревизия — старый ответ не показываем
  const [got, setGot] = useState<{ key: string; data: AssignmentReferences | null; error?: string }>({ key: '', data: null })
  useEffect(() => {
    if (!key) return
    const [assignmentId, rev] = key.split(':')
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const run = async (round: number) => {
      try {
        const r = await readReferences(assignmentId, Number(rev))
        if (!alive) return
        setGot({ key, data: r })
        if (!DONE.has(r.state) && round < POLL_LIMIT) timer = setTimeout(() => void run(round + 1), POLL_MS)
      } catch (e) {
        if (alive) setGot((g) => ({ key, data: g.key === key ? g.data : null, error: e instanceof Error ? e.message : 'Эталоны недоступны' }))
      }
    }
    void run(0)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [key])
  const data = got.key === key ? got.data : null
  const error = got.key === key ? got.error : undefined
  const of = (num: string) => data?.tasks.find((t) => normNum(t.taskNumber) === normNum(num))
  return { data, error, of }
}

