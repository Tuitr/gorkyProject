import { useState, type CSSProperties } from 'react'
import type { Assignment, Check, Report } from './api'
import { TasksDialog } from './TasksDialog'
import { folderName } from './titles'
import { isApproved } from './useChecks'

// Тенденции класса по каждому заданию. Учитываются только работы, отмеченные галочкой учителя.
export function ClassStats(props: {
  assignments: Assignment[]
  reports: Record<string, Report>
  checks: Record<string, Check>
  error?: string
  loading: boolean
}) {
  const { reports, error, loading } = props
  const shown = props.assignments

  if (shown.length === 0)
    return <p style={st.muted}>В классе пока нет заданий — загрузите задание через «Загрузить файлы».</p>

  return (
    <div>
      {error && <p style={st.error}>{error}</p>}
      {loading && Object.keys(reports).length === 0 && <p style={st.muted}>Загружаем статистику…</p>}
      {shown.map((a) => {
        const r = reports[a.assignmentId]
        if (!r) return null
        return <AssignmentStats key={a.assignmentId} a={a} r={r} checks={props.checks} />
      })}
    </div>
  )
}

function AssignmentStats({ a, r, checks }: { a: Assignment; r: Report; checks: Record<string, Check> }) {
  // Только работы с галочкой учителя (результат опубликован).
  const approved = r.results.map((x) => checks[x.checkId]).filter(isApproved)
  const taskIds = r.tasks.length ? r.tasks.map((t) => t.taskId) : (approved[0]?.taskResults.map((t) => t.taskId) ?? [])
  const tasks = taskIds.map((taskId) => {
    const results = approved.flatMap((c) => c.taskResults.filter((t) => t.taskId === taskId))
    const correct = results.filter((t) => t.answer === 'correct').length
    return { taskId, correct, total: results.length, share: results.length ? correct / results.length : 0 }
  })
  const answered = tasks.reduce((n, t) => n + t.total, 0)
  const correct = tasks.reduce((n, t) => n + t.correct, 0)
  const avg = answered ? Math.round((correct / answered) * 100) + '%' : '—'
  // Типичные ошибки: сколько учеников допустили ошибку этой категории.
  const issues = new Map<string, number>()
  for (const c of approved)
    for (const cat of new Set(c.taskResults.flatMap((t) => t.issues.map((i) => i.category))))
      issues.set(cat, (issues.get(cat) ?? 0) + 1)
  const topIssues = [...issues].sort((x, y) => y[1] - x[1]).slice(0, 5)
  const unapproved = r.results.length - approved.length
  const [tasksOpen, setTasksOpen] = useState(false)

  return (
    <div style={st.block}>
      <div style={st.blockHead}>
        <h3 style={st.h3}>
          {folderName(a)}
        </h3>
        <button onClick={() => setTasksOpen(true)} style={st.tasksButton}>
          Задания
        </button>
      </div>
      {tasksOpen && <TasksDialog a={a} onClose={() => setTasksOpen(false)} />}

      <div style={st.tiles}>
        <Tile value={`${r.submittedCount}/${r.students.length}`} label="сдали работу" />
        <Tile value={String(approved.length)} label="проверено учителем" />
        <Tile value={avg} label="верных ответов" />
      </div>

      {approved.length === 0 && (
        <p style={st.muted}>Отметьте галочкой проверенные работы — тенденции считаются только по ним.</p>
      )}

      {approved.length > 0 && tasks.length > 0 && (
        <>
          <div style={st.caption}>Доля верных ответов по заданиям</div>
          {tasks.map((t, i) => {
            const share = t.share
            return (
              <div key={t.taskId} style={st.barRow}>
                <span style={{ width: 28 }}>№{i + 1}</span>
                <span style={st.bar}>
                  <span style={{ ...st.fill, width: `${Math.round(share * 100)}%`, background: share >= 0.7 ? '#16a34a' : share >= 0.4 ? '#d97706' : '#dc2626' }} />
                </span>
                <span style={{ width: 120, textAlign: 'right', color: '#6b7280', fontSize: 13 }}>
                  {t.correct} из {t.total} · {Math.round(share * 100)}%
                </span>
              </div>
            )
          })}
        </>
      )}

      {topIssues.length > 0 && (
        <>
          <div style={st.caption}>Типичные ошибки (учеников)</div>
          <ul style={st.list}>
            {topIssues.map(([c, n]) => (
              <li key={c} style={st.issue}>
                <span>{c}</span>
                <strong>{n}</strong>
              </li>
            ))}
          </ul>
        </>
      )}

      {unapproved > 0 && <p style={st.muted}>Не отмечено учителем и не учитывается: {unapproved}</p>}
      {r.waiting.length > 0 && <p style={st.muted}>Без результата проверки: {r.waiting.map((s) => s.name).join(', ')}</p>}
      {r.notSubmitted.length > 0 && <p style={st.muted}>Не сдали: {r.notSubmitted.map((s) => s.name).join(', ')}</p>}
    </div>
  )
}

function Tile({ value, label }: { value: string; label: string }) {
  return (
    <div style={st.tile}>
      <div style={{ fontSize: 22, fontWeight: 700 }}>{value}</div>
      <div style={{ fontSize: 13, color: '#6b7280' }}>{label}</div>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  muted: { color: '#6b7280', margin: '6px 0' },
  error: { color: '#b91c1c', margin: '6px 0' },
  block: { border: '1px solid #e5e7eb', borderRadius: 8, padding: 12, marginBottom: 12 },
  blockHead: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 10 },
  h3: { margin: 0, fontSize: 16 },
  tasksButton: { padding: '4px 12px', font: 'inherit', fontSize: 14, cursor: 'pointer' },
  tiles: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginBottom: 12 },
  tile: { background: '#f9fafb', borderRadius: 8, padding: '10px 12px' },
  caption: { fontSize: 13, color: '#6b7280', margin: '8px 0 6px' },
  barRow: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 },
  bar: { flex: 1, height: 10, background: '#f3f4f6', borderRadius: 5, overflow: 'hidden' },
  fill: { display: 'block', height: '100%' },
  list: { listStyle: 'none', margin: 0, padding: 0 },
  issue: { display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #f3f4f6' },
}
