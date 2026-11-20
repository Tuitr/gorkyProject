import { useState } from 'react'
import { REVIEW_LABEL, type ReviewItem, type State, type Submission } from '../domain'
import { href } from '../router'
import { patch, useStore } from '../store'
import { Badge, Crop, Modal, PageHead } from '../ui'
import { AssignmentHeader, NotFound, useOwnClasses } from './common'

/** Экран 7. Спорные места: карточка на работу ученика (фото, имя, число предполагаемых ошибок и недочётов);
 *  по нажатию — окно со спорными местами этой работы. */
export function Queue({ assignmentId }: { assignmentId?: string }) {
  const { state } = useStore()
  const classes = useOwnClasses()
  const [openSub, setOpenSub] = useState<string | null>(null)
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
  const current = works.find((w) => w.s.id === openSub)
  // в окне — нерассмотренные места; когда разобрано последнее, работа уходит из списка и окно закрывается
  const currentItems = open.filter((r) => r.submissionId === openSub)

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
              <button className="qwork" onClick={() => setOpenSub(s.id)}>
                <span className="qwork__photo">
                  {s.pages[0] ? <img src={s.pages[0].image} alt="" /> : <span className="muted small">Нет фото</span>}
                </span>
                <b className="qwork__name">{studentName(s)}</b>
                <span className="qwork__count">
                  Предположительных ошибок и недочётов: <b>{count}</b>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {current && (
        <Modal title={studentName(current.s)} onClose={() => setOpenSub(null)} wide>
          <ul className="queue queue--modal">
            {currentItems.map((r) => (
              <QueueCard key={r.id} r={r} />
            ))}
          </ul>
        </Modal>
      )}
    </div>
  )
}

