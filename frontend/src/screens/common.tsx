import { type ReactNode } from 'react'
import { type Assignment, type ClassGroup } from '../domain'
import { go, href, useRoute } from '../router'
import { useStore } from '../store'
import { Tabs } from '../ui'

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

/**
 * Шапка раздела работы: «← работа» (страница работы с плитками), заголовок «Раздел · Работа», кнопки справа.
 * Шагов «Подготовка / Проверка / Итоги» и подвкладок нет — переход между разделами идёт через страницу работы.
 */
export function AssignmentHeader({
  a,
  step,
  actions,
  primary,
}: {
  a: Assignment
  step: Step
  classId?: string
  /** дополнительные кнопки раздела */
  actions?: ReactNode
  /** главное действие раздела */
  primary?: ReactNode
}) {
  const label = STEPS.find((s) => s.id === step)?.label
  return (
    <div className="a-head a-head--focus">
      <a className="back-link" href={href('a', a.id)}>
        ← {a.title}
      </a>
      <div className="a-head__row">
        <h1>
          {label} <span className="a-head__of">· {a.title}</span>
        </h1>
        {(primary || actions) && (
          <div className="page-head__actions">
            {primary}
            {actions}
          </div>
        )}
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
