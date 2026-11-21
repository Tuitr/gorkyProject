import { useEffect, useState, type CSSProperties } from 'react'
import { api, type Assignment, type Check, type References, type TaskResult } from './api'
import { folderName } from './titles'
import { GradePicker } from './GradePicker'

const ANSWER: Record<TaskResult['answer'], [string, string]> = {
  correct: ['Верно', '#15803d'],
  incorrect: ['Неверно', '#b91c1c'],
  skipped: ['Пропущено', '#6b7280'],
  unreadable: ['Не читается', '#a16207'],
}
const REASONING: Record<string, string> = {
  valid: 'решение верное',
  invalid: 'ошибка в решении',
  not_shown: 'решения нет',
  unknown: 'решение не оценено',
}
const METHOD: Record<string, string> = {
  met: 'способ соблюдён',
  unmet: 'способ не соблюдён',
  unconfirmed: 'способ не подтверждён',
  optional: '',
}
// Категории ошибок из промптов проверки (contracts/v1/grading/prompts).
const CATEGORY: Record<string, string> = {
  formula: 'Формула',
  units: 'Единицы измерения',
  substitution: 'Подстановка',
  arithmetic: 'Вычисления',
  missing_stage: 'Пропущен этап',
  other: 'Другое',
}
const categoryName = (c: string) => CATEGORY[c] ?? c

export type OpenWork = { checkId?: string; studentName: string; assignment: Assignment; photo?: { uploadId: string; fileId: string } }

// Работа ученика: слева фото и ошибки, справа статистика по работе.
export function StudentWork(props: {
  work: OpenWork
  check?: Check
  grade?: number | null
  onGrade: (checkId: string, grade: number | null) => Promise<void>
  onBack: () => void
}) {
  const { work, check } = props
  const [refs, setRefs] = useState<References>()
  const [photoFailed, setPhotoFailed] = useState(false)

  useEffect(() => {
    api.references(work.assignment).then(setRefs, () => undefined)
  }, [work.assignment])

  const taskInfo = (taskId: string, index: number) => {
    const t = refs?.tasks.find((x) => x.taskId === taskId)
    return { number: t?.taskNumber ?? String(index + 1), statement: t?.statement, answer: t?.state === 'ready' ? t.answer : undefined }
  }
  const ready = check && 'taskResults' in check ? check : undefined

  return (
    <div style={st.wrap}>
      <div style={st.bar}>
        <button onClick={props.onBack} style={st.back}>
          ← Назад
        </button>
        <strong>{work.studentName}</strong>
        <span style={st.muted}>· {folderName(work.assignment)}</span>
      </div>

      <div style={st.columns}>
        {/* ---------- слева: фото и ошибки ---------- */}
        <section style={st.half}>
          {check?.photoPages?.length ? (
            check.photoPages.map((page) => (
              <a key={page.pageIndex} href={page.url} target="_blank" rel="noreferrer" title="Открыть лист целиком">
                <img src={page.url} alt={`Работа ${work.studentName}, лист ${page.pageIndex + 1}`} style={st.photo} />
              </a>
            ))
          ) : work.photo && !photoFailed ? (
            <a href={api.photoUrl(work.photo.uploadId, work.photo.fileId)} target="_blank" rel="noreferrer" title="Открыть фото целиком">
              <img
                src={api.photoUrl(work.photo.uploadId, work.photo.fileId)}
                alt={`Работа: ${work.studentName}`}
                style={st.photo}
                onError={() => setPhotoFailed(true)}
              />
            </a>
          ) : (
            <p style={st.note}>
              {work.photo
                ? 'Фото не загрузилось.'
                : 'Фото работы недоступно. Ниже показаны замечания по заданиям.'}
            </p>
          )}

          <h3 style={st.h3}>Ошибки по заданиям</h3>
          {!check && !work.checkId && <p style={st.note}>{UNKNOWN}</p>}
          {!check && work.checkId && <p style={st.muted}>Загружаем результат проверки…</p>}
          {check && !ready && <p style={st.muted}>{pendingText(check)}</p>}
          {ready?.taskResults.map((t, i) => {
            const info = taskInfo(t.taskId, i)
            const [verdict, color] = ANSWER[t.answer]
            return (
              <div key={t.taskId} style={{ ...st.task, borderLeftColor: color }}>
                <div style={st.taskHead}>
                  <strong>Задание {info.number}</strong>
                  <span style={{ color, fontWeight: 600 }}>{verdict}</span>
                  {t.needsReview && <span style={st.review}>нужна ручная проверка</span>}
                </div>
                {info.statement && <div style={st.statement}>{info.statement}</div>}
                {t.issues.length === 0 && t.answer === 'correct' && <div style={st.muted}>Ошибок нет.</div>}
                {t.issues.map((issue, n) => {
                  const cause = issue.causedByIssueId ? t.issues.findIndex((x) => x.issueId === issue.causedByIssueId) : -1
                  return (
                    <div key={issue.issueId} style={st.issue}>
                      <div style={st.issueHead}>
                        <span style={st.category}>{categoryName(issue.category)}</span>
                        <span style={st.muted}>шаг {issue.stepId}</span>
                        {cause >= 0 && <span style={st.muted}>· следствие ошибки {cause + 1}</span>}
                        <span style={{ ...st.muted, marginLeft: 'auto' }}>№{n + 1}</span>
                      </div>
                      <div>{issue.comment}</div>
                      <div style={st.correction}>Правильно: {issue.correction}</div>
                    </div>
                  )
                })}
                {info.answer && t.answer !== 'correct' && <div style={st.muted}>Эталонный ответ: {info.answer}</div>}
              </div>
            )
          })}

          {check?.transcriptMarkdown && (
            <>
              <h3 style={st.h3}>Распознанный текст</h3>
              <pre style={st.transcript}>{check.transcriptMarkdown}</pre>
            </>
          )}
        </section>

        {/* ---------- справа: статистика работы ---------- */}
        <section style={st.half}>
          <h3 style={{ ...st.h3, marginTop: 0 }}>Статистика работы</h3>
          {ready ? (
            <WorkStats check={ready} taskInfo={taskInfo} grade={props.grade} onGrade={props.onGrade} />
          ) : (
            <p style={st.muted}>{check ? pendingText(check) : work.checkId ? 'Загружаем…' : 'Статистики нет: результат проверки недоступен.'}</p>
          )}
        </section>
      </div>
    </div>
  )
}

