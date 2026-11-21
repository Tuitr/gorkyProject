import { useEffect, useState } from 'react'
import { api, type Assignment, type References } from './api'

// Предупреждение в папке, если сервис проверки не смог разобрать задания варианта:
// тогда ответы учеников по нему не проверятся.
export function VariantWarning({ a }: { a: Assignment }) {
  const [state, setState] = useState<References['state']>()
  const [empty, setEmpty] = useState(false)

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const load = async () => {
      const r = await api.references(a).catch(() => undefined)
      if (!alive || !r) return
      setState(r.state)
      setEmpty(r.tasks.length === 0)
      if (['queued', 'running', 'waiting_reference'].includes(r.state)) timer = window.setTimeout(load, 5000)
    }
    load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [a])

  if (!(state === 'failed' || (state === 'needs_review' && empty))) return null
  return (
    <div style={{ margin: '6px 0 0 30px', padding: '8px 10px', borderRadius: 6, background: '#fef2f2', color: '#b91c1c', fontSize: 14 }}>
      ⚠ Задания варианта не распознаны — ответы по нему не проверятся. Пронумеруйте задания текстом («1.», «2.» или
      «Задание 1)»), а не автоматическим списком Word, и загрузите вариант заново.
    </div>
  )
}
