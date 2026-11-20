import { useCallback } from 'react'
import { generateVariants } from './api/generate'
import { processSubmission } from './api/process'
import type { State, Submission } from './domain'
import { patch, useStore } from './store'

/** Фоновые действия: обработка сдач (OCR → GigaChat) и генерация вариантов. */
export function useActions() {
  const { update, getState, runJob, toast } = useStore()

  const setSub = useCallback(
    (id: string, fn: (s: Submission) => Submission) =>
      update((st) => ({ ...st, submissions: patch(st.submissions, id, fn) })),
    [update],
  )

  const process = useCallback(
    (submissionId: string) =>
      runJob(`check:${submissionId}`, 'check', 'Проверка работы', async () => {
        const st = getState()
        const s = st.submissions.find((x) => x.id === submissionId)
        const a = st.assignments.find((x) => x.id === s?.assignmentId)
        if (!s || !a) return
        setSub(s.id, (x) => ({ ...x, status: 'processing', error: undefined }))
        try {
          const out = await processSubmission(a, s, st.classes.find((c) => c.id === s.classId))
          update((cur: State) => ({
            ...cur,
            submissions: patch(cur.submissions, s.id, (x) => ({
              ...x,
              pages: out.pages,
              results: out.results,
              summary: out.summary,
              status: 'processed',
              approved: false,
            })),
            // старые нерешённые пункты этой сдачи заменяем новыми
            review: [...cur.review.filter((r) => r.submissionId !== s.id || r.resolved), ...out.review],
          }))
        } catch (e) {
          const msg = e instanceof Error ? e.message : 'Ошибка обработки'
          setSub(s.id, (x) => ({ ...x, status: 'error', error: msg }))
          throw e
        }
      }),
    [getState, runJob, setSub, update],
  )

  /** Обработать все сданные (и упавшие) работы класса по очереди. */
  const processAll = useCallback(
    async (assignmentId: string, classId: string) => {
      const list = getState().submissions.filter(
        (s) =>
          s.assignmentId === assignmentId &&
          s.classId === classId &&
          s.pages.length > 0 &&
          (s.status === 'submitted' || s.status === 'error'),
      )
      if (!list.length) return toast('Нет новых сданных работ для проверки')
      toast(`Проверка запущена, работ: ${list.length}`)
      for (const s of list) await process(s.id)
    },
    [getState, process, toast],
  )

  const genVariants = useCallback(
    (assignmentId: string) =>
      runJob(`gen:${assignmentId}`, 'generate', 'Формирование вариантов', async () => {
        const a = getState().assignments.find((x) => x.id === assignmentId)
        if (!a) return
        if (!a.tasks.length) throw new Error('В исходном материале нет заданий. Сначала проверьте исходный материал.')
        const out = await generateVariants(a, a.variantCount)
        update((st) => ({
          ...st,
          assignments: patch(st.assignments, assignmentId, (x) => ({ ...x, ...out, status: 'draft' })),
        }))
      }),
    [getState, runJob, update],
  )

  return { process, processAll, genVariants, setSub }
}
