import { useMemo, useState } from 'react'
import { useActions } from '../actions'
import { CabinetUpload } from '../components/CabinetUpload'
import { CHECK_STATE_LABEL, type CheckState } from '../api/cabinet'
import { useCabinetChecks } from '../useCabinetChecks'
import { SUBMISSION_STATUS_LABEL, type SubmissionStatus } from '../domain'
import { href } from '../router'
import { useStore } from '../store'
import { Badge, type Tone } from '../ui'
import { AssignmentHeader, ClassTabs, NotFound, useAssignment } from './common'

export const STATUS_TONE: Record<SubmissionStatus, Tone> = {
  none: 'muted',
  submitted: 'info',
  processing: 'info',
  processed: 'ok',
  error: 'err',
}

/** Экран 5. Сбор работ: DOCX или ZIP учеников уходят в кабинет, он проверяет их и присылает результат. */
export function Collect({ id, classId }: { id: string; classId?: string }) {
  const { state } = useStore()
  const { pull } = useActions()
  const { a, classes } = useAssignment(id)
  const [q, setQ] = useState('')
  const [cabinetOpen, setCabinetOpen] = useState(false)
  const cls = classes.find((c) => c.id === classId) ?? classes[0]
  // работы класса, загруженные через кабинет: состояние их проверки там
  useCabinetChecks(
    state.submissions
      .filter((x) => x.assignmentId === id && x.classId === cls?.id && x.remote?.checkId)
      .map((x) => x.id),
  )

  const subs = useMemo(
    () => state.submissions.filter((s) => s.assignmentId === id && s.classId === cls?.id),
    [state.submissions, id, cls?.id],
  )
  if (!a) return <NotFound />
  if (!cls)
    return (
      <div className="page">
        <AssignmentHeader a={a} step="collect" />
        <div className="empty">
          <h2>Работа не назначена ни одному классу</h2>
          <a className="btn" href={href('a', a.id, 'edit')}>
            Выбрать классы
          </a>
        </div>
      </div>
    )

  const openReview = state.review.filter((r) => !r.resolved && subs.some((s) => s.id === r.submissionId))
  const attention = new Set([
    ...subs.filter((s) => s.status === 'error' || !s.studentId).map((s) => s.id),
    ...openReview.map((r) => r.submissionId),
  ])
  const handed = subs.filter((s) => s.status !== 'none' && s.remote).length
  const processed = subs.filter((s) => s.status === 'processed').length

  const rows = [
    ...cls.students.map((st) => ({ st, s: subs.find((x) => x.studentId === st.id) })),
    ...subs.filter((s) => !s.studentId).map((s) => ({ st: null, s })),
  ].filter(({ st }) => !q.trim() || (st?.name ?? 'не определён').toLowerCase().includes(q.trim().toLowerCase()))

  return (
    <div className="page page--wide">
      <AssignmentHeader a={a} step="collect" classId={cls.id} />
      <ClassTabs classes={classes} value={cls.id} a={a} step="collect" />

      {a.status === 'draft' && (
        <div className="notice notice--warn">
          Работа имеет статус «Черновик». Сбор работ доступен, однако рекомендуется сначала{' '}
          <a href={href('a', a.id, 'variants')}>проверить эталонные ответы и утвердить работу</a>.
        </div>
      )}

      <div className="stats">
        <div className="stat">
          <b>
            {handed}/{cls.students.length}
          </b>
          <span>сдано</span>
        </div>
        <div className="stat stat--ok">
          <b>{processed}</b>
          <span>проверено</span>
        </div>
        <a className={`stat${attention.size ? ' stat--warn' : ''}`} href={href('a', a.id, 'queue')}>
          <b>{attention.size}</b>
          <span>требует проверки учителем</span>
        </a>
        <div className="stats__actions">
          <button className="btn btn--primary" onClick={() => setCabinetOpen(true)}>
            Загрузить работы (DOCX или ZIP)
          </button>
        </div>
      </div>

      <input
        className="input search"
        type="search"
        placeholder="Поиск по имени"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />

      <table className="table">
        <thead>
          <tr>
            <th>Ученик</th>
            <th>Файл</th>
            <th>Статус</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map(({ st, s }) => {
            const status: SubmissionStatus = s?.remote ? s.status : 'none'
            const checkState = s?.remote?.checkState
            const link = s && !st ? href('a', a.id, 'queue') : href('a', a.id, 's', cls.id, st!.id)
            return (
              <tr key={st?.id ?? s!.id} className={s && attention.has(s.id) ? 'is-attention' : status === 'none' ? 'is-none' : ''}>
                <td>
                  <a href={link}>{st?.name ?? <i>Ученик не определён</i>}</a>
                </td>
                <td>{s?.remote?.fileName ?? <span className="muted">—</span>}</td>
                <td>
                  <Badge tone={STATUS_TONE[status]}>{SUBMISSION_STATUS_LABEL[status]}</Badge>
                  {checkState && checkState !== 'ready' && (
                    <div className="muted small">
                      {checkState === 'unavailable'
                        ? 'Сервис проверки недоступен.'
                        : CHECK_STATE_LABEL[checkState as CheckState] ?? checkState}
                      {s?.remote?.checkReason && <> · {s.remote.checkReason}</>}
                    </div>
                  )}
                  {s && attention.has(s.id) && status !== 'error' && (
                    <>
                      {' '}
                      <Badge tone="warn">требует проверки учителем</Badge>
                    </>
                  )}
                  {status === 'error' && s?.error && <div className="small err-text">{s.error}</div>}
                </td>
                <td className="table__actions">
                  {status === 'error' && s ? (
                    <button className="link-btn" onClick={() => void pull(s.id)}>
                      Обновить
                    </button>
                  ) : status === 'processed' && s ? (
                    <a className="link-btn" href={href('a', a.id, 'report', s.id)}>
                      Отчёт
                    </a>
                  ) : status === 'none' ? (
                    <button className="link-btn link-btn--quiet" onClick={() => setCabinetOpen(true)}>
                      Загрузить работу
                    </button>
                  ) : (
                    <a className="link-btn" href={link}>
                      Открыть
                    </a>
                  )}
                </td>
              </tr>
            )
          })}
          {!rows.length && (
            <tr>
              <td colSpan={4} className="muted">
                Ученики не найдены
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {cabinetOpen && <CabinetUpload a={a} cls={cls} onClose={() => setCabinetOpen(false)} />}
    </div>
  )
}
