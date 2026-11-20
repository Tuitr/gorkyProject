import { useState } from 'react'
import { useActions } from '../actions'
import { DisputeCard } from './DisputeCard'
import type { ReviewChange } from '../api/cabinet'
import {
  CATEGORIES,
  CATEGORY_LABEL,
  TASK_STATUS_LABEL,
  activeMistakes,
  taskScore,
  type MistakeCategory,
  type StepMistake,
  type Submission,
  type TaskResult,
} from '../domain'
import { patch, useStore } from '../store'
import { Badge, Drawer, JobStatus, Photo } from '../ui'

const ANSWER_V = { correct: 'верный', incorrect: 'неверный', skipped: 'нет ответа', unreadable: 'неразборчиво' }
const REASONING_V = { valid: 'верный', invalid: 'с ошибками', not_shown: 'не показан', unknown: 'не определён' }
const METHOD_V = { met: 'применён', unmet: 'не применён', unconfirmed: 'не подтверждён', optional: 'не требуется' }

type Props = {
  submissionId: string
  taskId: string
  onClose: () => void
  /** какую ошибку номера показать первой */
  mistakeId?: string
  /** встроенная панель на странице (а не выезжающая справа) */
  inline?: boolean
}

/** Экран 9. Панель ошибки: текст ИИ со ссылкой на строку, фото с фрагментом, расшифровка, категория, правило, правка. */
export function ErrorPanel({ submissionId, taskId, onClose, mistakeId, inline }: Props) {
  const { state, update, jobs, toast } = useStore()
  const { process, editRemote } = useActions()
  const s = state.submissions.find((x) => x.id === submissionId)
  const a = state.assignments.find((x) => x.id === s?.assignmentId)
  const task = a?.tasks.find((t) => t.id === taskId)
  const res = s?.results.find((r) => r.taskId === taskId)
  const [mid, setMid] = useState<string | null>(mistakeId ?? res?.mistakes[0]?.id ?? null)
  const [editLine, setEditLine] = useState<{ page: number; line: number; text: string } | null>(null)
  const [scoring, setScoring] = useState<{ score: string; comment: string } | null>(null)
  const [dirty, setDirty] = useState(false)
  // правки результата кабинета: замечание и вердикты задания
  const [issueEdit, setIssueEdit] = useState<{ comment: string; correction: string } | null>(null)
  const [verdict, setVerdict] = useState<NonNullable<TaskResult['remote']> | null>(null)
  const [saving, setSaving] = useState(false)
  if (!s || !a || !task) return null

  const student = state.classes.flatMap((c) => c.students).find((x) => x.id === s.studentId)
  const m = res?.mistakes.find((x) => x.id === mid) ?? res?.mistakes[0]
  const pageIdx = m?.page ?? 0
  const page = s.pages[pageIdx]
  const lines = page?.lines ?? []
  const job = jobs[`check:${s.id}`]
  // результат пришёл из кабинета — правки уходят на сервер (PATCH review), а не только в браузер
  const fromCabinet = !!res?.remote && !!s.remote?.resultRev
  // нерассмотренные спорные места этого задания — разбираются прямо здесь
  const disputes = state.review.filter((r) => r.submissionId === s.id && r.taskId === taskId && !r.resolved)
  const saveRemote = async (changes: ReviewChange[], done?: string) => {
    setSaving(true)
    const ok = await editRemote(s.id, changes, done)
    setSaving(false)
    return ok
  }

  const setSub = (fn: (x: Submission) => Submission) => update((st) => ({ ...st, submissions: patch(st.submissions, s.id, fn) }))
  const setRes = (fn: (r: TaskResult) => TaskResult) =>
    setSub((x) => ({ ...x, results: x.results.map((r) => (r.taskId === taskId ? fn(r) : r)), approved: false }))
  const setMistake = (id: string, p: Partial<StepMistake>) =>
    setRes((r) => ({ ...r, mistakes: r.mistakes.map((x) => (x.id === id ? { ...x, ...p } : x)) }))

  const saveLine = () => {
    if (!editLine) return
    setSub((x) => ({
      ...x,
      pages: x.pages.map((p, i) =>
        i !== editLine.page
          ? p
          : { ...p, lines: (p.lines ?? []).map((l, j) => (j === editLine.line ? { ...l, text: editLine.text, fixed: true } : l)) },
      ),
    }))
    setEditLine(null)
    setDirty(true)
    toast('Распознавание исправлено')
  }

  const saveScore = () => {
    if (!scoring) return
    const v = scoring.score.trim() === '' ? undefined : Math.max(0, Math.min(task.max, Number(scoring.score)))
    setRes((r) => ({ ...r, score: v, scoreComment: scoring.comment.trim() || undefined }))
    setScoring(null)
    toast(v == null ? 'Восстановлен автоматический балл' : `Балл за №${task.num}: ${v}`)
  }

  // окно строк вокруг ошибки
  const focus = new Set(m?.lines ?? [])
  const from = m?.lines.length ? Math.max(0, Math.min(...m.lines) - 3) : 0
  const to = m?.lines.length ? Math.min(lines.length, Math.max(...m.lines) + 4) : Math.min(lines.length, 12)
  const score = taskScore(res)

  const title = (
    <>
      №{task.num} · {student?.name ?? 'ученик не определён'}
    </>
  )
  const body = (
      <div className="ep">
        <div className="ep__summary">
          {res ? (
            <Badge tone={res.status === 'ok' ? 'ok' : res.status === 'error' ? 'err' : 'warn'}>{TASK_STATUS_LABEL[res.status]}</Badge>
          ) : (
            <Badge>не проверено</Badge>
          )}
          <span>
            Балл: <b>{score ?? '—'}</b> из {task.max}
            {res?.score != null && <span className="muted small"> (изменён учителем)</span>}
          </span>
          {res?.autoReading && <span className="auto-mark">⚠ {res.autoReading}</span>}
        </div>
        {res?.scoreComment && <p className="muted small">Комментарий: {res.scoreComment}</p>}

        {disputes.map((r) => (
          <DisputeCard key={r.id} r={r} compact />
        ))}

        {res && res.mistakes.length > 1 && (
          <div className="ep__tabs">
            {res.mistakes.map((x, i) => (
              <button key={x.id} className={`chip${x.id === m?.id ? ' is-on' : ''}${x.dismissed ? ' is-off' : ''}`} onClick={() => setMid(x.id)}>
                {i + 1}. {x.title}
              </button>
            ))}
          </div>
        )}

        {m ? (
          <section className={`ep__mistake${m.dismissed ? ' is-off' : ''}`}>
            <h3>
              {m.title} {m.dismissed && <Badge>не засчитано учителем</Badge>}
            </h3>
            <p>
              {m.where}
              {m.lines.length > 0 && (
                <>
                  {' '}
                  <button
                    className="line-ref"
                    onClick={() =>
                      document.getElementById(`line-${m.page}-${m.lines[0]}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                    }
                  >
                    → страница {m.page + 1}, строка {m.lines[0] + 1}
                  </button>
                </>
              )}
            </p>
            {/* у замечаний кабинета нет «что написал ученик» — блок не показываем */}
            {m.studentAnswer && m.studentAnswer !== '—' && (
              <div className="solution solution--bad">
                <span>Ответ ученика</span>
                <code>{m.studentAnswer}</code>
              </div>
            )}
            <div className="solution solution--good">
              <span>Верное решение</span>
              <p>{m.correctSolution}</p>
            </div>
            {m.explanation && <p>{m.explanation}</p>}
            <div className="ep__meta">
              <label>
                Категория{' '}
                <select
                  className="input input--sm"
                  value={m.category}
                  onChange={(e) => setMistake(m.id, { category: e.target.value as MistakeCategory })}
                >
                  {CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {CATEGORY_LABEL[c]}
                    </option>
                  ))}
                </select>
              </label>
              <span>
                Правило: {m.rule ? <b>{m.rule}</b> : <span className="muted">общий критерий — по эталонному ответу</span>}
              </span>
            </div>
            {fromCabinet ? (
              issueEdit ? (
                <div className="ep__edit">
                  <label className="field">
                    <span>Комментарий</span>
                    <textarea
                      className="input"
                      rows={3}
                      value={issueEdit.comment}
                      onChange={(e) => setIssueEdit({ ...issueEdit, comment: e.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span>Исправление</span>
                    <textarea
                      className="input"
                      rows={2}
                      value={issueEdit.correction}
                      onChange={(e) => setIssueEdit({ ...issueEdit, correction: e.target.value })}
                    />
                  </label>
                  <div className="row">
                    <button className="btn" onClick={() => setIssueEdit(null)}>
                      Отмена
                    </button>
                    <button
                      className="btn btn--primary"
                      disabled={saving || !issueEdit.comment.trim() || !issueEdit.correction.trim()}
                      onClick={async () => {
                        const ok = await saveRemote(
                          [{ type: 'update_issue', issueId: m.id, comment: issueEdit.comment.trim(), correction: issueEdit.correction.trim() }],
                          'Замечание изменено',
                        )
                        if (ok) setIssueEdit(null)
                      }}
                    >
                      Сохранить замечание
                    </button>
                  </div>
                </div>
              ) : (
                <div className="row row--start">
                  <button
                    className="link-btn"
                    onClick={() => setIssueEdit({ comment: m.explanation || m.title, correction: m.correctSolution === '—' ? '' : m.correctSolution })}
                  >
                    Изменить замечание
                  </button>
                  <button
                    className="link-btn link-btn--danger"
                    disabled={saving}
                    onClick={() =>
                      confirm('Убрать замечание из результата? На сервере его нельзя будет вернуть.') &&
                      void saveRemote([{ type: 'remove_issue', issueId: m.id }], 'Замечание убрано')
                    }
                  >
                    Не считать ошибкой
                  </button>
                </div>
              )
            ) : (
              <button className="link-btn" onClick={() => setMistake(m.id, { dismissed: !m.dismissed })}>
                {m.dismissed ? 'Восстановить ошибку' : 'Не считать ошибкой'}
              </button>
            )}
          </section>
        ) : (
          <p className="muted">{res?.status === 'ok' ? 'Ошибок нет: задание выполнено верно.' : 'Автоматическая проверка ошибок не выявила.'}</p>
        )}

        {/* на странице проверки работы (inline) фото и распознанный текст не показываются — фото уже рядом */}
        {!inline && page && page.kind !== 'text' && (
          <section>
            <h3>Фото{s.pages.length > 1 ? ` · страница ${pageIdx + 1}` : ''}</h3>
            <Photo
              src={page.image}
              marks={res ? activeMistakes(res).filter((x) => x.page === pageIdx).map((x) => ({ id: x.id, box: x.box, tone: x.id === m?.id ? 'focus' as const : 'err' as const })) : []}
              onMark={setMid}
            />
          </section>
        )}

        {!inline && (
          <section>
            <h3>Распознанный текст по строкам</h3>
            {!lines.length ? (
              <p className="muted small">Распознанный текст отсутствует: работа размечена вручную или ещё не распознана.</p>
            ) : (
              <ol className="lines" start={from + 1}>
                {lines.slice(from, to).map((l, k) => {
                  const j = from + k
                  const editing = editLine?.page === pageIdx && editLine.line === j
                  return (
                    <li key={j} id={`line-${pageIdx}-${j}`} className={focus.has(j) ? 'is-focus' : ''}>
                      {editing ? (
                        <span className="line-edit">
                          <input
                            className="input input--sm"
                            autoFocus
                            value={editLine.text}
                            onChange={(e) => setEditLine({ ...editLine, text: e.target.value })}
                            onKeyDown={(e) => e.key === 'Enter' && saveLine()}
                          />
                          <button className="link-btn" onClick={saveLine}>
                            Сохранить
                          </button>
                        </span>
                      ) : (
                        <>
                          <code>{l.text}</code>
                          {l.fixed && <Badge tone="info">исправлено</Badge>}
                          <button
                            className="link-btn"
                            onClick={() => setEditLine({ page: pageIdx, line: j, text: l.text })}
                            title="Исправить распознавание"
                          >
                            ✎
                          </button>
                        </>
                      )}
                    </li>
                  )
                })}
              </ol>
            )}
            {lines.length > 0 && (
              <div className="row">
                <button className="btn" onClick={() => setEditLine({ page: pageIdx, line: m?.lines[0] ?? from, text: lines[m?.lines[0] ?? from]?.text ?? '' })}>
                  Исправить распознавание
                </button>
                {dirty && (
                  <JobStatus
                    job={job}
                    onRetry={() => void process(s.id)}
                    idle={
                      <button className="btn btn--primary" onClick={() => void process(s.id).then(() => setDirty(false))}>
                        Перепроверить с исправлением
                      </button>
                    }
                  />
                )}
              </div>
            )}
          </section>
        )}

        {fromCabinet && res?.remote && (
          <section>
            <h3>Оценка задания</h3>
            {(() => {
              const v = verdict ?? res.remote
              const changed =
                !!verdict &&
                (verdict.answer !== res.remote.answer ||
                  verdict.reasoning !== res.remote.reasoning ||
                  verdict.method !== res.remote.method)
              return (
                <div className="ep__verdicts">
                  <label>
                    Ответ{' '}
                    <select
                      className="input input--sm"
                      value={v.answer}
                      onChange={(e) => setVerdict({ ...v, answer: e.target.value as typeof v.answer })}
                    >
                      {Object.entries(ANSWER_V).map(([k, l]) => (
                        <option key={k} value={k}>
                          {l}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Ход решения{' '}
                    <select
                      className="input input--sm"
                      value={v.reasoning}
                      onChange={(e) => setVerdict({ ...v, reasoning: e.target.value as typeof v.reasoning })}
                    >
                      {Object.entries(REASONING_V).map(([k, l]) => (
                        <option key={k} value={k}>
                          {l}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Метод{' '}
                    <select
                      className="input input--sm"
                      value={v.method}
                      onChange={(e) => setVerdict({ ...v, method: e.target.value as typeof v.method })}
                    >
                      {Object.entries(METHOD_V).map(([k, l]) => (
                        <option key={k} value={k}>
                          {l}
                        </option>
                      ))}
                    </select>
                  </label>
                  {changed && (
                    <button
                      className="btn btn--primary"
                      disabled={saving}
                      onClick={async () => {
                        const r = res.remote!
                        const ok = await saveRemote(
                          [
                            {
                              type: 'update_verdict',
                              taskId: r.taskId,
                              ...(v.answer !== r.answer ? { answer: v.answer } : {}),
                              ...(v.reasoning !== r.reasoning ? { reasoning: v.reasoning } : {}),
                              ...(v.method !== r.method ? { method: v.method } : {}),
                            },
                          ],
                          'Оценка задания изменена',
                        )
                        if (ok) setVerdict(null)
                      }}
                    >
                      Сохранить оценку
                    </button>
                  )}
                </div>
              )
            })()}
          </section>
        )}

        <section>
          <h3>Балл</h3>
          {scoring ? (
            <div className="score-edit">
              <label className="field field--inline">
                <span>Балл (из {task.max})</span>
                <input
                  className="input input--num"
                  type="number"
                  min={0}
                  max={task.max}
                  value={scoring.score}
                  onChange={(e) => setScoring({ ...scoring, score: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Обоснование изменения балла</span>
                <textarea
                  className="input"
                  rows={2}
                  value={scoring.comment}
                  onChange={(e) => setScoring({ ...scoring, comment: e.target.value })}
                  placeholder="Например: метод решения верный, допущена вычислительная описка"
                />
              </label>
              <div className="row">
                <button className="btn" onClick={() => setScoring(null)}>
                  Отмена
                </button>
                <button className="btn btn--primary" disabled={!scoring.comment.trim() && scoring.score !== ''} onClick={saveScore}>
                  Сохранить балл
                </button>
              </div>
              <span className="muted small">Если поле не заполнено, сохраняется балл автоматической проверки.</span>
            </div>
          ) : (
            <button
              className="btn"
              onClick={() => setScoring({ score: res?.score != null ? String(res.score) : String(score ?? ''), comment: res?.scoreComment ?? '' })}
              disabled={!res}
            >
              Изменить балл
            </button>
          )}
        </section>
      </div>
  )

  return inline ? (
    <section className="panel ep-inline" aria-label="Подробности ошибки">
      <div className="panel__head">
        <h2>{title}</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
          ✕
        </button>
      </div>
      {body}
    </section>
  ) : (
    <Drawer title={title} onClose={onClose}>
      {body}
    </Drawer>
  )
}
