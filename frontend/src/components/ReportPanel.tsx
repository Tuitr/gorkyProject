import { useEffect, useRef, type ReactNode } from 'react'
import type { Work } from '../types'

type Props = {
  work: Work
  checking: boolean
  activeMistake: string | null
  onSelectMistake: (id: string | null) => void
  onRetry: () => void
  dismissed: string[] // ошибки, которые учитель снял («не ошибка»)
  onToggleDismiss: (mistakeId: string) => void
  confirmed: boolean
  onToggleConfirm: () => void
  extra?: ReactNode // доп. блок внизу (например, распознанный LaTeX)
}

/** Итог проверки: счёт, список ошибок, выжимка и темы на повторение. */
export function ReportPanel({
  work,
  checking,
  activeMistake,
  onSelectMistake,
  onRetry,
  dismissed,
  onToggleDismiss,
  confirmed,
  onToggleConfirm,
  extra,
}: Props) {
  const listRef = useRef<HTMLOListElement>(null)

  useEffect(() => {
    if (!activeMistake) return
    listRef.current
      ?.querySelector(`[data-id="${activeMistake}"]`)
      ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [activeMistake])

  const r = work.result
  if (!r) {
    return (
      <aside className="report">
        <div className="report__empty">
          {checking ? (
            <>
              <div className="spinner" aria-hidden="true" />
              <p>Проверяем работу…</p>
            </>
          ) : (
            <>
              <h2>Работа ещё не проверена</h2>
              <p className="muted">
                Фото сохранено. Автоматическая проверка заработает, когда подключим сервер и нейросеть. Пока
                подсветку ошибок можно посмотреть на тестовых работах.
              </p>
              <button className="btn" onClick={onRetry}>
                Проверить ещё раз
              </button>
            </>
          )}
        </div>
      </aside>
    )
  }

  const isDismissed = (id: string) => dismissed.includes(id)
  const taskOk = (taskId: string) =>
    r.mistakes.filter((m) => m.taskId === taskId).every((m) => isDismissed(m.id)) ||
    !!r.tasks.find((t) => t.id === taskId)?.ok
  const okCount = r.tasks.filter((t) => taskOk(t.id)).length
  const total = r.tasks.length
  const pct = Math.round((okCount / total) * 100)
  const level = pct >= 85 ? 'good' : pct >= 50 ? 'mid' : 'bad'

  return (
    <aside className="report">
      <header className="report__head">
        <div>
          <div className="muted small">{r.subject}</div>
          <h2>{r.workTopic}</h2>
          <div className="muted">{r.student}</div>
        </div>
        <div className={`score score--${level}`}>
          <b>
            {okCount}/{total}
          </b>
          <span>верно</span>
        </div>
      </header>

      <div className="tasks">
        {r.tasks.map((t) => {
          const ok = taskOk(t.id)
          const m = r.mistakes.find((x) => x.taskId === t.id && !isDismissed(x.id))
          return (
            <button
              key={t.id}
              className={`task-chip ${ok ? 'is-ok' : 'is-err'}${m && m.id === activeMistake ? ' is-active' : ''}`}
              onClick={() => m && onSelectMistake(m.id === activeMistake ? null : m.id)}
              disabled={ok}
              title={ok ? 'Верно' : 'Есть ошибка'}
            >
              {t.label} {ok ? '✓' : '✗'}
            </button>
          )
        })}
      </div>

      <section>
        <h3>Ошибки</h3>
        {r.mistakes.length === 0 ? (
          <p className="muted">Ошибок нет 🎉</p>
        ) : (
          <ol className="mistakes" ref={listRef}>
            {r.mistakes.map((m, i) => {
              const task = r.tasks.find((t) => t.id === m.taskId)
              const off = isDismissed(m.id)
              const open = m.id === activeMistake && !off
              return (
                <li key={m.id} data-id={m.id} className={`mistake${open ? ' is-active' : ''}${off ? ' is-off' : ''}`}>
                  <div className="mistake__row">
                    <button
                      className="mistake__head"
                      onClick={() => !off && onSelectMistake(open ? null : m.id)}
                      disabled={off}
                    >
                      <span className="mistake__num">{i + 1}</span>
                      <span>
                        <b>{task?.label}</b> {m.title}
                        {off && <span className="muted small"> — снято учителем</span>}
                      </span>
                    </button>
                    <button
                      className="link-btn"
                      onClick={() => {
                        if (!off && open) onSelectMistake(null)
                        onToggleDismiss(m.id)
                      }}
                      title={off ? 'Вернуть ошибку' : 'ИИ ошибся — это не ошибка'}
                    >
                      {off ? 'Вернуть' : 'Не ошибка'}
                    </button>
                  </div>
                  {open && (
                    <div className="mistake__body">
                      {m.where && (
                        <p className="mistake__where">
                          <b>Где:</b> {m.where}
                        </p>
                      )}
                      <div className="solution solution--bad">
                        <span>У ученика</span>
                        <code>{m.studentAnswer}</code>
                      </div>
                      <div className="solution solution--good">
                        <span>Как правильно</span>
                        <p>{m.correctSolution}</p>
                      </div>
                      <p>{m.explanation}</p>
                    </div>
                  )}
                </li>
              )
            })}
          </ol>
        )}
      </section>

      <section>
        <h3>Что не так</h3>
        <p className="summary">{r.summary}</p>
      </section>

      {r.weakTopics.length > 0 && (
        <section>
          <h3>Что повторить</h3>
          <ul className="topics">
            {r.weakTopics.map((t) => (
              <li key={t.name}>
                <b>{t.name}</b>
                <span className="muted">{t.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {extra}

      <footer className={`review${confirmed ? ' is-done' : ''}`}>
        {confirmed ? (
          <>
            <span>✓ Проверено учителем</span>
            <button className="link-btn" onClick={onToggleConfirm}>
              Изменить
            </button>
          </>
        ) : (
          <>
            <span className="muted small">Проверку сделал ИИ. Просмотрите ошибки и подтвердите результат.</span>
            <button className="btn btn--primary" onClick={onToggleConfirm}>
              Подтвердить проверку
            </button>
          </>
        )}
      </footer>
    </aside>
  )
}
