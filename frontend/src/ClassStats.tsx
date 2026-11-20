import { useEffect, useState, type CSSProperties } from 'react'
import { api, type Assignment, type Report } from './api'

// Статистика выбранного класса: отчёт кабинета по каждому варианту.
export function ClassStats(props: { assignments: Assignment[]; refreshKey: number }) {
  const shown = props.assignments
  const [reports, setReports] = useState<Record<string, Report>>({})
  const [error, setError] = useState<string>()
  const [loading, setLoading] = useState(false)

  const ids = shown.map((a) => a.assignmentId + ':' + a.assignmentRev).join(',')

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const load = async () => {
      setLoading(true)
      try {
        const list = await Promise.all(shown.map((a) => api.report(a)))
        if (!alive) return
        setReports(Object.fromEntries(list.map((r) => [r.assignmentId, r])))
        setError(undefined)
        // Пока работы проверяются, обновляем статистику сами.
        if (list.some((r) => r.waiting.length > 0)) timer = window.setTimeout(load, 10000)
      } catch (e) {
        if (alive) setError((e as Error).message)
      } finally {
        if (alive) setLoading(false)
      }
    }
    setReports({})
    if (shown.length) load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, props.refreshKey])

  if (shown.length === 0)
    return <p style={st.muted}>В классе пока нет заданий — загрузите задание через «Загрузить файлы».</p>

  return (
    <div>
      {error && <p style={st.error}>{error}</p>}
      {loading && Object.keys(reports).length === 0 && <p style={st.muted}>Загружаем статистику…</p>}
      {shown.map((a) => {
        const r = reports[a.assignmentId]
        if (!r) return null
        return <AssignmentStats key={a.assignmentId} a={a} r={r} />
      })}
    </div>
  )
}

function AssignmentStats({ a, r }: { a: Assignment; r: Report }) {
  const answered = r.tasks.reduce((n, t) => n + t.correct + t.incorrect + t.skipped, 0)
  const correct = r.tasks.reduce((n, t) => n + t.correct, 0)
  const avg = answered ? Math.round((correct / answered) * 100) + '%' : '—'
  const issues = new Map<string, number>()
  for (const t of r.tasks) for (const [c, n] of Object.entries(t.issuesByCategory)) issues.set(c, (issues.get(c) ?? 0) + n)
  const topIssues = [...issues].sort((x, y) => y[1] - x[1]).slice(0, 5)

  return (
    <div style={st.block}>
      <h3 style={st.h3}>
        {a.subject === 'physics' ? 'Физика' : 'Математика'} · {a.assignmentDate}
      </h3>

      <div style={st.tiles}>
        <Tile value={`${r.submittedCount}/${r.students.length}`} label="сдали работу" />
        <Tile value={String(r.results.length)} label="проверено" />
        <Tile value={avg} label="верных ответов" />
      </div>

      {r.tasks.length > 0 && (
        <>
          <div style={st.caption}>Доля верных ответов по заданиям</div>
          {r.tasks.map((t, i) => {
            const share = t.correctShare ?? 0
            return (
              <div key={t.taskId} style={st.barRow}>
                <span style={{ width: 28 }}>№{i + 1}</span>
                <span style={st.bar}>
                  <span style={{ ...st.fill, width: `${Math.round(share * 100)}%`, background: share >= 0.7 ? '#16a34a' : share >= 0.4 ? '#d97706' : '#dc2626' }} />
                </span>
                <span style={{ width: 120, textAlign: 'right', color: '#6b7280', fontSize: 13 }}>
                  {t.correct} из {t.correct + t.incorrect + t.skipped} · {Math.round(share * 100)}%
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

      {r.waiting.length > 0 && <p style={st.muted}>Проверяются: {r.waiting.map((s) => s.name).join(', ')}</p>}
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
  h3: { margin: '0 0 10px', fontSize: 16 },
  tiles: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginBottom: 12 },
  tile: { background: '#f9fafb', borderRadius: 8, padding: '10px 12px' },
  caption: { fontSize: 13, color: '#6b7280', margin: '8px 0 6px' },
  barRow: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 },
  bar: { flex: 1, height: 10, background: '#f3f4f6', borderRadius: 5, overflow: 'hidden' },
  fill: { display: 'block', height: '100%' },
  list: { listStyle: 'none', margin: 0, padding: 0 },
  issue: { display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #f3f4f6' },
}
