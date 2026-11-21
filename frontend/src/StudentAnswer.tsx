import { useState, type CSSProperties } from 'react'
import type { Check } from './api'
import { GradePicker } from './GradePicker'

type Tone = 'ok' | 'bad' | 'review' | 'wait'

const TONE: Record<Tone, { bg: string; border: string; color: string }> = {
  ok: { bg: '#dcfce7', border: '#22c55e', color: '#15803d' },
  bad: { bg: '#fee2e2', border: '#ef4444', color: '#b91c1c' },
  review: { bg: '#fef9c3', border: '#eab308', color: '#a16207' },
  wait: { bg: '#e0e7ff', border: '#cbd5e1', color: '#3730a3' },
}

/** Зелёный — всё верно, красный — есть ошибки, жёлтый — плохо распознано, нужна ручная проверка. */
function toneOf(c: Check | undefined): { tone: Tone; label: string } {
  // Номер проверки неизвестен (работа загружена в другом браузере) — итог узнать нельзя.
  if (!c) return { tone: 'wait', label: 'Результата пока нет' }
  if (!('taskResults' in c)) {
    if (c.state === 'failed' || c.state === 'needs_review') return { tone: 'review', label: 'Нужна ручная проверка' }
    return { tone: 'wait', label: 'Проверяется…' }
  }
  const total = c.taskResults.length
  const correct = c.taskResults.filter((t) => t.answer === 'correct').length
  const label = `${correct} из ${total} верно`
  const review = c.state === 'needs_review' || c.taskResults.some((t) => t.needsReview || t.answer === 'unreadable')
  if (review) return { tone: 'review', label: `${label} · нужна ручная проверка` }
  return { tone: correct === total ? 'ok' : 'bad', label }
}

// Кнопка ответа ученика в папке: имя, число верно выполненных заданий, цвет по итогу и оценка учителя.
export function StudentAnswer(props: {
  name: string
  check?: Check
  grade?: number | null
  onGrade: (checkId: string, grade: number | null) => Promise<void>
  /** Открыть работу; checkId нет, если номер проверки неизвестен. */
  onOpen?: (checkId?: string) => void
}) {
  const { check } = props
  const [busy, setBusy] = useState(false)
  const { tone, label } = toneOf(check)
  const t = TONE[tone]
  const canGrade = !!check && 'taskResults' in check

  const grade = async (g: number | null) => {
    if (!check || busy) return
    setBusy(true)
    try {
      await props.onGrade(check.checkId, g)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="button"
      tabIndex={0}
      style={{ ...st.card, borderLeftColor: t.border, cursor: props.onOpen ? 'pointer' : 'default' }}
      onClick={() => props.onOpen?.(check?.checkId)}
      onKeyDown={(e) => e.key === 'Enter' && props.onOpen?.(check?.checkId)}
    >
      <span style={{ fontWeight: 500, flex: 1 }}>{props.name}</span>
      <span style={{ ...st.label, color: t.color, background: t.bg }}>{label}</span>
      <GradePicker
        grade={props.grade}
        disabled={!canGrade || busy}
        title={canGrade ? 'Оценка учителя — работа с оценкой учитывается в тенденциях' : 'Оценку можно поставить после результата проверки'}
        onChange={grade}
      />
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  card: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '10px 12px 10px 14px',
    marginBottom: 8,
    background: '#fff',
    border: '1px solid #e5e7eb',
    borderLeft: '4px solid',
    borderRadius: 10,
    color: '#1f2937',
  },
  label: { fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap', borderRadius: 999, padding: '3px 10px' },
}
