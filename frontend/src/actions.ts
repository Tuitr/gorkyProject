import { useCallback } from 'react'
import { CabinetError, editReview, isCabinet, publishReview, type ReviewChange } from './api/cabinet'
import { refreshCheck } from './api/sync'
import { generateVariants } from './api/generate'
import { publishAssignment } from './api/publish'
import { processSubmission } from './api/process'
import type { Assignment, State, Submission } from './domain'
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
        // результат уже пришёл из кабинета — он главнее нашей проверки
        if (s.remote?.resultRev && s.results.some((r) => r.remote)) return
        setSub(s.id, (x) => ({ ...x, status: 'processing', error: undefined }))
        try {
          const out = await processSubmission(a, s, st.classes.find((c) => c.id === s.classId))
          // пока шла наша проверка, мог прийти результат кабинета — его не перезаписываем
          const now = getState().submissions.find((x) => x.id === s.id)
          if (now?.remote?.resultRev && now.results.some((r) => r.remote)) return
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

  /**
   * Режим кабинета: сохранить работу на сервере (каждому классу; новая ревизия — если содержимое изменилось).
   * Возвращает серверные id сразу — состояние React обновится позже, читать его сразу после нельзя.
   */
  const publish = useCallback(
    async (assignmentId: string): Promise<Assignment['remote']> => {
      if (!isCabinet()) return undefined
      let out: Assignment['remote']
      await runJob(`publish:${assignmentId}`, 'sync', 'Сохранение работы на сервере', async () => {
        const a = getState().assignments.find((x) => x.id === assignmentId)
        if (!a) return
        try {
          const remote = await publishAssignment(a)
          out = remote
          update((st) => ({ ...st, assignments: patch(st.assignments, assignmentId, (x) => ({ ...x, remote })) }))
        } catch (e) {
          toast(`Работа не сохранена на сервере: ${e instanceof Error ? e.message : 'ошибка'}`, 'err')
          throw e
        }
      })
      return out ?? getState().assignments.find((x) => x.id === assignmentId)?.remote
    },
    [getState, runJob, toast, update],
  )

  /** Обновить сдачу из кабинета (после правки или конфликта версий). */
  const pull = useCallback(
    async (submissionId: string) => {
      const fn = await refreshCheck(getState(), submissionId)
      if (fn) update(fn)
    },
    [getState, update],
  )

  /**
   * Правки учителя в результате кабинета (PATCH /v1/checks/{id}/review поверх текущей версии).
   * Если результат успели изменить (409) — подтягиваем актуальную версию и просим повторить.
   */
  const editRemote = useCallback(
    async (submissionId: string, changes: ReviewChange[], done = 'Изменение сохранено на сервере') => {
      const s = getState().submissions.find((x) => x.id === submissionId)
      const checkId = s?.remote?.checkId
      const baseRev = s?.remote?.resultRev
      if (!checkId || !baseRev) return false
      try {
        await editReview(checkId, baseRev, changes)
        toast(done)
        return true
      } catch (e) {
        if (e instanceof CabinetError && e.code === 'conflict') toast('Результат уже изменён — загружена актуальная версия, повторите правку', 'err')
        else toast(e instanceof Error ? e.message : 'Изменение не сохранено', 'err')
        return false
      } finally {
        await pull(submissionId)
      }
    },
    [getState, pull, toast],
  )

  /** Опубликовать текущую версию результата кабинета (после этого доступен отзыв для ученика). */
  const publishRemote = useCallback(
    async (submissionId: string) => {
      const s = getState().submissions.find((x) => x.id === submissionId)
      const checkId = s?.remote?.checkId
      const rev = s?.remote?.resultRev
      if (!checkId || !rev) return
      try {
        await publishReview(checkId, rev)
        toast('Результат опубликован')
      } catch (e) {
        toast(e instanceof Error ? e.message : 'Не удалось опубликовать', 'err')
      } finally {
        await pull(submissionId)
      }
    },
    [getState, pull, toast],
  )

  return { process, processAll, genVariants, setSub, publish, pull, editRemote, publishRemote }
}
