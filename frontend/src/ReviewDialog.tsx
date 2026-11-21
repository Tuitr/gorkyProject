import { Fragment, useState } from 'react'
import { api, ApiError, type Grade, type PhotoMark, type ReviewChange } from './api'
import { Dialog, ErrorText, errorText } from './Dialogs'
import { categories, exportFeedback, number, ready, rowStatus, taskNumber, type Row } from './workspace'

export function ReviewDialog({ row, previous, next, go, onClose, reload, refresh }: {
  row: Row; previous?: string; next?: string; go: (id: string) => void; onClose: () => void
  reload: (id: string) => Promise<unknown>; refresh: () => void
}) {
  const [pageIndex, setPageIndex] = useState(0)
  const [active, setActive] = useState<PhotoMark>()
  const [feedback, setFeedback] = useState<string>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const check = row.check
  const result = ready(check)
  const checkId = row.submission?.checkId
  const pages = check?.photoPages ?? []
  const page = pages.find((item) => item.pageIndex === pageIndex) ?? pages[0]
  const marks = result?.photoMarks ?? []
  const shown = marks.filter((mark) => mark.pageIndex === page?.pageIndex)
  const transcript = page?.transcriptMarkdown ?? (pages.length < 2 ? check?.transcriptMarkdown : undefined)
  const text = feedback ?? result?.overallReview ?? ''
  const dirty = feedback !== undefined && feedback.trim() !== result?.overallReview
  const status = rowStatus(row)

  function navigate(target: string) {
    if (!dirty || window.confirm('Перейти к другому ученику без сохранения текста?')) go(target)
  }
  function close() {
    if (!dirty || window.confirm('Закрыть окно без сохранения текста?')) onClose()
  }

  async function change(changes: ReviewChange[]) {
    if (!result || !checkId) return
    setBusy(true)
    setError('')
    try {
      await api.editReview(checkId, result.resultRev, crypto.randomUUID(), changes)
      await reload(checkId)
      if (changes.some((item) => item.type === 'update_overall_review')) setFeedback(undefined)
      refresh()
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        await reload(checkId)
        setError('Результат обновился. Проверьте новую версию и повторите сохранение.')
      } else setError(errorText(cause))
    } finally { setBusy(false) }
  }

  async function publish() {
    if (!result || !checkId) return
    setBusy(true)
    setError('')
    try {
      await api.publish(checkId, result.resultRev)
      await reload(checkId)
      refresh()
    } catch (cause) { setError(errorText(cause)); await reload(checkId) }
    finally { setBusy(false) }
  }

  const selected = (mark: PhotoMark) => active?.taskId === mark.taskId && active.issueId === mark.issueId
  function show(mark: PhotoMark) {
    setPageIndex(mark.pageIndex)
    setActive(mark)
  }

  return <Dialog title={row.student.name} onClose={busy ? () => {} : close} wide>
    <div className="review-content">
      <section className="review-source" aria-label="Работа ученика">
        <div className="section-line"><h3>Работа ученика</h3><span className={'status ' + status.tone}>{status.label}</span></div>
        {!page && !transcript && <p className="empty-inline">{checkId ? 'Скан работы недоступен.' : 'Ученик ещё не сдал работу.'}</p>}
        {pages.length > 1 && <div className="page-picker" aria-label="Листы работы">
          {pages.map((item) => <button className="button compact" type="button" key={item.pageIndex}
            aria-pressed={item.pageIndex === page?.pageIndex} onClick={() => { setPageIndex(item.pageIndex); setActive(undefined) }}>
            Лист {item.pageIndex + 1}
          </button>)}
        </div>}
        {page && <div className="scan">
          <img src={page.url} alt={'Лист ' + (page.pageIndex + 1) + ' работы ученика'} />
          {shown.map((mark) => mark.box && <button type="button" key={mark.taskId + '/' + mark.issueId}
            aria-label={'Замечание к заданию № ' + taskNumber(mark.taskId) + ', строка ' + (mark.lineIndex + 1)}
            className={'scan-mark' + (selected(mark) ? ' mark-active' : '')}
            style={{ left: 100 * mark.box.x + '%', top: 100 * mark.box.y + '%', width: 100 * mark.box.w + '%', height: 100 * mark.box.h + '%' }}
            onClick={() => show(mark)} />)}
        </div>}
        {transcript && <details className="transcript" open={active && !active.box ? true : undefined}>
          <summary>Расшифровка</summary>
          <pre>{transcript.split('\n').map((line, index) => <span key={index}
            className={active?.pageIndex === page?.pageIndex && active?.lineIndex === index ? 'active-line' : undefined}>
            {line.split(/~~(.*?)~~/g).map((part, i) => i % 2 ? <s key={i}>{part}</s> : <Fragment key={i}>{part}</Fragment>)}
            {'\n'}
          </span>)}</pre>
        </details>}
      </section>
      <section className="review-detail" aria-label="Разбор и обратная связь">
        <h3>Разбор ошибок</h3>
        {check?.ocrReason && <p className="hint">{check.ocrReason}</p>}
        {!result && <p className="empty-inline">{checkId ? 'Разбор ещё недоступен. Можно посмотреть оригинал.' : 'Разбор появится после проверки.'}</p>}
        {result?.taskResults.map((task) => {
          const score = result.taskAssessments?.find((item) => item.taskId === task.taskId)
          return <article key={task.taskId} className="review-task">
            <div className="section-line"><strong>Задание № {taskNumber(task.taskId)}</strong>
              {score && <span className="score">{score.score == null ? 'На проверке' : number(score.score) + ' / ' + number(score.maxScore)}</span>}</div>
            {score && <p className="subtle">{score.comment}</p>}
            {task.issues.map((issue) => {
              const mark = marks.find((item) => item.taskId === task.taskId && item.issueId === issue.issueId)
              return <div className={'issue' + (mark && selected(mark) ? ' issue-active' : '')} key={issue.issueId}>
                {mark ? <button className="text-button" type="button" onClick={() => show(mark)}>
                  {categories[issue.category] ?? 'Замечание'} · лист {mark.pageIndex + 1}
                </button> : <strong>{categories[issue.category] ?? 'Замечание'}</strong>}
                <p>{issue.comment}</p><p className="subtle">{issue.correction}</p>
              </div>
            })}
          </article>
        })}
        {result && <section className="review-feedback">
          <div className="section-line"><h3>Оценка</h3><span className="subtle">Сохраняется сразу</span></div>
          <div className="grade-picker" aria-label="Оценка">
            {([2, 3, 4, 5] satisfies Grade[]).map((grade) => <button type="button" key={grade} className="button"
              aria-label={'Оценка ' + grade} aria-pressed={result.grade === grade} disabled={busy}
              onClick={() => void change([{ type: 'set_grade', grade: result.grade === grade ? null : grade }])}>{grade}</button>)}
          </div>
          <label className="feedback-label">Индивидуальный отзыв
            <textarea rows={5} value={text} onChange={(event) => setFeedback(event.target.value)} />
          </label>
          {result.recommendations?.length ? <div className="recommendations"><strong>Темы для повторения</strong>
            <ul>{result.recommendations.map((item, index) => <li key={index}><strong>{item.topic}.</strong> {item.text}</li>)}</ul></div> : null}
          <div className="review-buttons">
            <button className="button" type="button" disabled={busy || !dirty || !text.trim()}
              onClick={() => void change([{ type: 'update_overall_review', overallReview: text.trim() }])}>Сохранить текст</button>
            <button className="button primary" type="button" disabled={busy || dirty || result.publishedResultRev === result.resultRev}
              onClick={() => void publish()}>{result.publishedResultRev === result.resultRev ? 'Опубликовано' : 'Опубликовать'}</button>
            {result.publishedResultRev != null && <button className="button" type="button"
              onClick={() => void exportFeedback(result, row.student).catch((cause) => setError(errorText(cause)))}>Скачать .md</button>}
          </div>
        </section>}
        <ErrorText message={error} />
      </section>
    </div>
    <div className="review-navigation">
      <button className="button" disabled={!previous || busy} onClick={() => previous && navigate(previous)}>← Предыдущий</button>
      <button className="button" disabled={!next || busy} onClick={() => next && navigate(next)}>Следующий →</button>
    </div>
  </Dialog>
}
