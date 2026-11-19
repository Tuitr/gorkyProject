import type { Assignment, EquivFeature, PracticeTask, Variant } from '../domain'
import { uid } from '../domain'

/** Генерация через GigaChat (/api/generate в server/gigachat.ts). */

type Raw = Record<string, unknown>
const str = (v: unknown) => (typeof v === 'string' ? v : v == null ? '' : String(v))
const arr = (v: unknown): Raw[] => (Array.isArray(v) ? (v as Raw[]) : [])
const norm = (s: string) => s.replace(/^[^\dа-яa-z]*/i, '').replace(/[).\s]+$/, '').toLowerCase()

async function call(body: unknown): Promise<Raw> {
  const res = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(data?.message ?? `Генерация: ошибка ${res.status}`)
  return (data ?? {}) as Raw
}

export const VARIANT_NAMES = ['А', 'Б', 'В', 'Г', 'Д', 'Е']

/** Ключ к варианту А + варианты Б, В… + признаки равнозначности. */
export async function generateVariants(a: Assignment, count: number) {
  const raw = await call({
    kind: 'variants',
    tasks: a.tasks.map((t) => ({ num: t.num, text: t.text })),
    count,
    notes: a.notes,
  })
  const key = arr(raw.key)
  const variantA: Variant = {
    id: 'vA',
    name: 'А',
    tasks: a.tasks.map((t) => ({
      taskId: t.id,
      text: t.text,
      answer: str(key.find((k) => norm(str(k.num)) === norm(t.num))?.answer),
    })),
  }
  const others: Variant[] = arr(raw.variants)
    .slice(0, count - 1)
    .map((v, i) => {
      const ts = arr(v.tasks)
      return {
        id: `v${VARIANT_NAMES[i + 1]}`,
        name: VARIANT_NAMES[i + 1],
        tasks: a.tasks.map((t) => {
          const g = ts.find((x) => norm(str(x.num)) === norm(t.num))
          return { taskId: t.id, text: str(g?.text) || t.text, answer: str(g?.answer) }
        }),
      }
    })
  const features: Record<string, EquivFeature[]> = {}
  for (const f of arr(raw.features)) {
    const t = a.tasks.find((x) => norm(x.num) === norm(str(f.num)))
    if (!t) continue
    features[t.id] = arr(f.rows).map((r) => ({
      name: str(r.name),
      values: Array.isArray(r.values) ? r.values.map(str) : [],
      soft: r.soft === true,
    }))
  }
  return { variants: [variantA, ...others], features }
}

export async function replaceTask(num: string, original: string, current: string, avoid: string[]) {
  const raw = await call({ kind: 'replace', num, original, current, avoid })
  const text = str(raw.text)
  if (!text) throw new Error('GigaChat не прислал новое условие')
  return { text, answer: str(raw.answer) }
}

export type PracticeTopicIn = { topic: string; examples: string[]; count: number }

export async function generatePractice(topics: PracticeTopicIn[], board: boolean) {
  const raw = await call({ kind: 'practice', topics, board })
  const toTask = (t: Raw, topic: string): PracticeTask => ({
    id: uid('pt'),
    text: str(t.text),
    answer: str(t.answer),
    topic: str(t.topic) || topic,
  })
  const b = raw.board as Raw | undefined
  return {
    board: b && b.task ? { example: str(b.example), task: toTask(b.task as Raw, topics[0]?.topic ?? '') } : undefined,
    topics: arr(raw.topics).map((t) => ({
      topic: str(t.topic),
      tasks: arr(t.tasks).map((x) => toTask(x, str(t.topic))),
    })),
  }
}
