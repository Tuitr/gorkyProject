import { useState } from 'react'
import { ErrorPanel } from '../components/ErrorPanel'
import { CATEGORY_LABEL, TECHNIQUE_LABEL, activeMistakes, submissionTotal, taskScore, type TaskResult } from '../domain'
import { href, useRoute } from '../router'
import { useStore } from '../store'
import { Badge, Photo, type Tone } from '../ui'
import { NotFound, useAssignment } from './common'

const ANSWER: Record<TaskResult['answer'], [string, Tone]> = {
  correct: ['верный', 'ok'],
  wrong: ['неверный', 'err'],
  none: ['нет ответа', 'muted'],
}

/** Экран 10. Отчёт ученика: по каждому номеру — ответ, ход решения, приём. */
export function StudentReport({ id, submissionId }: { id: string; submissionId?: string }) {
  const { state } = useStore()
  const { a, classes } = useAssignment(id)
  // выбранная ошибка: номер и (если нажали на конкретную) сама ошибка; undefined — ещё не выбирали (открыта первая),
  // null — подсказку закрыли
  const [panel, setPanel] = useState<{ tid: string; mid?: string } | null | undefined>(undefined)
  const [pageIdx, setPageIdx] = useState(0)
  const fromQueue = useRoute().query.get('from') === 'queue'
  const s = state.submissions.find((x) => x.id === submissionId)
  const cls = classes.find((c) => c.id === s?.classId)
  if (!a || !s || !cls) return <NotFound what={!a ? 'Работа' : 'Сданная работа'} />

  const student = cls.students.find((x) => x.id === s.studentId)
  const v = a.variants.find((x) => x.id === s.variantId)
  const total = submissionTotal(a, s)
  const page = s.pages[pageIdx]
  // первая ошибка по порядку заданий — открыта по умолчанию
  const firstOf = (tid: string) => {
    const r = s.results.find((x) => x.taskId === tid)
    return r ? activeMistakes(r)[0]?.id : undefined
  }
  const firstTask = a.tasks.find((t) => firstOf(t.id))
  const open = panel === undefined ? (firstTask ? { tid: firstTask.id, mid: firstOf(firstTask.id) } : null) : panel
  // открытая ошибка подсвечивается: в таблице и рамкой на фото
  const openKey = open ? `${open.tid}|${open.mid ?? firstOf(open.tid) ?? ''}` : ''
  const allMarks = s.results.flatMap((r) =>
    activeMistakes(r)
      .filter((m) => m.page === pageIdx)
      .map((m) => ({
        id: `${r.taskId}|${m.id}`,
        box: m.box,
        label: `№${a.tasks.find((t) => t.id === r.taskId)?.num}`,
        tone: `${r.taskId}|${m.id}` === openKey ? ('focus' as const) : ('err' as const),
      })),
  )

  return (
    <div className="page page--wide report-page">
      <div className="muted small no-print">
        {fromQueue ? (
          <a href="#/queue">← Спорные места</a>
        ) : (
          <a href={href('a', a.id, 'sheet', { c: cls.id })}>← Ведомость {cls.name}</a>
        )}
      </div>
      <div className="page-head">
        <div>
          <h1>{student?.name ?? 'Ученик не определён'}</h1>
          <div className="muted">
            {a.title} · {cls.name} · вариант {v?.name ?? '—'}
          </div>
        </div>
        <div className="page-head__actions">
          <div className="total">
            <b>
              {s.teacherTotal ?? total.sum}/{total.max}
            </b>
            <span>
              {s.teacherTotal != null ? 'итог учителя' : total.preliminary ? 'предварительно' : 'баллов'}
            </span>
          </div>
          <button className="btn no-print" onClick={() => print()}>
            Печать
          </button>
        </div>
      </div>

      {s.status !== 'processed' && (
        <div className="notice notice--warn">Работа ещё не проверена. Отчёт будет доступен после проверки.</div>
      )}

      <div className="report-split">
        {/* слева — фото работы (уменьшено, закреплено при прокрутке) */}
        <div className="report-split__side">
        <section className="panel no-print-break">
          <div className="panel__head">
            <h2>Фото</h2>
            {s.pages.length > 1 && (
              <div className="segmented">
                {s.pages.map((p, i) => (
                  <button key={p.id} aria-selected={i === pageIdx} onClick={() => setPageIdx(i)}>
                    {i + 1}
                  </button>
                ))}
              </div>
            )}
          </div>
          {page ? <Photo src={page.image} marks={allMarks} onMark={(key) => {
                const [tid, mid] = key.split('|')
                setPanel({ tid, mid })
              }} /> : <p className="muted">Страницы не загружены</p>}
        </section>
        </div>

        {/* справа — подсказка по выбранной ошибке (сначала пусто), под ней итог по номерам */}
        <div className="report-grid report-grid--stack">
          <div className="no-print">
          {open ? (
            <ErrorPanel
              key={openKey}
              submissionId={s.id}
              taskId={open.tid}
              mistakeId={open.mid}
              inline
              onClose={() => setPanel(null)}
            />
          ) : (
            <section className="panel ep-empty" aria-label="Подробности ошибки" />
          )}
          </div>
        <section className="panel">
          <table className="table report-table">
            <thead>
              <tr>
                <th>№</th>
                <th>Ответ</th>
                <th>Ход решения</th>
                <th>Метод решения</th>
                <th>Балл</th>
              </tr>
            </thead>
            <tbody>
              {a.tasks.map((t) => {
                const r = s.results.find((x) => x.taskId === t.id)
                const sc = taskScore(r)
                const ms = r ? activeMistakes(r) : []
                return (
                  <tr key={t.id}>
                    <td>
                      <b>№{t.num}</b>
                    </td>
                    <td>
                      {r ? <Badge tone={ANSWER[r.answer][1]}>{ANSWER[r.answer][0]}</Badge> : <Badge>не сдано</Badge>}
                    </td>
                    <td>
                      {!r || r.status === 'missing' ? (
                        <span className="muted">задание отсутствует в работе</span>
                      ) : r.status === 'stopped' ? (
                        <Badge tone="warn">проверка остановлена</Badge>
                      ) : r.status === 'insufficient' ? (
                        <Badge tone="warn">недостаточно записей для оценки</Badge>
                      ) : r.status === 'unreadable' ? (
                        <Badge tone="warn">неразборчиво, требуется ручная проверка</Badge>
                      ) : r.status === 'manual' ? (
                        <Badge tone="warn">ручная проверка</Badge>
                      ) : ms.length === 0 ? (
                        <span className="ok-line">✓ без ошибок</span>
                      ) : (
                        <ul className="steps-list">
                          {ms.map((m) => (
                            <li key={m.id}>
                              <button
                                className={`link-btn link-btn--text${`${t.id}|${m.id}` === openKey ? ' is-open' : ''}`}
                                aria-pressed={`${t.id}|${m.id}` === openKey}
                                onClick={() => setPanel({ tid: t.id, mid: m.id })}
                              >
                                {m.title}
                              </button>{' '}
                              <Badge tone="err">{CATEGORY_LABEL[m.category]}</Badge>
                            </li>
                          ))}
                        </ul>
                      )}
                      {r?.autoReading && <div className="auto-mark">⚠ {r.autoReading}</div>}
                      {r?.scoreComment && <div className="muted small">Учитель: {r.scoreComment}</div>}
                    </td>
                    <td>
                      {r ? (
                        <Badge tone={r.technique === 'shown' ? 'ok' : r.technique === 'not_shown' ? 'warn' : 'muted'}>
                          {TECHNIQUE_LABEL[r.technique]}
                        </Badge>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>
                      <button className="link-btn" onClick={() => setPanel({ tid: t.id })}>
                        {sc ?? '—'}/{t.max}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {s.summary && (
            <>
              <h3>Заключение</h3>
              <p className="summary">{s.summary}</p>
            </>
          )}
        </section>
        </div>
      </div>
    </div>
  )
}
