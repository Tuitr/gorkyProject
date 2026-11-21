import { useEffect, useMemo, useState } from 'react'
import { api, type Report, type Student, type Submission, type Upload, type UploadFile, type Work } from './api'
import { ConfirmDialog, ErrorText, errorText, NameDialog, UploadSolutionsDialog } from './Dialogs'
import { ReviewDialog } from './ReviewDialog'
import { useChecks } from './useChecks'
import { categories, dateLabel, exportFeedback, exportWorkbook, number, ready, rowStatus, subjectName, taskCell, taskNumber, workName, type Row, type TaskColumn } from './workspace'

const uploadKey = (work: Work) => 'teacher-workspace:uploads:' + work.assignmentId + ':' + work.assignmentRev
function loadUploadIds(work: Work): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(uploadKey(work)) ?? '[]')
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
  } catch { return [] }
}

export function WorkView({ work }: { work: Work }) {
  const [mode, setMode] = useState<'table' | 'report'>('table')
  const [students, setStudents] = useState<Student[]>([])
  const [submissions, setSubmissions] = useState<Submission[]>([])
  const [report, setReport] = useState<Report>()
  const [taskColumns, setTaskColumns] = useState<TaskColumn[]>([])
  const [referenceState, setReferenceState] = useState('')
  const [error, setError] = useState('')
  const [reportError, setReportError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshKey, setRefreshKey] = useState(0)
  const [uploadIds, setUploadIds] = useState(() => loadUploadIds(work))
  const [uploads, setUploads] = useState<Upload[]>([])
  const [modal, setModal] = useState<'upload' | 'student' | null>(null)
  const [studentId, setStudentId] = useState<string>()
  const [bindTarget, setBindTarget] = useState<{ file: UploadFile; uploadId: string }>()
  const [marked, setMarked] = useState(false)
  const [removeStudent, setRemoveStudent] = useState<Student>()
  const [actionError, setActionError] = useState('')
  const refresh = () => setRefreshKey((value) => value + 1)
  const assignmentId = work.assignmentId
  const assignmentRev = work.assignmentRev
  const classId = work.classId

  useEffect(() => {
    let disposed = false
    let timer = 0
    async function load() {
      const items = await Promise.allSettled([
        api.students(classId), api.submissions(work), api.report(work), api.references(work),
      ] as const)
      if (disposed) return
      const [roster, submitted, summary, references] = items
      const failure = roster.status === 'rejected' ? roster.reason : submitted.status === 'rejected' ? submitted.reason : null
      setError(failure ? errorText(failure) : '')
      if (roster.status === 'fulfilled') setStudents(roster.value)
      if (submitted.status === 'fulfilled') setSubmissions(submitted.value.submissions)
      if (summary.status === 'fulfilled') { setReport(summary.value); setReportError('') }
      else setReportError(errorText(summary.reason))
      if (references.status === 'fulfilled') {
        setTaskColumns(references.value.tasks.map((task) => ({ id: task.taskId, number: task.taskNumber })))
        setReferenceState(references.value.state)
      }
      setLoading(false)
      timer = window.setTimeout(() => void load(), 4500)
    }
    void load()
    return () => { disposed = true; clearTimeout(timer) }
  }, [assignmentId, assignmentRev, classId, refreshKey, work])

  useEffect(() => {
    if (!uploadIds.length) return
    let disposed = false
    let timer = 0
    async function readUploads() {
      const results = await Promise.allSettled(uploadIds.map(api.upload))
      if (disposed) return
      const loaded = results.flatMap((item) => item.status === 'fulfilled' ? [item.value] : [])
      setUploads(loaded)
      if (loaded.some((upload) => upload.files.some((file) => ['pending_binding', 'matched'].includes(file.status)))) {
        timer = window.setTimeout(() => void readUploads(), 2500)
      }
    }
    void readUploads()
    return () => { disposed = true; clearTimeout(timer) }
  }, [uploadIds, refreshKey])

  function saveUploadIds(ids: string[]) {
    setUploadIds(ids)
    try { localStorage.setItem(uploadKey(work), JSON.stringify(ids)) } catch { /* The current import remains in view. */ }
  }

  const { checks, reload } = useChecks(submissions.map((item) => item.checkId))
  const rows = useMemo<Row[]>(() => {
    const byStudent = new Map(submissions.map((item) => [item.studentId, item]))
    return [...students].sort((left, right) => left.name.localeCompare(right.name, 'ru')).map((student) => {
      const submission = byStudent.get(student.studentId)
      return { student, submission, check: submission && checks[submission.checkId] }
    })
  }, [students, submissions, checks])
  const tasks = useMemo(() => {
    const columns = new Map(taskColumns.map((item) => [item.id, item]))
    for (const id of [...(report?.tasks ?? []).map((item) => item.taskId),
      ...rows.flatMap((row) => ready(row.check)?.taskResults.map((task) => task.taskId) ?? [])]) {
      if (!columns.has(id)) columns.set(id, { id, number: taskNumber(id) })
    }
    return [...columns.values()].sort((a, b) => a.number.localeCompare(b.number, 'ru', { numeric: true }))
  }, [taskColumns, report, rows])
  const rowIndex = rows.findIndex((row) => row.student.studentId === studentId)
  const row = rows[rowIndex]
  const pending = uploads.flatMap((upload) => {
    const seen = new Set<string>()
    return upload.files.filter((file) => {
      if (file.status === 'bound' || seen.has(file.groupId ?? file.fileId)) return false
      seen.add(file.groupId ?? file.fileId)
      return true
    }).map((file) => ({ file, uploadId: upload.uploadId }))
  })

  async function bind(uploadId: string, file: UploadFile, student: string) {
    setActionError('')
    try { await api.bind(uploadId, file.fileId, { studentId: student }); refresh() }
    catch (cause) { setActionError(errorText(cause)) }
  }

  async function exportCurrent() {
    setActionError('')
    try { await exportWorkbook(work, rows, tasks, report, mode) }
    catch (cause) { setActionError(errorText(cause)) }
  }

  return <>
    <a className="back-link" href="#">← Все работы</a>
    <div className="page-head work-head">
      <div><div className="work-meta">{work.className} · {subjectName(work.subject)} · {dateLabel(work.assignmentDate)}</div>
        <h1>{workName(work)}</h1></div>
      <button className="button primary" type="button" onClick={() => setModal('upload')}>↑ Загрузить решения</button>
    </div>
    <div className="work-nav">
      <nav className="tabs" aria-label="Режим просмотра">
        <button type="button" className="tab" aria-current={mode === 'table' ? 'page' : undefined} onClick={() => setMode('table')}>Ведомость</button>
        <button type="button" className="tab" aria-current={mode === 'report' ? 'page' : undefined} onClick={() => setMode('report')}>Отчёт класса</button>
      </nav>
      {mode === 'table' && <div className="toolbar">
        <button className="button" type="button" onClick={() => setModal('student')}>+ Добавить ученика</button>
        <button className="button" type="button" onClick={() => setMarked(!marked)} aria-pressed={marked}>Удалить ученика</button>
        <button className="button" type="button" disabled title="Фильтр появится в следующей версии">Фильтр</button>
      </div>}
    </div>
    <ErrorText message={error || actionError} />
    {loading && <p className="empty-inline" role="status">Загружаем ведомость…</p>}
    {['queued', 'running', 'waiting_reference'].includes(referenceState) && <p className="hint" role="status">Готовятся задания и эталонные ответы. Решения уже можно загрузить.</p>}
    {referenceState === 'needs_review' && !taskColumns.length && <p className="notice-error">Условие требует проверки. Проверьте, что на фото видны номера и текст задач.</p>}
    {pending.length > 0 && <section className="pending-box" aria-label="Импорт решений">
      <h2>Импорт решений</h2>
      {pending.map(({ file, uploadId }) => <div className="pending-entry" key={file.fileId}>
        <div><strong>{file.originalName}</strong><p>{file.reason ?? 'Уточняем привязку.'}</p></div>
        {file.status === 'needs_confirmation' && file.reasonCode !== 'invalid_page_order' && <div className="toolbar">
          <select
          aria-label={'Ученик для файла ' + file.originalName} value="" onChange={(event) => void bind(uploadId, file, event.target.value)}>
          <option value="" disabled>Выберите ученика</option>
          {[...file.candidates, ...students.filter((student) => !file.candidates.some((item) => item.studentId === student.studentId))]
            .map((student) => <option key={student.studentId} value={student.studentId}>{student.name}</option>)}
          </select>
          <button type="button" className="button" onClick={() => setBindTarget({ file, uploadId })}>Новый ученик</button>
        </div>}
      </div>)}
    </section>}
    {mode === 'table' ? <div className="table-frame">
      <table>
        <thead><tr><th scope="col">Ученик</th>{tasks.map((task) => <th scope="col" key={task.id}>Задание {task.number}</th>)}
          <th scope="col">Итого</th><th scope="col">Оценка</th><th scope="col">Статус</th><th scope="col">Отзыв</th></tr></thead>
        <tbody>
          {rows.map((item) => {
            const status = rowStatus(item)
            const result = ready(item.check)
            const grade = item.check?.grade === undefined
              ? report?.results.find((entry) => entry.checkId === item.submission?.checkId)?.grade : item.check.grade
            return <tr key={item.student.studentId}>
              <th scope="row">{marked && <button type="button" className="delete-student" aria-label={'Удалить: ' + item.student.name}
                onClick={() => setRemoveStudent(item.student)}>−</button>}
                <button type="button" className="text-button student-link" onClick={() => setStudentId(item.student.studentId)}>{item.student.name}</button></th>
              {tasks.map((task) => {
                const cell = taskCell(item.check, task.id)
                return <td className="task-cell" key={task.id}>
                  <span className="score">{cell.maxScore === undefined ? '—' : (cell.score == null ? '—' : number(cell.score)) + ' / ' + number(cell.maxScore)}</span>
                  {cell.comment && <span className="cell-comment">{cell.comment}</span>}
                </td>
              })}
              <td><span className="score">{result?.total ? number(result.total.score) + ' / ' + number(result.total.maxScore) : '—'}</span></td>
              <td>{grade == null ? '—' : <span className="grade-chip">{grade}</span>}</td>
              <td><span className={'status ' + status.tone}>{status.label}</span></td>
              <td>{result?.publishedResultRev != null ? <button className="text-button" type="button" aria-label={'Скачать отзыв для ' + item.student.name}
                onClick={() => void exportFeedback(result, item.student).catch((cause) => setActionError(errorText(cause)))}>.md ↓</button> : '—'}</td>
            </tr>
          })}
        </tbody>
      </table>
      {!rows.length && !loading && <p className="empty-inline">В классе пока нет учеников. Добавьте ученика или загрузите работы.</p>}
    </div> : <ClassReport report={report} error={reportError} />}
    <div className="page-footer"><button className="button" type="button" onClick={() => void exportCurrent()}
      disabled={mode === 'table' ? !rows.length : !report}>↓ Экспорт в Excel</button></div>

    {modal === 'upload' && <UploadSolutionsDialog work={work} onClose={() => setModal(null)} onUpload={(ids) => {
      saveUploadIds([...new Set([...uploadIds, ...ids])])
      setModal(null)
      refresh()
    }} />}
    {modal === 'student' && <NameDialog title="Добавить ученика" label="Фамилия и имя" placeholder="Смирнова Анна"
      onClose={() => setModal(null)} action={async (name) => { await api.addStudent(classId, name); refresh() }} />}
    {bindTarget && <NameDialog title="Привязать к новому ученику" label="Фамилия и имя" placeholder="Смирнова Анна"
      onClose={() => setBindTarget(undefined)} action={async (name) => {
        await api.bind(bindTarget.uploadId, bindTarget.file.fileId, { newStudent: { name } })
        refresh()
      }} />}
    {removeStudent && <ConfirmDialog title="Удалить ученика?" text={removeStudent.name + ' исчезнет из текущего списка класса.'}
      onClose={() => setRemoveStudent(undefined)} action={async () => { await api.deleteStudent(classId, removeStudent.studentId); refresh() }} />}
    {row && <ReviewDialog key={row.student.studentId} row={row} go={setStudentId} onClose={() => setStudentId(undefined)}
      previous={rows[rowIndex - 1]?.student.studentId} next={rows[rowIndex + 1]?.student.studentId} reload={reload} refresh={refresh} />}
  </>
}

