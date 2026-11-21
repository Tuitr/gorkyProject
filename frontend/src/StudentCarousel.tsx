import { useEffect, useRef, useState } from 'react'
import type { TaskResult } from './api'
import { Metrics, ProgressBar, RemoveButton, Score, scoreText, StatusBadge, UploadButton } from './components'
import { ready, rowStatus, taskCell, type Row, type TaskColumn } from './workspace'

const answers: Record<TaskResult['answer'], { label: string; tone: string }> = {
  correct: { label: 'Верно', tone: 'success' },
  incorrect: { label: 'Неверно', tone: 'danger' },
  skipped: { label: 'Пропущено', tone: 'muted' },
  unreadable: { label: 'Неразборчиво', tone: 'warning' },
}

type Props = {
  rows: Row[]
  tasks: TaskColumn[]
  gradeOf: (row: Row) => number | null | undefined
  onOpen: (studentId: string) => void
  onFeedback: (row: Row) => void
  onRemove: (row: Row) => void
  onUpload: (row: Row) => void
}

// Карусель учеников: карточка в центре — текущий ученик, под лентой его статистика по заданиям.
export function StudentCarousel({ rows, tasks, gradeOf, onOpen, onFeedback, onRemove, onUpload }: Props) {
  const track = useRef<HTMLDivElement>(null)
  const [current, setCurrent] = useState(0)
  const index = Math.min(current, Math.max(rows.length - 1, 0))
  const row = rows[index]

  // Центральной считаем карточку, чей центр ближе всего к центру ленты.
  useEffect(() => {
    const element = track.current
    if (!element) return
    let frame = 0
    const onScroll = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const middle = element.scrollLeft + element.clientWidth / 2
        let best = 0
        let distance = Infinity
        Array.from(element.children).forEach((child, position) => {
          const card = child as HTMLElement
          const gap = Math.abs(card.offsetLeft + card.offsetWidth / 2 - middle)
          if (gap < distance) { distance = gap; best = position }
        })
        setCurrent(best)
      })
    }
    element.addEventListener('scroll', onScroll, { passive: true })
    return () => { element.removeEventListener('scroll', onScroll); cancelAnimationFrame(frame) }
  }, [rows.length])

  function go(position: number) {
    const element = track.current
    const card = element?.children[position] as HTMLElement | undefined
    if (!element || !card) return
    element.scrollTo({ left: card.offsetLeft - (element.clientWidth - card.offsetWidth) / 2, behavior: 'smooth' })
    setCurrent(position)
  }

  if (!rows.length) return null
  const result = ready(row.check)
  const status = rowStatus(row)
  const grade = gradeOf(row)
  const counts = { correct: 0, incorrect: 0, skipped: 0, unreadable: 0 }
  for (const task of result?.taskResults ?? []) counts[task.answer] += 1
  const total = result?.total
  const share = total && total.maxScore > 0 ? total.score / total.maxScore : undefined

  return <section className="carousel" aria-label="Ученики">
    <div className="carousel-stage">
      <button type="button" className="carousel-arrow" aria-label="Предыдущий ученик" disabled={index === 0} onClick={() => go(index - 1)}>‹</button>
      <div className="carousel-track" ref={track} tabIndex={0} aria-label="Лента учеников"
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft' && index > 0) { event.preventDefault(); go(index - 1) }
          if (event.key === 'ArrowRight' && index < rows.length - 1) { event.preventDefault(); go(index + 1) }
        }}>
        {rows.map((item, position) => {
          const itemResult = ready(item.check)
          const itemStatus = rowStatus(item)
          return <div key={item.student.studentId} className="carousel-item" aria-current={position === index ? 'true' : undefined}>
            <div role="button" tabIndex={0} className="carousel-card" onClick={() => go(position)}
              onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); go(position) } }}>
              <span className="carousel-avatar" aria-hidden="true">{item.student.name.trim().charAt(0).toUpperCase()}</span>
              <strong>{item.student.name}</strong>
              <Score score={itemResult?.total?.score} maxScore={itemResult?.total?.maxScore} />
              {item.submission ? <StatusBadge status={itemStatus} />
                : <UploadButton studentName={item.student.name} onClick={(event) => { event.stopPropagation(); onUpload(item) }} />}
            </div>
            <RemoveButton className="carousel-remove" label={'Удалить ученика ' + item.student.name}
              title="Удалить ученика" onClick={() => onRemove(item)} />
          </div>
        })}
      </div>
      <button type="button" className="carousel-arrow" aria-label="Следующий ученик" disabled={index === rows.length - 1} onClick={() => go(index + 1)}>›</button>
    </div>
    <p className="carousel-counter subtle">{index + 1} из {rows.length}</p>

    <div className="student-stats" aria-live="polite" key={row.student.studentId}>
      <div className="student-stats-head">
        <div>
          <h2>{row.student.name}</h2>
          <StatusBadge status={status} />
        </div>
        <div className="toolbar">
          {result?.publishedResultRev != null && <button type="button" className="button" onClick={() => onFeedback(row)}>Отзыв .md ↓</button>}
          {row.submission && <button type="button" className="button primary" onClick={() => onOpen(row.student.studentId)}>Открыть работу</button>}
        </div>
      </div>
      {!row.submission ? <p className="subtle">Работа ещё не сдана.</p> : !result ? <p className="subtle">Статистика появится после проверки.</p> : <>
        <Metrics items={[
          { value: scoreText(total?.score, total?.maxScore), label: 'Баллы' + (share === undefined ? '' : ' · ' + Math.round(share * 100) + '%') },
          { value: grade == null ? '—' : grade, label: 'Оценка' },
          { value: counts.correct + ' из ' + result.taskResults.length, label: 'Верно решено' },
        ]} />
        {share !== undefined && <ProgressBar share={share} label={'Выполнено на ' + Math.round(share * 100) + '%'} />}
        <p className="subtle stats-counts">Неверно · {counts.incorrect} · Пропущено · {counts.skipped} · Неразборчиво · {counts.unreadable}</p>
        <ul className="student-tasks">{tasks.map((task) => {
          const cell = taskCell(row.check, task.id)
          const answer = result.taskResults.find((item) => item.taskId === task.id)?.answer
          const part = cell.maxScore && cell.score != null ? cell.score / cell.maxScore : undefined
          return <li key={task.id}>
            <strong>№ {task.number}</strong>
            <div>
              <div className="task-line">
                {answer ? <StatusBadge status={answers[answer]} /> : <span className="subtle">Нет данных</span>}
                <Score score={cell.score} maxScore={cell.maxScore} empty="" />
              </div>
              {part !== undefined && <ProgressBar share={part} thin />}
              {cell.comment && <p className="cell-comment">{cell.comment}</p>}
            </div>
          </li>
        })}</ul>
      </>}
    </div>
  </section>
}