const UNKNOWN =
  'Работа сдана, но результат её проверки здесь недоступен: она загружена в другом браузере или до обновления страницы, ' +
  'а кабинет не отдаёт номер проверки для работ без результата. Если проверка не удалась (например, фото без настроенного ' +
  'распознавания), загрузите ответ заново — тогда здесь будет видно, что с ним.'

function pendingText(c: Check) {
  const reason = 'reason' in c && c.reason ? `: ${c.reason}` : ''
  if (c.state === 'failed') return `Проверка не удалась${reason}. Нужна ручная проверка.`
  if (c.state === 'needs_review') return `Нужна ручная проверка${reason}.`
  return 'Работа ещё проверяется…'
}

function WorkStats(props: {
  check: Extract<Check, { taskResults: unknown }>
  taskInfo: (taskId: string, index: number) => { number: string }
  grade?: number | null
  onGrade: (checkId: string, grade: number | null) => Promise<void>
}) {
  const c = props.check
  const [busy, setBusy] = useState(false)
  const total = c.taskResults.length
  const correct = c.taskResults.filter((t) => t.answer === 'correct').length
  const review = c.state === 'needs_review' || c.taskResults.some((t) => t.needsReview || t.answer === 'unreadable')
  const color = review ? '#a16207' : correct === total ? '#15803d' : '#b91c1c'
  const issues = new Map<string, number>()
  for (const t of c.taskResults) for (const i of t.issues) issues.set(i.category, (issues.get(i.category) ?? 0) + 1)

  return (
    <div>
      <div style={st.scoreRow}>
        <div>
          <div style={{ ...st.score, color }}>
            {correct} из {total}
          </div>
          <div style={st.muted}>заданий выполнено верно</div>
        </div>
        <div style={st.approve}>
          <span style={st.muted}>Оценка учителя</span>
          <GradePicker
            size="large"
            grade={props.grade}
            disabled={busy}
            onChange={async (g) => {
              setBusy(true)
              try {
                await props.onGrade(c.checkId, g)
              } finally {
                setBusy(false)
              }
            }}
          />
        </div>
      </div>
      {review && <p style={{ ...st.note, background: '#fefce8', color: '#a16207' }}>Часть работы распознана неуверенно — проверьте вручную.</p>}

      <table style={st.table}>
        <thead>
          <tr>
            <th style={st.th}>№</th>
            <th style={st.th}>Ответ</th>
            <th style={st.th}>Решение</th>
            <th style={st.th}>Ошибок</th>
          </tr>
        </thead>
        <tbody>
          {c.taskResults.map((t, i) => {
            const [verdict, vColor] = ANSWER[t.answer]
            const method = METHOD[t.method]
            return (
              <tr key={t.taskId}>
                <td style={st.td}>{props.taskInfo(t.taskId, i).number}</td>
                <td style={{ ...st.td, color: vColor, fontWeight: 600 }}>{verdict}</td>
                <td style={{ ...st.td, color: '#4b5563' }}>
                  {REASONING[t.reasoning] ?? t.reasoning}
                  {method ? `, ${method}` : ''}
                </td>
                <td style={st.td}>{t.issues.length || '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {issues.size > 0 && (
        <>
          <div style={st.caption}>Ошибки по видам</div>
          {[...issues].sort((a, b) => b[1] - a[1]).map(([cat, n]) => (
            <div key={cat} style={st.issueRow}>
              <span>{categoryName(cat)}</span>
              <strong>{n}</strong>
            </div>
          ))}
        </>
      )}

      {c.overallReview && (
        <>
          <div style={st.caption}>Общий отзыв</div>
          <p style={st.review2}>{c.overallReview}</p>
        </>
      )}
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  wrap: { flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 },
  bar: { display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px 0' },
  back: { padding: '6px 12px', font: 'inherit', cursor: 'pointer' },
  columns: { flex: 1, display: 'flex', gap: 16, padding: 16, minHeight: 0 },
  half: { flex: 1, minWidth: 0, padding: 20, overflowY: 'auto', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 14, boxShadow: 'var(--shadow)' },
  photo: { width: '100%', borderRadius: 8, border: '1px solid #e5e7eb', display: 'block' },
  note: { margin: 0, padding: '10px 12px', borderRadius: 8, background: '#f9fafb', color: '#6b7280', fontSize: 14 },
  h3: { margin: '20px 0 10px', fontSize: 16 },
  muted: { color: '#6b7280', fontSize: 14 },
  task: { border: '1px solid #e5e7eb', borderLeft: '4px solid', borderRadius: 8, padding: '10px 12px', marginBottom: 10, display: 'flex', flexDirection: 'column', gap: 8 },
  taskHead: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  review: { fontSize: 12, fontWeight: 600, color: '#a16207', background: '#fefce8', borderRadius: 999, padding: '2px 8px' },
  statement: { whiteSpace: 'pre-wrap', fontSize: 14, color: '#374151', background: '#f9fafb', borderRadius: 6, padding: '6px 8px' },
  issue: { background: '#fef2f2', borderRadius: 6, padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 4 },
  issueHead: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  category: { fontSize: 12, fontWeight: 700, color: '#b91c1c', background: '#fee2e2', borderRadius: 999, padding: '2px 8px' },
  correction: { color: '#15803d' },
  transcript: { margin: 0, whiteSpace: 'pre-wrap', font: '13px/1.5 ui-monospace, Consolas, monospace', background: '#f9fafb', borderRadius: 8, padding: 10 },
  scoreRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 },
  score: { fontSize: 32, fontWeight: 800, lineHeight: 1.1 },
  approve: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 },
  table: { width: '100%', borderCollapse: 'collapse', margin: '12px 0', fontSize: 14 },
  th: { textAlign: 'left', color: '#6b7280', fontWeight: 500, fontSize: 13, padding: '6px 8px', borderBottom: '1px solid #e5e7eb' },
  td: { padding: '6px 8px', borderBottom: '1px solid #f3f4f6', verticalAlign: 'top' },
  caption: { fontSize: 13, color: '#6b7280', margin: '14px 0 6px' },
  issueRow: { display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #f3f4f6' },
  review2: { margin: 0, whiteSpace: 'pre-wrap', lineHeight: 1.5 },
}
