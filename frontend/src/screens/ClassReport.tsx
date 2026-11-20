import { useState } from 'react'
import { analyzeClass } from '../analytics'
import { CATEGORY_LABEL, submissionTotal } from '../domain'
import { href } from '../router'
import { useStore } from '../store'
import { Badge, plural } from '../ui'
import { AssignmentHeader, ClassTabs, NotFound, useAssignment } from './common'

/** Экран 11. Отчёт по классу: типовые ошибки и «Темы для повторения» от самой проблемной. */
export function ClassReport({ id, classId }: { id: string; classId?: string }) {
  const { state } = useStore()
  const { a, classes } = useAssignment(id)
  const [open, setOpen] = useState<string | null>(null)
  const cls = classes.find((c) => c.id === classId) ?? classes[0]
  if (!a) return <NotFound />
  if (!cls) return <NotFound what="Класс" />

  const subs = state.submissions.filter((s) => s.assignmentId === a.id && s.classId === cls.id)
  const { processed, typical, topics } = analyzeClass(a, subs)
  const name = (sid: string | null) => cls.students.find((s) => s.id === sid)?.name ?? 'не определён'
  const num = (tid: string) => a.tasks.find((t) => t.id === tid)?.num
  const totals = subs.filter((s) => s.status === 'processed').map((s) => submissionTotal(a, s))
  const avg = totals.length ? totals.reduce((m, t) => m + t.sum, 0) / totals.length : 0
  const max = a.tasks.reduce((m, t) => m + t.max, 0)

  return (
    <div className="page page--wide">
      <AssignmentHeader
        a={a}
        step="class-report"
        classId={cls.id}
        actions={
          <a className="btn btn--primary" href={href('a', a.id, 'practice', { c: cls.id })}>
            Работа над ошибками →
          </a>
        }
      />
      <ClassTabs classes={classes} value={cls.id} a={a} step="class-report" />

      <div className="stats">
        <div className="stat">
          <b>
            {processed}/{cls.students.length}
          </b>
          <span>проверено</span>
        </div>
        <div className="stat">
          <b>
            {avg.toFixed(1)}/{max}
          </b>
          <span>средний балл{totals.some((t) => t.preliminary) ? ' (предв.)' : ''}</span>
        </div>
        <div className="stat stat--warn">
          <b>{topics.length}</b>
          <span>{plural(topics.length, 'задание с типичными ошибками', 'задания с типичными ошибками', 'заданий с типичными ошибками')}</span>
        </div>
      </div>

      {!processed ? (
        <div className="empty">
          <h2>Проверенных работ нет</h2>
          <a className="btn" href={href('a', a.id, 'collect', { c: cls.id })}>
            К сбору работ
          </a>
        </div>
      ) : (
        <div className="two-col">
          <section className="panel">
            <h2>Типовые ошибки</h2>
            {!typical.length ? (
              <p className="muted">Ошибок не обнаружено</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Ошибка</th>
                    <th>Учеников</th>
                    <th>Задания</th>
                  </tr>
                </thead>
                <tbody>
                  {typical.map((g) => (
                    <tr key={g.category}>
                      <td>
                        <Badge tone="err">{CATEGORY_LABEL[g.category]}</Badge>
                        <div className="muted small">{g.examples[0]?.mistake.title}</div>
                      </td>
                      <td>
                        <b>{g.students.size}</b>
                      </td>
                      <td>{[...g.taskIds].map((t) => `№${num(t)}`).join(', ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel">
            <h2>Темы для повторения</h2>
            <ol className="topics-list">
              {topics.map((t) => (
                <li key={t.taskId}>
                  <div className="topic__head">
                    <b>{t.name}</b>
                    <span className="share">
                      <span className="bar">
                        <span style={{ width: `${Math.round(t.share * 100)}%` }} />
                      </span>
                      {Math.round(t.share * 100)}% · {t.failed.length} из {t.total}
                    </span>
                  </div>
                  <div className="muted small">
                    Ученики: {t.failed.map((sid) => name(state.submissions.find((s) => s.id === sid)?.studentId ?? null)).join(', ')}
                  </div>
                  {t.categories.length > 0 && (
                    <div className="chips">
                      {t.categories.map((c) => (
                        <Badge key={c} tone="err">
                          {c}
                        </Badge>
                      ))}
                    </div>
                  )}
                  <button className="link-btn" onClick={() => setOpen(open === t.taskId ? null : t.taskId)}>
                    {open === t.taskId ? 'Скрыть примеры' : `Показать примеры (${t.examples.length})`}
                  </button>
                  {open === t.taskId && (
                    <ul className="examples">
                      {t.examples.map((e) => (
                        <li key={e.mistake.id}>
                          <a href={href('a', a.id, 'report', e.submissionId)}>{name(e.studentId)}</a>: {e.mistake.title}
                          <div className="solution solution--bad">
                            <code>{e.mistake.studentAnswer}</code>
                          </div>
                          <div className="solution solution--good">
                            <p>{e.mistake.correctSolution}</p>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          </section>
        </div>
      )}
    </div>
  )
}
