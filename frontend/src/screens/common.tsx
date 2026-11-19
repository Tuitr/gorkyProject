import type { ReactNode } from 'react'
import { WORK_TYPE_LABEL, type Assignment, type ClassGroup } from '../domain'
import { go, href } from '../router'
import { useStore } from '../store'
import { Badge, Tabs } from '../ui'

export const STEPS = [
  { id: 'source', label: 'Исходник' },
  { id: 'variants', label: 'Варианты и печать' },
  { id: 'collect', label: 'Сбор работ' },
  { id: 'queue', label: 'Очередь' },
  { id: 'sheet', label: 'Ведомость' },
  { id: 'class-report', label: 'Отчёт по классу' },
  { id: 'practice', label: 'Практика' },
] as const
export type Step = (typeof STEPS)[number]['id']

/** Только свои классы: фото и отчёты доступны лишь владельцу класса. */
export function useOwnClasses() {
  const { state } = useStore()
  return state.classes.filter((c) => c.ownerId === state.teacher.id)
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
  return (
    <div className="a-head">
      <div className="page-head">
        <div>
          <div className="muted small">
            <a href="#/">Работы</a> / {WORK_TYPE_LABEL[a.type]}
          </div>
          <h1>
            {a.title}{' '}
            {a.status === 'draft' ? <Badge tone="warn">Черновик</Badge> : <Badge tone="ok">Утверждена</Badge>}
          </h1>
        </div>
        <div className="page-head__actions">
          {actions}
          <a className="btn" href={href('a', a.id, 'edit')}>
            Параметры
          </a>
        </div>
      </div>
      <nav className="steps-nav" aria-label="Этапы работы">
        {STEPS.map((s) => (
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
  if (classes.length < 2) return null
  return (
    <Tabs
      label="Класс"
      items={classes.map((c) => ({ id: c.id, label: c.name }))}
      value={value}
      onChange={(c) => go('a', a.id, step, { c })}
    />
  )
}

export const studentName = (classes: ClassGroup[], id: string | null) =>
  (id && classes.flatMap((c) => c.students).find((s) => s.id === id)?.name) || 'Ученик не определён'
