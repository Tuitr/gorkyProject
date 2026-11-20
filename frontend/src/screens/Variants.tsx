import { useActions } from '../actions'
import { replaceTask } from '../api/generate'
import type { Assignment, EquivFeature, Variant } from '../domain'
import { href } from '../router'
import { patch, useStore } from '../store'
import { Badge, JobStatus } from '../ui'
import { AssignmentHeader, NotFound, useAssignment } from './common'

/** Экран 4. Варианты и печать: число вариантов, А и Б рядом с ключами, признаки равнозначности, утверждение. */
export function Variants({ id }: { id: string }) {
  const { update, jobs, runJob, toast } = useStore()
  const { genVariants } = useActions()
  const { a } = useAssignment(id)
  if (!a) return <NotFound />

  const setA = (fn: (x: Assignment) => Assignment) =>
    update((s) => ({ ...s, assignments: patch(s.assignments, id, fn) }))
  const setCell = (vid: string, taskId: string, p: { text?: string; answer?: string }) =>
    setA((x) => ({
      ...x,
      status: 'draft',
      variants: x.variants.map((v) =>
        v.id !== vid ? v : { ...v, tasks: v.tasks.map((t) => (t.taskId === taskId ? { ...t, ...p } : t)) },
      ),
    }))

  const genJob = jobs[`gen:${a.id}`]
  const ready = a.variants.length >= a.variantCount && a.variants.length > 0
  const missingKeys = a.variants.flatMap((v) => v.tasks.filter((t) => !t.answer.trim()).map(() => v.name)).length
  const softCount = Object.values(a.features).flat().filter((f) => f.soft).length
  const cols = a.variants.slice(0, Math.max(1, a.variantCount))

  const replace = (v: Variant, taskId: string) => {
    const t = a.tasks.find((x) => x.id === taskId)
    const cur = v.tasks.find((x) => x.taskId === taskId)
    if (!t || !cur) return
    const avoid = a.variants.map((x) => x.tasks.find((y) => y.taskId === taskId)?.text ?? '').filter(Boolean)
    void runJob(`replace:${a.id}:${v.id}:${taskId}`, 'generate', `Замена №${t.num} (${v.name})`, async () => {
      const r = await replaceTask(t.num, t.text, cur.text, avoid)
      setCell(v.id, taskId, r)
      toast(`№${t.num} в варианте ${v.name} заменён`)
    })
  }

  return (
    <div className="page page--wide">
      <AssignmentHeader a={a} step="variants" />

      <div className="toolbar">
        <label className="field field--inline">
          <span>Вариантов</span>
          <input
            className="input input--num"
            type="number"
            min={1}
            max={6}
            value={a.variantCount}
            onChange={(e) =>
              setA((x) => ({ ...x, variantCount: Math.min(6, Math.max(1, Number(e.target.value) || 1)), status: 'draft' }))
            }
          />
        </label>
        <button className="btn" onClick={() => void genVariants(a.id)} disabled={genJob?.status === 'running'}>
          {a.variants.length ? 'Сформировать заново' : 'Сформировать варианты и ответы'}
        </button>
        <JobStatus job={genJob} onRetry={() => void genVariants(a.id)} />
        <span className="spacer" />
        {ready && (
          <>
            <a className="btn" href={href('print', 'variants', a.id)} target="_blank" rel="noreferrer">
              ⤓ PDF для учеников
            </a>
            <a className="btn" href={href('print', 'keys', a.id)} target="_blank" rel="noreferrer">
              ⤓ Ответы для учителя
            </a>
          </>
        )}
        {a.status === 'approved' ? (
          <span className="ok-line">✓ Утверждено</span>
        ) : (
          <button
            className="btn btn--primary"
            disabled={!ready}
            title={!ready ? 'Сначала сформируйте варианты' : undefined}
            onClick={() => {
              if (missingKeys && !confirm(`Заданий без ответа: ${missingKeys}. Утвердить работу?`)) return
              setA((x) => ({ ...x, status: 'approved' }))
              toast('Работа утверждена. Доступны печать и сбор работ')
            }}
          >
            Утвердить
          </button>
        )}
      </div>

      {!a.variants.length ? (
        <div className="empty">
          <h2>Варианты не сформированы</h2>
          <p className="muted">
            Будут сформированы ответы к варианту А{a.variantCount > 1 ? ` и ещё ${a.variantCount - 1} равнозначных варианта` : ''}
            . Обычно это занимает 20–60 секунд.
          </p>
        </div>
      ) : (
        <>
          {!ready && (
            <div className="notice notice--warn">
              Вариантов меньше, чем нужно ({a.variants.length} из {a.variantCount}) — сформируйте заново.
            </div>
          )}
          {softCount > 0 && (
            <div className="notice notice--warn">
              Жёлтым выделены признаки, по которым равнозначность вариантов сомнительна ({softCount}). Проверьте эти задания и при
              необходимости нажмите «Заменить задание».
            </div>
          )}
          <div className="variants" style={{ ['--cols' as string]: cols.length }}>
            <div className="variants__row variants__row--head">
              <div>№</div>
              {cols.map((v) => (
                <div key={v.id}>Вариант {v.name}</div>
              ))}
            </div>
            {a.tasks.map((t) => {
              const feats = a.features[t.id] ?? []
              return (
                <div key={t.id} className="variants__group">
                  <div className="variants__row">
                    <div className="variants__num">
                      <b>№{t.num}</b>
                      {t.manual && <Badge tone="warn">ручная</Badge>}
                    </div>
                    {cols.map((v, vi) => {
                      const cell = v.tasks.find((x) => x.taskId === t.id)
                      const job = jobs[`replace:${a.id}:${v.id}:${t.id}`]
                      return (
                        <div key={v.id} className="vcell">
                          {vi === 0 ? (
                            <p className="vcell__text">{t.text}</p>
                          ) : (
                            <textarea
                              className="input"
                              rows={3}
                              value={cell?.text ?? ''}
                              onChange={(e) => setCell(v.id, t.id, { text: e.target.value })}
                              aria-label={`№${t.num}, вариант ${v.name}`}
                            />
                          )}
                          <label className="key">
                            <span>Ответ</span>
                            <input
                              className={`input${cell?.answer ? '' : ' input--warn'}`}
                              value={cell?.answer ?? ''}
                              onChange={(e) => setCell(v.id, t.id, { answer: e.target.value })}
                              placeholder="нет ответа"
                            />
                          </label>
                          {vi > 0 && (
                            <div className="vcell__foot">
                              <JobStatus
                                job={job}
                                onRetry={() => replace(v, t.id)}
                                idle={
                                  <button className="link-btn" onClick={() => replace(v, t.id)}>
                                    Заменить задание
                                  </button>
                                }
                              />
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                  {feats.length > 0 && (
                    <FeatureTable feats={feats.filter((f) => f.soft)} cols={cols.length} />
                  )}
                  {feats.some((f) => !f.soft) && (
                    <details className="features-more">
                      <summary>
                        Признаки равнозначности · {feats.filter((f) => !f.soft).length} совпадают
                      </summary>
                      <FeatureTable feats={feats.filter((f) => !f.soft)} cols={cols.length} />
                    </details>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

/** Признаки равнозначности номера: «требует внимания» показываем сразу, остальные — под спойлером. */
function FeatureTable({ feats, cols }: { feats: EquivFeature[]; cols: number }) {
  if (!feats.length) return null
  return (
    <table className="features">
      <tbody>
        {feats.map((f) => (
          <tr key={f.name} className={f.soft ? 'is-soft' : ''}>
            <th>
              {f.name}
              {f.soft && <span className="soft-tag">сомнительно</span>}
            </th>
            {Array.from({ length: cols }, (_, i) => (
              <td key={i}>{f.values[i] ?? '—'}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
