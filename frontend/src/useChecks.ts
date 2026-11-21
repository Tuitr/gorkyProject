import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Check } from './api'

const inProgress = (c: Check) => !('taskResults' in c) && ['queued', 'running', 'waiting_reference'].includes(c.state)

// Проверки по их checkId — общие для кнопок учеников (слева) и тенденций (справа).
export function useChecks(checkIds: string[]) {
  const [checks, setChecks] = useState<Record<string, Check>>({})
  const checksRef = useRef(checks)
  checksRef.current = checks
  const key = [...new Set(checkIds)].sort().join(',')

  const loadOne = useCallback(async (id: string) => {
    const c = await api.check(id).catch(() => undefined)
    if (c) setChecks((prev) => ({ ...prev, [id]: c }))
    return c
  }, [])

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const ids = key ? key.split(',') : []
    const run = async () => {
      // Готовые проверки не перечитываем, идущие — раз в 5 секунд.
      const todo = ids.filter((id) => !checksRef.current[id] || inProgress(checksRef.current[id]))
      const got = await Promise.all(todo.map(loadOne))
      if (alive && got.some((c) => !c || inProgress(c))) timer = window.setTimeout(run, 5000)
    }
    run()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [key, loadOne])

  return { checks, reload: loadOne }
}
