import { useEffect, useState } from 'react'

/** Точный ответ солвера (sympy на нашем сервере, /api/solve) или null — тип задания не распознан. */
const cache = new Map<string, string | null>()

async function solve(texts: string[]): Promise<void> {
  const todo = [...new Set(texts)].filter((t) => t.trim() && !cache.has(t))
  if (!todo.length) return
  const res = await fetch('/api/solve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tasks: todo.map((text, i) => ({ num: String(i), text })) }),
  })
  if (!res.ok) return
  const data = (await res.json().catch(() => null)) as { answers?: { num: string; answer: string }[] } | null
  const byNum = new Map((data?.answers ?? []).map((a) => [a.num, a.answer]))
  todo.forEach((t, i) => cache.set(t, byNum.get(String(i)) ?? null))
}

/** Ответы солвера для списка условий (по тексту). Пересчёт — через 600 мс после последней правки. */
export function useSolvedAnswers(texts: string[]): Map<string, string> {
  const key = texts.join('\u0000')
  const [, setTick] = useState(0)
  useEffect(() => {
    const list = key ? key.split('\u0000') : []
    if (list.every((t) => !t.trim() || cache.has(t))) return
    let alive = true
    const timer = setTimeout(() => {
      solve(list)
        .catch(() => undefined)
        .then(() => alive && setTick((n) => n + 1))
    }, 600)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [key])
  const out = new Map<string, string>()
  for (const t of texts) {
    const v = cache.get(t)
    if (v) out.set(t, v)
  }
  return out
}
