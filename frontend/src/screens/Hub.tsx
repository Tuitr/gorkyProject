import { WORK_TYPE_LABEL } from '../domain'
import { href } from '../router'
import { useStore } from '../store'
import { Badge } from '../ui'
import { NotFound, useAssignment } from './common'

/** Страница работы: что можно сделать — крупными плитками, каждая открывает только свой раздел. */
export function Hub({ id }: { id: string }) {
  const { state } = useStore()
  const { a, classes } = useAssignment(id)
  if (!a) return <NotFound />

  const subs = state.submissions.filter((s) => s.assignmentId === a.id)
  const handed = subs.filter((s) => s.status !== 'none' && s.pages.length).length
  const students = classes.reduce((n, c) => n + c.students.length, 0)
  const processed = subs.filter((s) => s.status === 'processed').length
  const queue = state.review.filter((r) => !r.resolved && subs.some((s) => s.id === r.submissionId)).length
  const f = { f: '1' }

  const tiles = [
    {
      to: href('a', a.id, 'collect', f),
      icon: '📷',
      title: 'Добавить ответы учеников',
      note: `Фото работ: сдано ${handed} из ${students}`,
      main: true,
    },
    {
      to: href('a', a.id, 'source', { ...f, add: '1' }),
      icon: '＋',
      title: 'Добавить номер',
      note: `Сейчас номеров: ${a.tasks.length}`,
    },
    {
      to: href('a', a.id, 'variants', f),
      icon: '🗂',
      title: 'Варианты',
      note: a.variants.length ? `Вариантов: ${a.variants.length} · ключи и печать` : 'Ещё не созданы',
    },
    {
      to: href('a', a.id, 'sheet', f),
      icon: '📋',
      title: 'Ведомость',
      note: `Проверено: ${processed}${queue ? ` · требует внимания: ${queue}` : ''}`,
    },
    {
      to: href('a', a.id, 'class-report', f),
      icon: '📊',
      title: 'Отчёт',
      note: 'Типовые ошибки и темы для повторения',
    },
  ]

  return (
    <div className="page">
      <div className="a-head a-head--focus">
        <a className="back-link" href="#/">
          ← Работы
        </a>
        <h1>
          {a.title}{' '}
          {a.status === 'draft' ? <Badge tone="info">Черновик</Badge> : <Badge tone="ok">Утверждена</Badge>}
        </h1>
        <div className="muted">
          {WORK_TYPE_LABEL[a.type]} · {a.tasks.length} номеров · {classes.map((c) => c.name).join(', ') || 'классы не выбраны'}
        </div>
      </div>

      <nav className="hub" aria-label="Действия с работой">
        {tiles.map((t) => (
          <a key={t.title} className={`hub__tile${t.main ? ' hub__tile--main' : ''}`} href={t.to}>
            <span className="hub__icon" aria-hidden="true">
              {t.icon}
            </span>
            <b>{t.title}</b>
            <span className="hub__note">{t.note}</span>
          </a>
        ))}
      </nav>

      <div className="hub__more">
        <a href={href('a', a.id, a.status === 'draft' ? 'source' : 'collect')}>Все этапы работы</a>
        <a href={href('a', a.id, 'edit')}>Параметры</a>
      </div>
    </div>
  )
}
