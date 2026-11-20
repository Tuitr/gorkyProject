import { useCallback } from 'react'
import { CabinetError, editReview, publishReview, type ReviewChange } from './api/cabinet'
import { refreshCheck } from './api/sync'
import { publishAssignment } from './api/publish'
import type { Assignment, Submission } from './domain'
import { patch, useStore } from './store'

/** Идущие сохранения работ на сервере (общие для всех экранов). */
const publishing = new Map<string, Promise<Assignment['remote']>>()

/** Действия с кабинетом: сохранить работу, подтянуть результат проверки, править и опубликовать его. */
export function useActions() {
  const { update, getState, runJob, toast } = useStore()

  const setSub = useCallback(
    (id: string, fn: (s: Submission) => Submission) =>
      update((st) => ({ ...st, submissions: patch(st.submissions, id, fn) })),
    [update],
  )

  /**
   * Сохранить работу на сервере (каждому классу; новая ревизия — если содержимое изменилось).
   * Возвращает серверные id сразу — состояние React обновится позже, читать его сразу после нельзя.
   */
  const doPublish = useCallback(
    async (assignmentId: string): Promise<Assignment['remote']> => {
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

  const publish = useCallback(
    (assignmentId: string): Promise<Assignment['remote']> => {
      // сохранение этой работы уже идёт (например, сразу после «Утвердить») — ждём его, а не возвращаем пусто
      const inflight = publishing.get(assignmentId)
      if (inflight) return inflight
      const p = doPublish(assignmentId).finally(() => publishing.delete(assignmentId))
      publishing.set(assignmentId, p)
      return p
    },
    [doPublish],
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

  return { setSub, publish, pull, editRemote, publishRemote }
}
