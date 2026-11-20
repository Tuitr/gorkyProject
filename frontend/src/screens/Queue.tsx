import type { Submission } from '../domain'
import { href } from '../router'
import { useStore } from '../store'
import { PageHead, TextPage } from '../ui'
import { AssignmentHeader, NotFound, useOwnClasses } from './common'

/** Экран 7. Спорные места: карточка на работу ученика (фото, имя, число предполагаемых ошибок и недочётов);
 *  по нажатию — сразу страница проверки этой работы, спорные места разбираются там (QueueCard). */
export function Queue({ assignmentId }: { assignmentId?: string }) {
  const { state } = useStore()
  const classes = useOwnClasses()
  const a = assignmentId ? state.assignments.find((x) => x.id === assignmentId) : undefined
  if (assignmentId && !a) return <NotFound />

  const own = new Set(classes.map((c) => c.id))
  const items = state.review.filter((r) => {
    const s = state.submissions.find((x) => x.id === r.submissionId)
    return s && own.has(s.classId) && (!assignmentId || s.assignmentId === assignmentId)
  })
  const open = items.filter((r) => !r.resolved)

  // по работе ученика — в порядке появления спорных мест
  const works: { s: Submission; count: number }[] = []
  for (const r of open) {
    const w = works.find((x) => x.s.id === r.submissionId)
    if (w) w.count++
    else {
      const s = state.submissions.find((x) => x.id === r.submissionId)
      if (s) works.push({ s, count: 1 })
    }
  }
  const studentName = (s: Submission) =>
    state.classes.find((c) => c.id === s.classId)?.students.find((x) => x.id === s.studentId)?.name ?? 'Ученик не определён'

  return (
    <div className={a ? 'page page--wide' : 'page'}>
      {a ? <AssignmentHeader a={a} step="queue" /> : <PageHead title="Спорные места" />}

      {!works.length ? (
        <div className="empty">
          <h2>Спорных мест нет</h2>
        </div>
      ) : (
        <ul className="qworks">
          {works.map(({ s, count }) => (
            <li key={s.id}>
              <a className="qwork" href={href('a', s.assignmentId, 'report', s.id, { from: 'queue' })}>
                <span className="qwork__photo">
                  {s.pages[0]?.kind === 'text' ? (
                    <TextPage lines={s.pages[0].lines} compact />
                  ) : s.pages[0] ? (
                    <img src={s.pages[0].image} alt="" />
                  ) : (
                    <span className="muted small">Нет фото</span>
                  )}
                </span>
                <b className="qwork__name">{studentName(s)}</b>
                <span className="qwork__count">
                  Предположительных ошибок и недочётов: <b>{count}</b>
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}

    </div>
  )
}
