import type { CSSProperties } from 'react'
import type { Assignment, Check, Report, Student, Upload, UploadFile } from './api'
import { StudentAnswer } from './StudentAnswer'
import { subjectDate, titleOf } from './titles'

// Загрузка из этого сеанса: файл отправляется, не ушёл, или принят кабинетом (uploadId).
export type Work = {
  key: string
  name: string
  state: 'sending' | 'failed' | 'sent'
  error?: string
  uploadId?: string
  /** Папка (задание), куда ушло решение. Нет — это загрузка самого задания. */
  assignmentId?: string
}

const STATUS: Record<UploadFile['status'], [string, string]> = {
  pending_binding: ['Определяем ученика…', '#2563eb'],
  matched: ['Ученик найден', '#2563eb'],
  needs_confirmation: ['Укажите ученика', '#b45309'],
  bound: ['Принято', '#15803d'],
  rejected: ['Отклонено', '#b91c1c'],
}

// Левая часть: каждое задание — папка, в ней решения учеников этого задания.
export function WorkFolders(props: {
  assignments: Assignment[]
  reports: Record<string, Report>
  works: Work[]
  uploads: Record<string, Upload>
  students: Student[]
  open: Set<string>
  onToggle: (assignmentId: string) => void
  onBind: (uploadId: string, fileId: string, studentId: string) => void
  onAddAnswer: (assignmentId: string) => void
  checks: Record<string, Check>
  onApprove: (checkId: string, resultRev: number) => Promise<void>
}) {
  const { works, uploads } = props
  const loose = works.filter((w) => !w.assignmentId) // загрузки заданий: отправляются или не ушли

  return (
    <div>
      {loose.length > 0 && (
        <ul style={st.list}>
          {loose.map((w) => (
            <li key={w.key} style={st.item}>
              <span style={st.name}>Вариант: {w.name}</span>
              {w.state === 'sending' ? <span style={st.muted}>Загружаем…</span> : <span style={st.error}>{w.error}</span>}
            </li>
          ))}
        </ul>
      )}

      {props.assignments.length === 0 && loose.length === 0 && (
        <p style={st.muted}>Нажмите «Загрузить файлы» и загрузите вариант проверочной работы — для него появится папка для ответов учеников.</p>
      )}

      {props.assignments.map((a) => {
        const report = props.reports[a.assignmentId]
        const sessionWorks = works.filter((w) => w.assignmentId === a.assignmentId)
        // Из сеанса показываем то, что ещё не стало сдачей ученика (сдачи берём из отчёта).
        const pendingRows = sessionWorks.flatMap((w) => {
          if (w.state !== 'sent') return [{ w, f: undefined as UploadFile | undefined }]
          const files = uploads[w.uploadId!]?.files
          if (!files) return [{ w, f: undefined }]
          return files.filter((f) => f.status !== 'bound').map((f) => ({ w, f }))
        })
        const checked = new Set(report?.results.map((r) => r.studentId))
        // Проверка ученика: из отчёта, а если результата там ещё нет — из загрузки этого сеанса.
        const checkOf = new Map<string, string>()
        for (const w of sessionWorks)
          for (const f of (w.uploadId && uploads[w.uploadId]?.files) || [])
            if (f.studentId && f.checkId) checkOf.set(f.studentId, f.checkId)
        for (const r of report?.results ?? []) checkOf.set(r.studentId, r.checkId)
        const waiting = new Set(report?.waiting.map((s) => s.studentId))
        const submitted = (report?.students ?? []).filter((s) => checked.has(s.studentId) || waiting.has(s.studentId))
        const count = submitted.length + pendingRows.length
        const isOpen = props.open.has(a.assignmentId)

        return (
          <div key={a.assignmentId} style={st.folder}>
            <button style={st.folderHead} onClick={() => props.onToggle(a.assignmentId)} aria-expanded={isOpen}>
              <span style={{ width: 14, color: '#6b7280' }}>{isOpen ? '▾' : '▸'}</span>
              <span style={{ fontSize: 18 }}>{isOpen ? '📂' : '📁'}</span>
              <span style={{ fontWeight: 600 }}>{titleOf(a) ?? subjectDate(a)}</span>
              {titleOf(a) && <span style={{ fontSize: 13, color: '#6b7280' }}>{subjectDate(a)}</span>}
              <span style={st.count}>{count ? `ответов: ${count}` : 'пусто'}</span>
            </button>

            {isOpen && (
              <ul style={{ ...st.list, padding: '8px 8px 0 30px' }}>
                {count === 0 && <li style={{ ...st.muted, paddingBottom: 8 }}>Ответов пока нет.</li>}

                {pendingRows.map(({ w, f }) => (
                  <li key={w.key + (f?.fileId ?? '')} style={st.item}>
                    <div style={st.col}>
                      <span style={st.name}>{f?.originalName ?? w.name}</span>
                      {f?.reason && <span style={{ ...st.muted, fontSize: 13 }}>{f.reason}</span>}
                      {f?.status === 'needs_confirmation' && (
                        <select
                          defaultValue=""
                          onChange={(e) => e.target.value && props.onBind(w.uploadId!, f.fileId, e.target.value)}
                          style={st.input}
                        >
                          <option value="" disabled>
                            Чья это работа?
                          </option>
                          {[...f.candidates, ...props.students.filter((x) => !f.candidates.some((c) => c.studentId === x.studentId))].map((x) => (
                            <option key={x.studentId} value={x.studentId}>
                              {x.name}
                            </option>
                          ))}
                        </select>
                      )}
                    </div>
                    {w.state === 'sending' ? (
                      <span style={st.muted}>Отправляем…</span>
                    ) : w.state === 'failed' ? (
                      <span style={st.error}>{w.error}</span>
                    ) : f ? (
                      <span style={{ ...st.status, color: STATUS[f.status][1] }}>{STATUS[f.status][0]}</span>
                    ) : (
                      <span style={st.muted}>Обрабатываем…</span>
                    )}
                  </li>
                ))}

                {submitted
                  .slice()
                  .sort((x, y) => x.name.localeCompare(y.name, 'ru'))
                  .map((s) => (
                    <li key={s.studentId}>
                      <StudentAnswer
                        name={s.name}
                        check={props.checks[checkOf.get(s.studentId) ?? '']}
                        onApprove={props.onApprove}
                      />
                    </li>
                  ))}

                <li style={{ marginBottom: 8 }}>
                  <button style={st.addAnswer} onClick={() => props.onAddAnswer(a.assignmentId)}>
                    + Добавить ответ ученика
                  </button>
                </li>
              </ul>
            )}
          </div>
        )
      })}
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  list: { listStyle: 'none', margin: 0, padding: 0 },
  item: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, padding: '8px 12px', marginBottom: 8, border: '1px solid #e5e7eb', borderRadius: 8 },
  col: { display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 },
  name: { wordBreak: 'break-all' },
  muted: { color: '#6b7280', margin: 0 },
  error: { color: '#b91c1c', fontSize: 14 },
  status: { fontSize: 13, whiteSpace: 'nowrap' },
  input: { padding: '7px 10px', font: 'inherit', border: '1px solid #d1d5db', borderRadius: 6, background: '#fff' },
  folder: { marginBottom: 6 },
  folderHead: { display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '10px 12px', border: '1px solid #e5e7eb', borderRadius: 8, background: '#f9fafb', font: 'inherit', cursor: 'pointer', textAlign: 'left' },
  addAnswer: { padding: '6px 12px', font: 'inherit', fontSize: 14, cursor: 'pointer' },
  count: { marginLeft: 'auto', fontSize: 13, color: '#6b7280' },
}
