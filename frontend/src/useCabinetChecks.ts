import { useEffect } from 'react'
import { isCabinet } from './api/cabinet'
import { refreshCheck } from './api/sync'
import { useStore } from './store'

/**
 * Режим кабинета: пока страница открыта, спрашиваем кабинет о проверке перечисленных сдач
 * (сразу и затем раз в 30 секунд) и применяем ответ — состояние, готовый результат, «нужен учитель».
 */
export function useCabinetChecks(submissionIds: string[]) {
  const { getState, update } = useStore()
  const key = submissionIds.join(',')
  useEffect(() => {
    if (!isCabinet() || !key) return
    let alive = true
    const run = async () => {
      for (const id of key.split(',')) {
        const fn = await refreshCheck(getState(), id)
        if (!alive) return
        if (fn) update(fn)
      }
    }
    void run()
    const t = setInterval(() => void run(), 30_000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [key, getState, update])
}
