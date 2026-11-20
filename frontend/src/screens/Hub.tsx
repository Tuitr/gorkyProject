import { WORK_TYPE_LABEL } from '../domain'
import { askConfirm } from '../lib/confirm'
import { deleteAssignment } from '../api/cabinet'
import { go, href } from '../router'
import { useStore } from '../store'
import { Badge } from '../ui'
import { NotFound, useAssignment } from './common'

/** Страница работы: что можно сделать — крупными плитками, каждая открывает только свой раздел. */
export function Hub({ id }: { id: string }) {
  const { state, update, toast } = useStore()
  const { a, classes } = useAssignment(id)
  if (!a) return <NotFound />

  const subs = state.submissions.filter((s) => s.assignmentId === a.id)
  const handed = subs.filter((s) => s.status !== 'none' && s.remote).length
  const students = classes.reduce((n, c) => n + c.students.length, 0)
  const processed = subs.filter((s) => s.status === 'processed').length
  const queue = state.review.filter((r) => !r.resolved && subs.some((s) => s.id === r.submissionId)).length
  const f = { f: '1' }

  const tiles = [
    {
      to: href('a', a.id, 'source', { ...f, add: '1' }),
      icon: '＋',
      title: 'Добавить задание',
      note: `Заданий: ${a.tasks.length}`,
    },
    {
      to: href('a', a.id, 'variants', f),
      icon: '🗂',
      title: 'Эталонные ответы',
      note: a.status === 'approved' ? 'Утверждена · ответы из реестра решателей' : 'Проверить ответы и утвердить',
    },
    {
      to: href('a', a.id, 'collect', f),
      icon: '📄',
      title: 'Загрузить работы учеников',
      note: `DOCX или ZIP: сдано ${handed} из ${students}`,
      main: true,
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
      title: 'Отчёт по классу',
      note: '',
    },
  ]

  const remove = async () => {
    const extra = handed ? `
Будут удалены также сданные работы учеников: ${handed}.` : ''
    if (!(await askConfirm(`Удалить работу «${a.title}»? Действие нельзя отменить.${extra}`, { ok: 'Удалить', danger: true })))
      return
    try {
      // работа удаляется и в кабинете (у каждого класса — своя), вместе со сдачами
      for (const r of Object.values(a.remote ?? {})) await deleteAssignment(r.assignmentId)
    } catch (e) {
      return toast(e instanceof Error ? e.message : 'Не удалось удалить работу на сервере', 'err')
    }
    const subIds = new Set(subs.map((s) => s.id))
    update((s) => ({
      ...s,
      assignments: s.assignments.filter((x) => x.id !== a.id),
      submissions: s.submissions.filter((x) => x.assignmentId !== a.id),
      review: s.review.filter((r) => !subIds.has(r.submissionId)),
      practices: s.practices.filter((p) => p.assignmentId !== a.id),
    }))
    toast('Работа удалена')
    go()
  }

  return (
    <div className="page">
      <div className="a-head a-head--focus">
        <div className="a-head__top">
          <a className="back-link" href="#/">
            ← Работы
          </a>
          <button className="btn btn--danger" onClick={() => void remove()}>
            Удалить работу
          </button>
        </div>
        <h1>
          {a.title}{' '}
          {a.status === 'draft' ? <Badge tone="info">Черновик</Badge> : <Badge tone="ok">Утверждена</Badge>}
        </h1>
        <div className="muted">
          {WORK_TYPE_LABEL[a.type]} · заданий: {a.tasks.length} · {classes.map((c) => c.name).join(', ') || 'классы не выбраны'}
        </div>
      </div>

      <nav className="hub" aria-label="Действия с работой">
        {tiles.map((t) => (
          <a key={t.title} className={`hub__tile${t.main ? ' hub__tile--main' : ''}`} href={t.to}>
            <span className="hub__icon" aria-hidden="true">
              {t.icon}
            </span>
            <b>{t.title}</b>
            {t.note && <span className="hub__note">{t.note}</span>}
          </a>
        ))}
      </nav>
    </div>
  )
}
