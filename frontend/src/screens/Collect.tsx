import { useMemo, useRef, useState } from 'react'
import { useActions } from '../actions'
import { SUBMISSION_STATUS_LABEL, uid, type Submission, type SubmissionStatus } from '../domain'
import { href } from '../router'
import { hashString, useStore } from '../store'
import { Badge, type Tone } from '../ui'
import { compressImage } from '../photos'
import { AssignmentHeader, ClassTabs, NotFound, useAssignment } from './common'

export const STATUS_TONE: Record<SubmissionStatus, Tone> = {
  none: 'muted',
  submitted: 'info',
  processing: 'info',
  processed: 'ok',
  error: 'err',
}

/** Экран 5. Сбор работ: ученики класса, статус сдачи, поиск, запуск обработки. */
export function Collect({ id, classId }: { id: string; classId?: string }) {
  const { state, update, jobs, toast } = useStore()
  const { processAll, process } = useActions()
  const { a, classes } = useAssignment(id)
  const [q, setQ] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const cls = classes.find((c) => c.id === classId) ?? classes[0]

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
  const handed = subs.filter((s) => s.status !== 'none' && s.pages.length).length
  const processed = subs.filter((s) => s.status === 'processed').length
  const pending = subs.filter((s) => s.pages.length && (s.status === 'submitted' || s.status === 'error')).length
  const running = subs.some((s) => jobs[`check:${s.id}`]?.status === 'running')

  const rows = [
    ...cls.students.map((st) => ({ st, s: subs.find((x) => x.studentId === st.id) })),
    ...subs.filter((s) => !s.studentId).map((s) => ({ st: null, s })),
  ].filter(({ st }) => !q.trim() || (st?.name ?? 'не определён').toLowerCase().includes(q.trim().toLowerCase()))

  /** Пачка фото без учеников — каждое фото отдельной сдачей, ученика укажете в очереди. */
  const uploadBatch = async (files: FileList) => {
    const known = new Set(state.submissions.filter((s) => s.assignmentId === id).flatMap((s) => s.pages.map((p) => p.hash)))
    const fresh: Submission[] = []
    let dup = 0
    for (const f of Array.from(files)) {
      const image = await compressImage(f)
      const hash = hashString(image)
      if (known.has(hash)) {
        dup++
        continue
      }
      known.add(hash)
      fresh.push({
        id: uid('s'),
        assignmentId: id,
        classId: cls.id,
        studentId: null,
        variantId: a.variants[0]?.id ?? null,
        pages: [{ id: uid('p'), image, hash }],
        status: 'submitted',
        results: [],
        approved: false,
      })
    }
    if (fresh.length) update((s) => ({ ...s, submissions: [...s.submissions, ...fresh] }))
    toast(`Загружено: ${fresh.length}${dup ? ` · повторы пропущены: ${dup}` : ''}`)
  }

  return (
    <div className="page page--wide">
      <AssignmentHeader a={a} step="collect" classId={cls.id} />
      <ClassTabs classes={classes} value={cls.id} a={a} step="collect" />

      {a.status === 'draft' && a.variants.length > 0 && (
        <div className="notice notice--warn">
          Работа имеет статус «Черновик». Сбор работ доступен, однако рекомендуется сначала <a href={href('a', a.id, 'variants')}>утвердить варианты</a>
          : проверка выполняется по эталонным ответам.
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
          <button
            className="btn btn--primary"
            disabled={!pending || running}
            onClick={() => void processAll(a.id, cls.id)}
            title={!pending ? 'Нет новых сданных работ' : undefined}
          >
            {running ? 'Выполняется проверка…' : `Запустить проверку${pending ? ` (${pending})` : ''}`}
          </button>
          <button className="btn" onClick={() => fileRef.current?.click()}>
            Загрузить несколько фото
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files?.length) void uploadBatch(e.target.files)
              e.target.value = ''
            }}
          />
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
            <th>Вариант</th>
            <th>Страниц</th>
            <th>Статус</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map(({ st, s }) => {
            const status: SubmissionStatus = s && s.pages.length ? s.status : 'none'
            const job = s ? jobs[`check:${s.id}`] : undefined
            const v = a.variants.find((x) => x.id === s?.variantId)
            const link = s && !st ? href('a', a.id, 'queue') : href('a', a.id, 's', cls.id, st!.id)
            return (
              <tr key={st?.id ?? s!.id} className={s && attention.has(s.id) ? 'is-attention' : status === 'none' ? 'is-none' : ''}>
                <td>
                  <a href={link}>{st?.name ?? <i>Ученик не определён</i>}</a>
                </td>
                <td>{v ? v.name : <span className="muted">—</span>}</td>
                <td>{s?.pages.length || <span className="muted">0</span>}</td>
                <td>
                  <Badge tone={job?.status === 'running' ? 'info' : STATUS_TONE[status]}>
                    {job?.status === 'running' ? 'На проверке…' : SUBMISSION_STATUS_LABEL[status]}
                  </Badge>
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
                    <button className="link-btn" onClick={() => void process(s.id)}>
                      Повторить
                    </button>
                  ) : status === 'processed' && s ? (
                    <a className="link-btn" href={href('a', a.id, 'report', s.id)}>
                      Отчёт
                    </a>
                  ) : status === 'none' ? (
                    <a className="link-btn link-btn--quiet" href={link}>
                      Отправить фото
                    </a>
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
              <td colSpan={5} className="muted">
                Ученики не найдены
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
