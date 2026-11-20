import { useEffect, useState } from 'react'
import { CabinetError, readReport, type AssignmentReport } from '../api/cabinet'
import { sameTask } from '../api/tasks'
import { CATEGORY_LABEL, type Assignment, type MistakeCategory } from '../domain'

/**
 * Сводка кабинета по классу (GET /v1/assignments/{id}/report): кто сдал и кто нет, кто ждёт результата,
 * по каждому заданию — верно / неверно / пропущено / не проверено и замечания по категориям.
 * Считает сервис проверки кабинета; пока его нет, кабинет отвечает «недоступно».
 */
export function CabinetReport({ a, classId }: { a: Assignment; classId: string }) {
  const remote = a.remote?.[classId]
  const [report, setReport] = useState<AssignmentReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!remote) return
    let alive = true
    readReport(remote.assignmentId, remote.rev)
      .then((r) => alive && setReport(r))
      .catch(
        (e) =>
          alive &&
          setError(
            e instanceof CabinetError && e.status >= 500
              ? 'Сервис проверки кабинета недоступен — сводка появится, когда он заработает.'
              : e instanceof Error
                ? e.message
                : 'Сводка кабинета недоступна',
          ),
      )
    return () => {
      alive = false
    }
  }, [remote])

  if (!remote) return null
  const pct = (v: number | null) => (v == null ? '—' : `${Math.round(v * 100)}%`)
  const taskNum = (taskId: string) => a.tasks.find((t) => sameTask(taskId, t))?.num ?? taskId
  const cat = (c: string) => CATEGORY_LABEL[c as MistakeCategory] ?? c

  return (
    <section className="panel">
      <div className="panel__head">
        <h2>Сводка кабинета</h2>
        <span className="muted small">ревизия работы {remote.rev}</span>
      </div>
      {error && <p className="muted">{error}</p>}
      {!error && !report && <p className="muted small">Загрузка…</p>}
      {report && (
        <>
          <div className="cab-rep__people">
            <span>
              Сдали: <b>{report.submittedCount}</b> из {report.students.length}
            </span>
            {report.notSubmitted.length > 0 && (
              <span>
                Не сдали: {report.notSubmitted.map((s) => s.name).join(', ')}
              </span>
            )}
            {report.waiting.length > 0 && (
              <span>
                Ждут результата: {report.waiting.map((s) => s.name).join(', ')}
              </span>
            )}
          </div>
          <table className="table report-table">
            <thead>
              <tr>
                <th>№</th>
                <th>Верно</th>
                <th>Неверно</th>
                <th>Пропущено</th>
                <th>Не проверено</th>
                <th>Замечания</th>
              </tr>
            </thead>
            <tbody>
              {report.tasks.map((t) => (
                <tr key={t.taskId}>
                  <td>
                    <b>№{taskNum(t.taskId)}</b>
                  </td>
                  <td>
                    {t.correct} <span className="muted small">({pct(t.correctShare)})</span>
                  </td>
                  <td>
                    {t.incorrect} <span className="muted small">({pct(t.incorrectShare)})</span>
                  </td>
                  <td>
                    {t.skipped} <span className="muted small">({pct(t.skippedShare)})</span>
                  </td>
                  <td>{t.unchecked}</td>
                  <td>
                    {Object.entries(t.issuesByCategory)
                      .sort((x, y) => y[1] - x[1])
                      .map(([c, n]) => `${cat(c)}: ${n}`)
                      .join(', ') || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}
