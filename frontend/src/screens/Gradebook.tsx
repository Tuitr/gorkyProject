import { useState } from 'react'
import { ErrorPanel } from '../components/ErrorPanel'
import {
  TASK_STATUS_LABEL,
  activeMistakes,
  needsManual,
  submissionTotal,
  taskScore,
  type Assignment,
  type SourceTask,
  type Submission,
  type TaskResult,
} from '../domain'
import { href } from '../router'
import { patch, useStore } from '../store'
import { Badge, Modal } from '../ui'
import { AssignmentHeader, ClassTabs, NotFound, useAssignment } from './common'

/** Экран 8. Ведомость класса: ученики × номера, предварительная сумма, итог учителя, утверждение. */
export function Gradebook({ id, classId }: { id: string; classId?: string }) {
  const { state, update, toast } = useStore()
  const { a, classes } = useAssignment(id)
  const [panel, setPanel] = useState<{ sid: string; tid: string } | null>(null)
  const [criterion, setCriterion] = useState<SourceTask | null>(null)
  const cls = classes.find((c) => c.id === classId) ?? classes[0]
  if (!a) return <NotFound />
  if (!cls)
    return (
      <div className="page">
        <AssignmentHeader a={a} step="sheet" />
        <p className="muted">Работа не назначена ни одному классу.</p>
      </div>
    )

  const subs = state.submissions.filter((s) => s.assignmentId === a.id && s.classId === cls.id)
  const setSub = (sid: string, fn: (s: Submission) => Submission) =>
    update((st) => ({ ...st, submissions: patch(st.submissions, sid, fn) }))
  const ready = subs.filter((s) => s.status === 'processed' && !s.approved && !submissionTotal(a, s).preliminary)

  return (
    <div className="page page--wide">
      <AssignmentHeader a={a} step="sheet" classId={cls.id} />
      <ClassTabs classes={classes} value={cls.id} a={a} step="sheet" />

      <div className="toolbar">
        <span className="muted small">
          Нажмите на ячейку, чтобы открыть сведения об ошибках; на заголовок задания — чтобы изменить критерии оценивания.
        </span>
        <span className="spacer" />
        <button
          className="btn btn--primary"
          disabled={!ready.length}
          onClick={() => {
            update((st) => ({
              ...st,
              submissions: st.submissions.map((s) => (ready.some((r) => r.id === s.id) ? { ...s, approved: true } : s)),
            }))
            toast(`Утверждено: ${ready.length}`)
          }}
          title="Утвердить все работы, в которых нет заданий на ручной проверке"
        >
          Утвердить проверенные ({ready.length})
        </button>
      </div>

      <div className="table-wrap">
        <table className="table sheet">
          <thead>
            <tr>
              <th className="sheet__name">Ученик</th>
              <th>Вариант</th>
              {a.tasks.map((t) => (
                <th key={t.id} className="sheet__task">
                  <button className="link-btn" onClick={() => setCriterion(t)} title="Изменить критерий">
                    №{t.num}
                    <span className="muted small"> /{t.max}</span>
                    {t.manual && <span className="muted small"> ✋</span>}
                  </button>
                </th>
              ))}
              <th>Предварительный балл</th>
              <th>Итоговый балл</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {cls.students.map((st) => {
              const s = subs.find((x) => x.studentId === st.id)
              const v = a.variants.find((x) => x.id === s?.variantId)
              if (!s || s.status === 'none' || !s.pages.length)
                return (
                  <tr key={st.id} className="is-muted">
                    <td className="sheet__name">{st.name}</td>
                    <td>—</td>
                    <td colSpan={a.tasks.length}>
                      <Badge>Не сдано</Badge>
                    </td>
                    <td>—</td>
                    <td />
                    <td />
                  </tr>
                )
              if (s.status !== 'processed')
                return (
                  <tr key={st.id}>
                    <td className="sheet__name">{st.name}</td>
                    <td>{v?.name ?? '—'}</td>
                    <td colSpan={a.tasks.length}>
                      {s.status === 'error' ? (
                        <Badge tone="err">Ошибка проверки</Badge>
                      ) : (
                        <Badge tone="info">{s.status === 'processing' ? 'На проверке' : 'Сдано, не проверено'}</Badge>
                      )}
                    </td>
                    <td>—</td>
                    <td />
                    <td>
                      <a className="link-btn" href={href('a', a.id, 'collect', { c: cls.id })}>
                        К сбору работ
                      </a>
                    </td>
                  </tr>
                )
              const total = submissionTotal(a, s)
              return (
                <tr key={st.id} className={s.approved ? 'is-approved' : ''}>
                  <td className="sheet__name">
                    <a href={href('a', a.id, 'report', s.id)}>{st.name}</a>
                  </td>
                  <td>{v?.name ?? '—'}</td>
                  {a.tasks.map((t) => (
                    <td key={t.id} className="sheet__cell">
                      <Cell t={t} r={s.results.find((x) => x.taskId === t.id)} onOpen={() => setPanel({ sid: s.id, tid: t.id })} />
                    </td>
                  ))}
                  <td>
                    <b>
                      {total.sum}/{total.max}
                    </b>
                    {total.preliminary && (
                      <div>
                        <Badge tone="warn" title="Есть задания на ручной проверке">
                          предварительная
                        </Badge>
                      </div>
                    )}
                  </td>
                  <td>
                    <input
                      className="input input--num"
                      type="number"
                      min={0}
                      value={s.teacherTotal ?? ''}
                      placeholder={String(total.sum)}
                      onChange={(e) =>
                        setSub(s.id, (x) => ({
                          ...x,
                          teacherTotal: e.target.value === '' ? undefined : Number(e.target.value),
                          approved: false,
                        }))
                      }
                      aria-label={`Итоговый балл: ${st.name}`}
                    />
                  </td>
                  <td>
                    {s.approved ? (
                      <button className="link-btn" onClick={() => setSub(s.id, (x) => ({ ...x, approved: false }))}>
                        ✓ Утверждено
                      </button>
                    ) : (
                      <button
                        className="btn btn--sm"
                        onClick={() => {
                          if (total.preliminary && s.teacherTotal == null && !confirm('Есть задания на ручной проверке. Утвердить предварительный балл?'))
                            return
                          setSub(s.id, (x) => ({ ...x, approved: true }))
                        }}
                      >
                        Утвердить
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="muted small legend">
        <Badge tone="ok">1/1</Badge> полученный балл из максимального · <Badge tone="warn">ручная</Badge> требует оценки учителя ·{' '}
        <Badge>не сдано</Badge> и <Badge tone="warn">неразборчиво</Badge> не оцениваются как 0 баллов · ⚠ вариант распознавания выбран
        автоматически
      </p>

      {panel && <ErrorPanel submissionId={panel.sid} taskId={panel.tid} onClose={() => setPanel(null)} />}
      {criterion && <CriterionModal a={a} task={criterion} onClose={() => setCriterion(null)} />}
    </div>
  )
}

function Cell({ t, r, onOpen }: { t: SourceTask; r?: TaskResult; onOpen: () => void }) {
  const v = taskScore(r)
  if (!r) return <span className="muted">—</span>
  const label =
    v != null
      ? `${v}/${t.max}`
      : r.status === 'manual'
        ? 'ручная'
        : r.status === 'unreadable'
          ? 'неразборчиво'
          : r.status === 'missing'
            ? 'не сдано'
            : TASK_STATUS_LABEL[r.status]
  const tone = v == null ? (r.status === 'missing' ? 'muted' : 'warn') : v >= t.max ? 'ok' : v > 0 ? 'warn' : 'err'
  return (
    <button className={`cell cell--${tone}`} onClick={onOpen} title={TASK_STATUS_LABEL[r.status]}>
      {label}
      {r.score != null && <sup title="Балл изменён учителем">✎</sup>}
      {(r.autoReading || needsManual(r)) && v != null && <sup title={r.autoReading ?? 'на ручной проверке'}>⚠</sup>}
      {activeMistakes(r).length > 0 && <span className="cell__n">{activeMistakes(r).length}</span>}
    </button>
  )
}

/** Смена критерия: какие работы затронет, подтверждение пересчёта. */
function CriterionModal({ a, task, onClose }: { a: Assignment; task: SourceTask; onClose: () => void }) {
  const { state, update, toast } = useStore()
  const [max, setMax] = useState(task.max)
  const [manual, setManual] = useState(task.manual)
  const changed = max !== task.max || manual !== task.manual

  const affected = state.submissions.filter(
    (s) => s.assignmentId === a.id && s.status === 'processed' && s.results.some((r) => r.taskId === task.id && r.status !== 'missing'),
  )
  const name = (sid: string | null) => state.classes.flatMap((c) => c.students).find((x) => x.id === sid)?.name ?? 'не определён'

  const recalc = (r: TaskResult): TaskResult => {
    if (r.taskId !== task.id) return r
    const k = task.max ? max / task.max : 1
    const next: TaskResult = {
      ...r,
      aiScore: r.aiScore == null ? undefined : Math.round(r.aiScore * k * 2) / 2,
      score: r.score == null ? undefined : Math.min(max, r.score),
    }
    if (manual && !task.manual) next.status = 'manual'
    if (!manual && task.manual && r.status === 'manual') next.status = activeMistakes(r).length ? 'error' : 'ok'
    return next
  }

  const apply = () => {
    update((st) => ({
      ...st,
      assignments: patch(st.assignments, a.id, (x) => ({
        ...x,
        tasks: x.tasks.map((t) => (t.id === task.id ? { ...t, max, manual } : t)),
      })),
      submissions: st.submissions.map((s) =>
        affected.some((x) => x.id === s.id) ? { ...s, results: s.results.map(recalc), approved: false } : s,
      ),
    }))
    toast(`Пересчитано работ: ${affected.length}`)
    onClose()
  }

  return (
    <Modal
      title={`Критерии оценивания задания №${task.num}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn btn--primary" disabled={!changed} onClick={apply}>
            Подтвердить пересчёт ({affected.length})
          </button>
        </>
      }
    >
      <label className="field field--inline">
        <span>Максимальный балл</span>
        <input className="input input--num" type="number" min={0} value={max} onChange={(e) => setMax(Math.max(0, Number(e.target.value) || 0))} />
      </label>
      <label className="check">
        <input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} /> Ручная проверка (балл выставляет учитель)
      </label>
      {changed && (
        <div className="affected">
          <h3>Затронутые работы · {affected.length}</h3>
          {affected.length ? (
            <ul>
              {affected.map((s) => {
                const r = s.results.find((x) => x.taskId === task.id)!
                const before = taskScore(r)
                const after = taskScore(recalc(r))
                return (
                  <li key={s.id}>
                    {name(s.studentId)}: {before ?? '—'} → <b>{after ?? 'ручная'}</b>
                    {s.approved && <Badge tone="warn">утверждение будет отменено</Badge>}
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="muted">Проверенных работ с этим заданием нет.</p>
          )}
        </div>
      )}
    </Modal>
  )
}
