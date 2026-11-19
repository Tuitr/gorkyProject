import type { Box, CheckResult, Mistake, TaskCheck } from '../types'
import type { OcrLine } from './ocr'

/**
 * Проверка работы: строки OCR уходят на /api/check (server/gigachat.ts), там LaTeX → обычный текст → GigaChat;
 * модель называет номера строк с ошибками — по ним строим рамки на фото.
 */

type RawTask = { label?: unknown; ok?: unknown; lines?: unknown }
type RawMistake = {
  task?: unknown
  lines?: unknown
  title?: unknown
  where?: unknown
  studentAnswer?: unknown
  correctSolution?: unknown
  explanation?: unknown
}
type RawTopic = { name?: unknown; reason?: unknown }
type RawResult = {
  subject?: unknown
  workTopic?: unknown
  tasks?: RawTask[]
  mistakes?: RawMistake[]
  summary?: unknown
  weakTopics?: RawTopic[]
}

const str = (v: unknown) => (typeof v === 'string' ? v : v == null ? '' : String(v))
const nums = (v: unknown) => (Array.isArray(v) ? v.map(Number).filter((n) => Number.isInteger(n)) : [])
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])
/** Строки задания идут подряд: модель шлёт [первая, последняя] (или весь список) — берём всё от min до max. */
const span = (v: unknown) => {
  const n = nums(v)
  if (!n.length) return []
  const lo = Math.min(...n)
  return Array.from({ length: Math.min(Math.max(...n) - lo + 1, 400) }, (_, i) => lo + i)
}

/** Общая рамка вокруг нужных строк (с небольшим запасом); null — если строк нет. */
function unionBox(ids: number[], lines: OcrLine[]): Box | null {
  const boxes = ids.map((i) => lines[i]?.box).filter((b): b is Box => !!b && b.w > 0)
  if (!boxes.length) return null
  const pad = 0.008
  const x0 = Math.max(0, Math.min(...boxes.map((b) => b.x)) - pad)
  const y0 = Math.max(0, Math.min(...boxes.map((b) => b.y)) - pad)
  const x1 = Math.min(1, Math.max(...boxes.map((b) => b.x + b.w)) + pad)
  const y1 = Math.min(1, Math.max(...boxes.map((b) => b.y + b.h)) + pad)
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

export async function checkWork(lines: OcrLine[], task?: string): Promise<CheckResult> {
  const res = await fetch('/api/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines: lines.map((l) => l.text), task }),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(data?.message ?? `Проверка: ошибка ${res.status}`)
  const raw = (data ?? {}) as RawResult

  const tasks: TaskCheck[] = arr<RawTask>(raw.tasks).map((t, i) => ({
    id: `t${i}`,
    label: str(t.label) || `№${i + 1}`,
    ok: t.ok === true,
    box: unionBox(span(t.lines), lines) ?? { x: 0, y: 0, w: 0, h: 0 },
  }))

  const mistakes: Mistake[] = arr<RawMistake>(raw.mistakes).map((m, i) => {
    const label = str(m.task)
    let task = tasks.find((t) => t.label === label)
    if (!task) {
      task = { id: `t${tasks.length}`, label: label || `Ошибка ${i + 1}`, ok: false, box: { x: 0, y: 0, w: 0, h: 0 } }
      tasks.push(task)
    }
    task.ok = false
    return {
      id: `m${i}`,
      taskId: task.id,
      // нет строк — рамку не рисуем (нулевой размер), ошибка остаётся в списке
      box: unionBox(nums(m.lines), lines) ?? (task.box.w > 0 ? task.box : { x: 0, y: 0, w: 0, h: 0 }),
      title: str(m.title) || 'Ошибка',
      where: str(m.where) || undefined,
      studentAnswer: str(m.studentAnswer) || '—',
      correctSolution: str(m.correctSolution) || '—',
      explanation: str(m.explanation),
    }
  })

  return {
    subject: str(raw.subject) || 'Предмет не определён',
    workTopic: str(raw.workTopic) || 'Работа',
    student: 'Проверено GigaChat',
    tasks,
    mistakes,
    summary: str(raw.summary),
    weakTopics: arr<RawTopic>(raw.weakTopics)
      .map((t) => ({ name: str(t.name), reason: str(t.reason) }))
      .filter((t) => t.name),
  }
}