function ClassReport({ report, error }: { report?: Report; error: string }) {
  if (!report) return <div className="report-panel">{error ? <ErrorText message={error} /> : <p className="empty-inline">Отчёт готовится.</p>}</div>
  return <div className="report-panel">
    <ErrorText message={error} />
    <div className="metrics">
      <div><strong>{report.submittedCount}</strong><span>Сдали</span></div>
      <div><strong>{report.waiting.length}</strong><span>Ждут проверки</span></div>
      <div><strong>{report.notSubmitted.length}</strong><span>Не сдали</span></div>
    </div>
    <h2>Что повторить с классом</h2>
    {report.trends?.length ? <div className="trends">{report.trends.map((trend) => {
      const affected = report.tasks.find((task) => task.taskId === trend.taskId)?.issuesByCategory[trend.category] ?? 0
      return <article key={trend.taskId + '/' + trend.category}>
        <h3>{trend.topic}</h3>
        <p>Задание № {taskNumber(trend.taskId)} · {categories[trend.category] ?? trend.category} · {affected} из {report.submittedCount}</p>
        <p className="subtle">{trend.exampleComment}</p>
      </article>
    })}</div> : <p className="subtle">Повторяющихся ошибок в готовых результатах пока нет.</p>}
    <h2>Результаты по заданиям</h2>
    {report.tasks?.length ? <ul className="task-stats">{report.tasks.map((task) => <li key={task.taskId}>
      <strong>№ {taskNumber(task.taskId)}</strong>
      <span>Неверно · {task.incorrect} из {report.submittedCount}{task.incorrectShare == null ? '' : ' (' + Math.round(task.incorrectShare * 100) + '%)'}</span>
      <span className="subtle">Верно · {task.correct} · Пропущено · {task.skipped} · Неразборчиво · {task.unreadable ?? 0}</span>
    </li>)}</ul> : <p className="subtle">Данные появятся после проверки.</p>}
  </div>
}
