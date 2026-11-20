import { Fragment, type ReactNode } from 'react'
import { WORK_TYPE_LABEL, type Assignment, type ClassGroup } from '../domain'
import { go, href, useRoute } from '../router'
import { useStore } from '../store'
import { Badge, Tabs } from '../ui'

export const STEPS = [
  { id: 'source', label: 'Исходный материал' },
  { id: 'variants', label: 'Варианты и печать' },
  { id: 'collect', label: 'Сбор работ' },
  { id: 'queue', label: 'Спорные места' },
  { id: 'sheet', label: 'Ведомость' },
  { id: 'class-report', label: 'Отчёт по классу' },
  { id: 'practice', label: 'Работа над ошибками' },
] as const
export type Step = (typeof STEPS)[number]['id']

/** Семь экранов сгруппированы в три шага — учитель видит путь, а не семь кнопок. */
const STAGES: { label: string; steps: Step[] }[] = [
  { label: 'Подготовка', steps: ['source', 'variants'] },
  { label: 'Проверка', steps: ['collect', 'queue', 'sheet'] },
  { label: 'Итоги', steps: ['class-report', 'practice'] },
]

/** Только свои классы: фото и отчёты доступны лишь владельцу класса. */
export function useOwnClasses() {
  const { state } = useStore()
  return state.classes.filter((c) => c.ownerId === state.teacher.id).sort(byClassName)
}

/** От младших к старшим: номер параллели — как число (5А < 10А), затем буква. Без номера — в конце. */
export function byClassName(a: { name: string }, b: { name: string }) {
  const na = Number(a.name.match(/^\d+/)?.[0] ?? Infinity)
  const nb = Number(b.name.match(/^\d+/)?.[0] ?? Infinity)
  return na - nb || a.name.localeCompare(b.name, 'ru')
}

export function useAssignment(id: string) {
  const { state } = useStore()
  const a = state.assignments.find((x) => x.id === id)
  const own = useOwnClasses()
  const classes = a ? own.filter((c) => a.classIds.includes(c.id)) : []
  return { a, classes }
}

export function NotFound({ what = 'Работа' }: { what?: string }) {
  return (
    <div className="empty">
      <h2>{what} не найдена</h2>
      <a className="btn" href="#/">
        К списку работ
      </a>
    </div>
  )
}

/** Шапка работы + вкладки этапов. */
export function AssignmentHeader({
  a,
  step,
  classId,
  actions,
}: {
  a: Assignment
  step: Step
  classId?: string
  actions?: ReactNode
}) {
  const { state } = useStore()
  const queue = state.review.filter(
    (r) => !r.resolved && state.submissions.find((s) => s.id === r.submissionId)?.assignmentId === a.id,
  ).length
  const current = STAGES.find((g) => g.steps.includes(step)) ?? STAGES[0]
  const { query } = useRoute()

  // Открыто с карточки работы («Варианты», «Ведомость», «Отчёт») — только этот раздел, без шагов и переходов
  if (query.get('f')) {
    const label = STEPS.find((s) => s.id === step)?.label
    return (
      <div className="a-head a-head--focus">
        <a className="back-link" href={href('a', a.id)}>
          ← {a.title}
        </a>
        <h1>
          {label} <span className="a-head__of">· {a.title}</span>
        </h1>
      </div>
    )
  }

  return (
    <div className="a-head">
      <div className="page-head">
        <div>
          <div className="muted small">
            <a href="#/">Работы</a> / {WORK_TYPE_LABEL[a.type]}
          </div>
          <h1>
            {a.title}{' '}
            {a.status === 'draft' ? <Badge tone="info">Черновик</Badge> : <Badge tone="ok">Утверждена</Badge>}
          </h1>
        </div>
        <div className="page-head__actions">
          {actions}
          <a className="btn" href={href('a', a.id, 'edit')}>
            Параметры
          </a>
        </div>
      </div>
      <div className="stages">
        <nav className="stages__main" aria-label="Шаги работы">
          {STAGES.map((g, i) => {
            const active = g.steps.includes(step)
            const n = g.steps.includes('queue') ? queue : 0
            return (
              <Fragment key={g.label}>
                {i > 0 && <span className="stages__sep" aria-hidden="true" />}
                <a
                  href={href('a', a.id, g.steps[0], { c: classId })}
                  className={active ? 'is-active' : ''}
                  aria-current={active ? 'step' : undefined}
                >
                  <span className="stages__num">{i + 1}</span>
                  {g.label}
                  {!active && n > 0 && <span className="count">{n}</span>}
                </a>
              </Fragment>
            )
          })}
        </nav>
        <nav className="steps-nav" aria-label="Разделы шага">
          {STEPS.filter((s) => current.steps.includes(s.id)).map((s) => (
            <a
              key={s.id}
              href={href('a', a.id, s.id, { c: classId })}
              className={s.id === step ? 'is-active' : ''}
              aria-current={s.id === step ? 'page' : undefined}
            >
              {s.label}
              {s.id === 'queue' && queue > 0 && <span className="count">{queue}</span>}
            </a>
          ))}
        </nav>
      </div>
    </div>
  )
}

/** Вкладки классов (8А / 8Б / 8В) — класс хранится в адресе ?c=. */
export function ClassTabs({
  classes,
  value,
  a,
  step,
}: {
  classes: ClassGroup[]
  value: string
  a: Assignment
  step: Step
}) {
  const { query } = useRoute()
  if (classes.length < 2) return null
  const f = query.get('f') ?? undefined
  return (
    <Tabs
      label="Класс"
      items={classes.map((c) => ({ id: c.id, label: c.name }))}
      value={value}
      onChange={(c) => go('a', a.id, step, { c, f })}
    />
  )
}

export const studentName = (classes: ClassGroup[], id: string | null) =>
  (id && classes.flatMap((c) => c.students).find((s) => s.id === id)?.name) || 'Ученик не определён'
