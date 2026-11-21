import { useState, type CSSProperties } from 'react'
import type { Check } from './api'
import { isApproved } from './useChecks'

type Tone = 'ok' | 'bad' | 'review' | 'wait'

const TONE: Record<Tone, { bg: string; border: string; color: string }> = {
  ok: { bg: '#f0fdf4', border: '#22c55e', color: '#15803d' },
  bad: { bg: '#fef2f2', border: '#ef4444', color: '#b91c1c' },
  review: { bg: '#fefce8', border: '#eab308', color: '#a16207' },
  wait: { bg: '#fff', border: '#e5e7eb', color: '#2563eb' },
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

// Кнопка ответа ученика в папке: галочка учителя, имя, число верно выполненных заданий и цвет по итогу.
export function StudentAnswer(props: {
  name: string
  check?: Check
  onApprove: (checkId: string, resultRev: number) => Promise<void>
  /** Открыть работу; checkId нет, если номер проверки неизвестен. */
  onOpen?: (checkId?: string) => void
}) {
  const { check } = props
  const [busy, setBusy] = useState(false)
  const { tone, label } = toneOf(check)
  const t = TONE[tone]
  const approved = isApproved(check)
  const canApprove = !!check && 'taskResults' in check && !approved

  const approve = async () => {
    if (!check || !('taskResults' in check) || busy) return
    setBusy(true)
    try {
      await props.onApprove(check.checkId, check.resultRev)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="button"
      tabIndex={0}
      style={{ ...st.card, background: t.bg, borderColor: t.border, cursor: props.onOpen ? 'pointer' : 'default' }}
      onClick={() => props.onOpen?.(check?.checkId)}
      onKeyDown={(e) => e.key === 'Enter' && props.onOpen?.(check?.checkId)}
    >
      <label
        style={st.check}
        title={approved ? 'Работа проверена учителем и учитывается в тенденциях' : canApprove ? 'Отметить работу как проверенную' : 'Дождитесь результата проверки'}
        onClick={(e) => e.stopPropagation()}
      >
        <input type="checkbox" checked={approved} disabled={!canApprove || busy} onChange={approve} style={st.box} />
      </label>
      <span style={{ fontWeight: 500, flex: 1 }}>{props.name}</span>
      <span style={{ ...st.label, color: t.color }}>{label}</span>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  card: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '8px 12px',
    marginBottom: 8,
    border: '2px solid',
    borderRadius: 8,
    color: '#1f2937',
  },
  check: { display: 'flex', alignItems: 'center', cursor: 'pointer' },
  box: { width: 18, height: 18, margin: 0, cursor: 'inherit' },
  label: { fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap' },
}
