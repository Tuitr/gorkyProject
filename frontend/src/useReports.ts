import { useEffect, useState } from 'react'
import { api, type Assignment, type Report } from './api'

// Отчёты кабинета по всем заданиям класса — общие для папок работ (слева) и статистики (справа).
export function useReports(assignments: Assignment[], refreshKey: number) {
  const [reports, setReports] = useState<Record<string, Report>>({})
  const [error, setError] = useState<string>()
  const [loading, setLoading] = useState(false)
  const ids = assignments.map((a) => a.assignmentId + ':' + a.assignmentRev).join(',')

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const load = async () => {
      setLoading(true)
      try {
        const list = await Promise.all(assignments.map((a) => api.report(a)))
        if (!alive) return
        setReports(Object.fromEntries(list.map((r) => [r.assignmentId, r])))
        setError(undefined)
        // Пока работы проверяются, перечитываем сами.
        if (list.some((r) => r.waiting.length > 0)) timer = window.setTimeout(load, 10000)
      } catch (e) {
        if (alive) setError((e as Error).message)
      } finally {
        if (alive) setLoading(false)
      }
    }
    setReports({})
    if (assignments.length) load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, refreshKey])

  return { reports, error, loading }
}
