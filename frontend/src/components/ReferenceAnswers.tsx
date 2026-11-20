import type { AssignmentReferences, TaskReference } from '../api/cabinet'
import { Badge, type Tone } from '../ui'
import type { References } from '../useReferences'

/** Эталонные ответы, с которыми кабинет сверяет работы (их готовит useReferences). */

const STATE: Record<TaskReference['state'], [string, Tone]> = {
  ready: ['эталон готов', 'ok'],
  pending: ['готовится', 'muted'],
  missing: ['эталона нет', 'warn'],
  unsupported: ['решатель не поддерживает', 'warn'],
  validation_failed: ['эталон не прошёл проверку', 'err'],
}

const PREPARATION: Record<AssignmentReferences['state'], string> = {
  queued: 'Вариант ждёт разбора.',
  running: 'Вариант разбирается.',
  waiting_reference: 'Готовятся эталонные ответы.',
  ready: '',
  needs_review: 'Для части заданий эталона нет: их проверяет учитель.',
  failed: 'Не удалось разобрать вариант: задания не найдены. Проверьте нумерацию «1.», «2.» в DOCX.',
}

/** Строка под условием: эталон задания или почему его нет. */
export function ReferenceLine({ refs, num }: { refs: References; num: string }) {
  const t = refs.of(num)
  if (!t) return null
  const [label, tone] = STATE[t.state]
  return (
    <p className="src-task__answer" title="Ответ из реестра решателей: с ним проверка сверяет ответ ученика">
      {t.state === 'ready' && t.answer ? (
        <>
          Эталонный ответ: <b>{t.answer}</b>
        </>
      ) : (
        <Badge tone={tone}>{label}</Badge>
      )}
    </p>
  )
}

/** Состояние подготовки варианта одной строкой (или ничего, когда всё готово). */
export function ReferenceStatus({ refs, saved }: { refs: References; saved: boolean }) {
  if (!saved) return <p className="muted small">Эталонные ответы появятся после сохранения работы на сервере.</p>
  if (refs.error) return <p className="err-text small">Эталонные ответы: {refs.error}</p>
  if (!refs.data) return <p className="muted small">Загрузка эталонных ответов…</p>
  const note = PREPARATION[refs.data.state]
  return note ? <p className="muted small">{note}</p> : null
}