function QueueCard({ r }: { r: ReviewItem }) {
  const { state, update, toast } = useStore()
  const s = state.submissions.find((x) => x.id === r.submissionId) as Submission
  const a = state.assignments.find((x) => x.id === s.assignmentId)!
  const cls = state.classes.find((c) => c.id === s.classId)
  const task = a.tasks.find((t) => t.id === r.taskId)
  const student = cls?.students.find((x) => x.id === s.studentId)
  const page = s.pages[r.page] ?? s.pages[0]
  const [choice, setChoice] = useState(r.chosen ?? '')

  const resolve = (fn?: (st: State) => State, msg = 'Рассмотрено') => {
    update((st) => {
      const next = fn ? fn(st) : st
      return { ...next, review: patch(next.review, r.id, (x) => ({ ...x, resolved: true, chosen: choice || x.chosen, auto: false })) }
    })
    toast(msg)
  }
  const setResult = (st: State, fn: (res: Submission['results'][number]) => Submission['results'][number]) => ({
    ...st,
    submissions: patch(st.submissions, s.id, (x) => ({
      ...x,
      results: x.results.map((res) => (res.taskId === r.taskId ? fn(res) : res)),
    })),
  })

  const assignStudent = () => {
    const target = state.submissions.find((x) => x.assignmentId === a.id && x.studentId === choice && x.id !== s.id)
    if (target && !confirm('У этого ученика уже есть сданная работа. Добавить страницы к ней?')) return
    resolve((st) => {
      if (!target) return { ...st, submissions: patch(st.submissions, s.id, (x) => ({ ...x, studentId: choice })) }
      return {
        ...st,
        submissions: st.submissions
          .filter((x) => x.id !== s.id)
          .map((x) => (x.id === target.id ? { ...x, pages: [...x.pages, ...s.pages], status: 'submitted' as const } : x)),
        review: st.review.map((x) => (x.submissionId === s.id ? { ...x, submissionId: target.id } : x)),
      }
    }, 'Ученик указан')
  }

  const confirmReading = () =>
    resolve(
      (st) =>
        setResult(st, (res) =>
          choice === r.chosen
            ? { ...res, autoReading: undefined }
            : // прочтение изменилось — балл ИИ больше не действителен, оценит учитель
              { ...res, autoReading: undefined, status: 'manual', aiScore: undefined, scoreComment: `Распознавание исправлено учителем: «${choice}»` },
        ),
      choice === r.chosen ? 'Распознавание подтверждено' : 'Распознавание исправлено. Задание ожидает оценки учителя в ведомости',
    )

  return (
    <li className={`qcard${r.resolved ? ' is-done' : ''}`}>
      <div className="qcard__head">
        <Badge tone={r.kind === 'student' || r.kind === 'reshoot' ? 'err' : 'warn'}>
          {r.kind === 'sign' && task ? `Уточнить распознанный символ в задании №${task.num}` : REVIEW_LABEL[r.kind]}
        </Badge>
        {r.resolved && <Badge tone="ok">рассмотрено</Badge>}
        <span className="spacer" />
        <span className="muted small">
          {a.title} · {cls?.name}
        </span>
      </div>
      <div className="qcard__body">
        {page && (
          <a href={href('a', a.id, s.studentId ? 'report' : 'queue', ...(s.studentId ? [s.id] : []))} className="qcard__crop">
            <Crop src={page.image} box={r.box} />
          </a>
        )}
        <div className="qcard__info">
          <b>{student?.name ?? 'Ученик не определён'}</b>
          {task && <span className="muted small"> · №{task.num}</span>}
          <p>{r.note}</p>

          {r.kind === 'sign' && (
            <>
              {r.auto && !r.resolved && <div className="auto-mark">⚠ вариант распознавания выбран автоматически</div>}
              <div className="readings" role="radiogroup" aria-label="Варианты распознавания">
                {r.readings.map((v) => (
                  <label key={v} className={`reading${choice === v ? ' is-on' : ''}`}>
                    <input type="radio" checked={choice === v} onChange={() => setChoice(v)} disabled={r.resolved} />
                    <code>{v}</code>
                    {v === r.chosen && <span className="muted small"> (выбран автоматически)</span>}
                  </label>
                ))}
              </div>
            </>
          )}

          {r.kind === 'student' && (
            <label className="field">
              <span>Укажите ученика</span>
              <select className="input" value={choice} onChange={(e) => setChoice(e.target.value)} disabled={r.resolved}>
                <option value="">— выберите ученика —</option>
                {cls?.students.map((st) => (
                  <option key={st.id} value={st.id}>
                    {st.name}
                    {state.submissions.some((x) => x.assignmentId === a.id && x.studentId === st.id && x.pages.length)
                      ? ' (работа уже сдана)'
                      : ''}
                  </option>
                ))}
              </select>
              {r.auto && !r.resolved && <span className="auto-mark">⚠ ученик определён автоматически по подписи на листе</span>}
            </label>
          )}

          {!r.resolved && (
            <div className="qcard__actions">
              {r.kind === 'student' && (
                <button className="btn btn--primary" disabled={!choice} onClick={assignStudent}>
                  Указать ученика
                </button>
              )}
              {r.kind === 'sign' && (
                <button className="btn btn--primary" disabled={!choice} onClick={confirmReading}>
                  {choice === r.chosen ? 'Подтвердить распознавание' : 'Выбрать этот вариант'}
                </button>
              )}
              {r.kind === 'method' && (
                <>
                  <button
                    className="btn btn--primary"
                    onClick={() =>
                      resolve(
                        (st) =>
                          setResult(st, (res) => ({
                            ...res,
                            status: 'ok',
                            answer: 'correct',
                            technique: 'shown',
                            score: task?.max,
                            scoreComment: 'Другой способ засчитан учителем',
                            mistakes: res.mistakes.map((m) => ({ ...m, dismissed: true })),
                          })),
                        'Способ засчитан',
                      )
                    }
                  >
                    Засчитать способ решения
                  </button>
                  <button className="btn" onClick={() => resolve(undefined, 'Автоматическая оценка сохранена')}>
                    Сохранить автоматическую оценку
                  </button>
                  <a className="link-btn" href={href('a', a.id, 'report', s.id)}>
                    Проверить другой способ решения
                  </a>
                </>
              )}
              {r.kind === 'reshoot' && (
                <>
                  {s.studentId && (
                    <a className="btn btn--primary" href={href('a', a.id, 's', s.classId, s.studentId)}>
                      Заменить или добавить фото
                    </a>
                  )}
                  <button className="btn" onClick={() => resolve(undefined, 'Отмечено')}>
                    Уже исправлено
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  )
}
