import { useState } from 'react'
import { useActions } from '../actions'
import {
  CATEGORIES,
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

type Props = { submissionId: string; taskId: string; onClose: () => void }

/** Экран 9. Панель ошибки: текст ИИ со ссылкой на строку, фото с фрагментом, расшифровка, категория, правило, правка. */
export function ErrorPanel({ submissionId, taskId, onClose }: Props) {
  const { state, update, jobs, toast } = useStore()
  const { process } = useActions()
  const s = state.submissions.find((x) => x.id === submissionId)
  const a = state.assignments.find((x) => x.id === s?.assignmentId)
  const task = a?.tasks.find((t) => t.id === taskId)
  const res = s?.results.find((r) => r.taskId === taskId)
  const [mid, setMid] = useState<string | null>(res?.mistakes[0]?.id ?? null)
  const [editLine, setEditLine] = useState<{ page: number; line: number; text: string } | null>(null)
  const [scoring, setScoring] = useState<{ score: string; comment: string } | null>(null)
  const [dirty, setDirty] = useState(false)
  if (!s || !a || !task) return null

  const student = state.classes.flatMap((c) => c.students).find((x) => x.id === s.studentId)
  const m = res?.mistakes.find((x) => x.id === mid) ?? res?.mistakes[0]
  const pageIdx = m?.page ?? 0
  const page = s.pages[pageIdx]
  const lines = page?.lines ?? []
  const job = jobs[`check:${s.id}`]

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
    toast(v == null ? 'Возвращён балл ИИ' : `Балл за №${task.num}: ${v}`)
  }

  // окно строк вокруг ошибки
  const focus = new Set(m?.lines ?? [])
  const from = m?.lines.length ? Math.max(0, Math.min(...m.lines) - 3) : 0
  const to = m?.lines.length ? Math.min(lines.length, Math.max(...m.lines) + 4) : Math.min(lines.length, 12)
  const score = taskScore(res)

  return (
    <Drawer
      title={
        <>
          №{task.num} · {student?.name ?? 'ученик не определён'}
        </>
      }
      onClose={onClose}
    >
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
              {m.title} {m.dismissed && <Badge>снято учителем</Badge>}
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
                    → стр. {m.page + 1}, строка {m.lines[0] + 1}
                  </button>
                </>
              )}
            </p>
            <div className="solution solution--bad">
              <span>У ученика</span>
              <code>{m.studentAnswer}</code>
            </div>
            <div className="solution solution--good">
              <span>Как правильно</span>
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
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
              <span>
                Правило: {m.rule ? <b>{m.rule}</b> : <span className="muted">общее — по ключу</span>}
              </span>
            </div>
            <button className="link-btn" onClick={() => setMistake(m.id, { dismissed: !m.dismissed })}>
              {m.dismissed ? 'Вернуть ошибку' : 'Это не ошибка'}
            </button>
          </section>
        ) : (
          <p className="muted">{res?.status === 'ok' ? 'Ошибок нет — номер решён верно.' : 'Ошибок ИИ не нашёл.'}</p>
        )}

        {page && (
          <section>
            <h3>Фото{s.pages.length > 1 ? ` · страница ${pageIdx + 1}` : ''}</h3>
            <Photo
              src={page.image}
              marks={res ? activeMistakes(res).filter((x) => x.page === pageIdx).map((x) => ({ id: x.id, box: x.box, tone: x.id === m?.id ? 'focus' as const : 'err' as const })) : []}
              onMark={setMid}
            />
          </section>
        )}

        <section>
          <h3>Расшифровка строк</h3>
          {!lines.length ? (
            <p className="muted small">Расшифровки нет: работа размечена вручную или ещё не распознана.</p>
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
                <span>Комментарий — почему</span>
                <textarea
                  className="input"
                  rows={2}
                  value={scoring.comment}
                  onChange={(e) => setScoring({ ...scoring, comment: e.target.value })}
                  placeholder="Например: способ верный, арифметика — описка"
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
              <span className="muted small">Пустое поле — вернуть балл ИИ.</span>
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
    </Drawer>
  )
}
