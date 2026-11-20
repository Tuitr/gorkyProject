import { useEffect } from 'react'
import { refreshCheck } from './api/sync'
import { useStore } from './store'

/**
 * Пока страница открыта, спрашиваем кабинет о проверке перечисленных сдач
 * (сразу и затем раз в 30 секунд) и применяем ответ — состояние, готовый результат, «нужен учитель».
 */
export function useCabinetChecks(submissionIds: string[]) {
  const { getState, update } = useStore()
  const key = submissionIds.join(',')
  useEffect(() => {
    if (!key) return
    let alive = true
    let busy = false // прошлый круг ещё идёт (много работ, медленный сервер) — новый не начинаем
    const run = async () => {
      if (busy) return
      busy = true
      try {
        for (const id of key.split(',')) {
          const fn = await refreshCheck(getState(), id)
          if (!alive) return
          if (fn) update(fn)
        }
      } finally {
        busy = false
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
