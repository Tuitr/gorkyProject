import { useState } from 'react'
import { askConfirm } from '../lib/confirm'
import { useActions } from '../actions'
import { CHECK_STATE_LABEL, deleteAttempt, type CheckState } from '../api/cabinet'
import { CabinetUpload } from '../components/CabinetUpload'
import { SUBMISSION_STATUS_LABEL } from '../domain'
import { href } from '../router'
import { useStore } from '../store'
import { Badge, TextPage } from '../ui'
import { useCabinetChecks } from '../useCabinetChecks'
import { STATUS_TONE } from './Collect'
import { NotFound, useAssignment } from './common'

/** Экран 6. Карточка ученика: сданный DOCX, состояние проверки в кабинете, текст работы, загрузка и удаление. */
export function StudentCard({ id, classId, studentId }: { id: string; classId?: string; studentId?: string }) {
  const { state, update, toast } = useStore()
  const { pull } = useActions()
  const { a, classes } = useAssignment(id)
  const [uploading, setUploading] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const cls = classes.find((c) => c.id === classId)
  const student = cls?.students.find((s) => s.id === studentId)
  const sub = state.submissions.find((s) => s.assignmentId === id && s.studentId === studentId)
  useCabinetChecks(sub?.remote?.checkId ? [sub.id] : [])
  if (!a || !cls || !student) return <NotFound what={!a ? 'Работа' : 'Ученик'} />

  const idx = cls.students.findIndex((s) => s.id === student.id)
  const next = cls.students[idx + 1]
  const status = sub?.remote ? sub.status : 'none'
  const checkState = sub?.remote?.checkState
  const lines = sub?.pages.flatMap((p) => p.lines ?? []) ?? []

  /** Удаляет сдачу в кабинете (вместе с её проверкой) и у нас. */
  const remove = async () => {
    if (!sub) return
    if (!(await askConfirm(`Удалить работу ученика ${student.name}? Результат проверки тоже будет удалён.`, { ok: 'Удалить', danger: true })))
      return
    setDeleting(true)
    try {
      if (sub.remote?.attemptId) await deleteAttempt(sub.remote.attemptId)
      update((st) => ({
        ...st,
        submissions: st.submissions.filter((x) => x.id !== sub.id),
        review: st.review.filter((r) => r.submissionId !== sub.id),
      }))
      toast('Работа удалена')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось удалить работу', 'err')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="page page--narrow student-card">
      <div className="muted small">
        <a href={href('a', a.id, 'collect', { c: cls.id })}>← {a.title} · {cls.name}</a>
      </div>
      <div className="page-head">
        <div>
          <h1>{student.name}</h1>
          <div className="muted">
            {cls.name} · <Badge tone={STATUS_TONE[status]}>{SUBMISSION_STATUS_LABEL[status]}</Badge>
          </div>
        </div>
        {sub?.status === 'processed' && (
          <a className="btn" href={href('a', a.id, 'report', sub.id)}>
            Отчёт
          </a>
        )}
      </div>

      <section className="panel">
        <div className="panel__head">
          <h2>Работа ученика</h2>
          {sub?.remote && <span className="muted small">{sub.remote.fileName}</span>}
        </div>
        {sub?.remote ? (
          <>
            {checkState && checkState !== 'ready' && (
              <p className="muted small">
                Проверка:{' '}
                {checkState === 'unavailable' ? 'сервис проверки недоступен' : CHECK_STATE_LABEL[checkState as CheckState] ?? checkState}
                {sub.remote.checkReason && <> · {sub.remote.checkReason}</>}
              </p>
            )}
            {lines.length > 0 ? <TextPage lines={lines} /> : <p className="muted small">Текст работы не прочитан.</p>}
          </>
        ) : (
          <p className="muted">Работа не сдана. Загрузите DOCX ученика (или ZIP с работами класса).</p>
        )}
        <div className="row row--start">
          <button className="btn btn--primary" onClick={() => setUploading(true)}>
            {sub?.remote ? 'Загрузить другой файл' : 'Загрузить DOCX'}
          </button>
          {sub?.remote?.checkId && (
            <button className="btn" onClick={() => void pull(sub.id)}>
              Обновить состояние
            </button>
          )}
          {sub && (
            <button className="btn btn--danger" disabled={deleting} onClick={() => void remove()}>
              Удалить работу
            </button>
          )}
        </div>
      </section>

      {next && (
        <div className="finish">
          <a className="btn btn--big" href={href('a', a.id, 's', cls.id, next.id)}>
            Следующий ученик: {next.name}
          </a>
        </div>
      )}

      {uploading && <CabinetUpload a={a} cls={cls} onClose={() => setUploading(false)} />}
    </div>
  )
}
