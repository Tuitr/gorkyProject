import { parseRules } from '../api/source'
import { uid, type Assignment, type SourceTask } from '../domain'
import { href } from '../router'
import { patch, useStore } from '../store'
import { Badge } from '../ui'
import { AssignmentHeader, NotFound, useAssignment } from './common'

/** Экран 3. Сверка исходника: оригинал рядом со списком номеров, правка, объединение, правила проверки. */
export function SourceReview({ id }: { id: string }) {
  const { update } = useStore()
  const { a, classes } = useAssignment(id)
  if (!a) return <NotFound />

  // любая правка исходника возвращает работу в черновик
  const setA = (fn: (x: Assignment) => Assignment) =>
    update((s) => ({ ...s, assignments: patch(s.assignments, id, (x) => ({ ...fn(x), status: 'draft' })) }))
  const setTask = (tid: string, p: Partial<SourceTask>) =>
    setA((x) => ({ ...x, tasks: x.tasks.map((t) => (t.id === tid ? { ...t, ...p } : t)) }))

  const merge = (i: number) =>
    setA((x) => {
      const prev = x.tasks[i - 1]
      const cur = x.tasks[i]
      const tasks = x.tasks.filter((_, j) => j !== i)
      // номер случайно разбился на два — склеиваем текст, балл берём больший
      tasks[i - 1] = { ...prev, text: `${prev.text}\n${cur.text}`.trim(), max: Math.max(prev.max, cur.max) }
      return { ...x, tasks, variants: x.variants.map((v) => ({ ...v, tasks: v.tasks.filter((t) => t.taskId !== cur.id) })) }
    })

  const remove = (tid: string) =>
    setA((x) => ({
      ...x,
      tasks: x.tasks.filter((t) => t.id !== tid),
      variants: x.variants.map((v) => ({ ...v, tasks: v.tasks.filter((t) => t.taskId !== tid) })),
    }))

  const add = () =>
    setA((x) => ({
      ...x,
      tasks: [...x.tasks, { id: uid('t'), num: String(x.tasks.length + 1), text: '', manual: false, max: 1 }],
    }))

  const { rules, unrecognized } = parseRules(a.notes)
  const classRules = classes
    .filter((c) => a.classNotes[c.id]?.trim())
    .map((c) => ({ c, ...parseRules(a.classNotes[c.id]) }))
  const manual = a.tasks.filter((t) => t.manual)
  const total = a.tasks.reduce((s, t) => s + t.max, 0)

  return (
    <div className="page page--wide">
      <AssignmentHeader
        a={a}
        step="source"
        actions={
          <a className="btn btn--primary" href={href('a', a.id, 'variants')}>
            Далее: варианты →
          </a>
        }
      />

      <div className="source">
        <section className="panel source__orig">
          <div className="panel__head">
            <h2>Оригинал</h2>
            <span className="muted small">{a.file?.name ?? 'файл не загружен'}</span>
          </div>
          <Original a={a} />
        </section>

        <section className="panel">
          <div className="panel__head">
            <h2>
              Номера · {a.tasks.length} · макс. {total} б.
            </h2>
            <Badge tone={a.status === 'draft' ? 'warn' : 'ok'}>{a.status === 'draft' ? 'Черновик' : 'Утверждена'}</Badge>
          </div>
          <p className="muted small">
            Сверьте номера с оригиналом. Если один номер разбился на два — «объединить с предыдущим».
          </p>
          <ol className="src-tasks">
            {a.tasks.map((t, i) => (
              <li key={t.id} className={`src-task${t.manual ? ' is-manual' : ''}`}>
                <div className="src-task__head">
                  <label className="num">
                    №
                    <input
                      className="input input--num"
                      value={t.num}
                      onChange={(e) => setTask(t.id, { num: e.target.value })}
                      aria-label="Номер"
                    />
                  </label>
                  <label className="max">
                    макс.
                    <input
                      className="input input--num"
                      type="number"
                      min={0}
                      value={t.max}
                      onChange={(e) => setTask(t.id, { max: Math.max(0, Number(e.target.value) || 0) })}
                      aria-label="Максимальный балл"
                    />
                    б.
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={t.manual}
                      onChange={(e) => setTask(t.id, { manual: e.target.checked })}
                    />
                    Ручная проверка
                  </label>
                  <span className="spacer" />
                  {i > 0 && (
                    <button className="link-btn" onClick={() => merge(i)} title="Склеить с предыдущим номером">
                      ⤒ объединить строки
                    </button>
                  )}
                  <button className="icon-btn" onClick={() => remove(t.id)} aria-label={`Удалить №${t.num}`}>
                    ✕
                  </button>
                </div>
                <textarea
                  className="input"
                  rows={Math.min(6, Math.max(2, t.text.split('\n').length + 1))}
                  value={t.text}
                  onChange={(e) => setTask(t.id, { text: e.target.value })}
                  aria-label={`Условие №${t.num}`}
                />
              </li>
            ))}
          </ol>
          <button className="btn" onClick={add}>
            + Номер
          </button>
        </section>

        <section className="panel how">
          <h2>Как будет проверяться</h2>
          {rules.length === 0 && !classRules.length && !manual.length && (
            <p className="muted">Особых правил нет — ИИ проверяет по ключу: верный ответ и верный ход решения.</p>
          )}
          {rules.length > 0 && (
            <ul className="rules">
              {rules.map((r) => (
                <li key={r.text}>
                  <Badge tone="info">{r.kind}</Badge> {r.text}
                </li>
              ))}
            </ul>
          )}
          {classRules.map(({ c, rules: cr, unrecognized: cu }) => (
            <div key={c.id}>
              <h3>Только для {c.name}</h3>
              <ul className="rules">
                {cr.map((r) => (
                  <li key={r.text}>
                    <Badge tone="info">{r.kind}</Badge> {r.text}
                  </li>
                ))}
                {cu.map((r) => (
                  <li key={r}>
                    <Badge tone="warn">Не учтено автоматически</Badge> {r}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {manual.length > 0 && (
            <p>
              <Badge tone="warn">Ручная проверка</Badge> {manual.map((t) => `№${t.num}`).join(', ')} — ИИ не ставит
              балл, сумма будет «предварительной», пока вы не оцените.
            </p>
          )}
          {unrecognized.length > 0 && (
            <div className="unrec">
              <h3>Не учтено автоматически</h3>
              <ul>
                {unrecognized.map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
              <p className="muted small">
                Эти строки уйдут в GigaChat как есть, но строгого правила по ним нет. Проверьте такие места сами или
                переформулируйте: «снимать 1 балл за…», «не засчитывать…», «можно…».
              </p>
            </div>
          )}
          <a className="link-btn" href={href('a', a.id, 'edit')}>
            Изменить заметки
          </a>
        </section>
      </div>
    </div>
  )
}

function Original({ a }: { a: Assignment }) {
  const f = a.file
  if (!f) return <p className="muted">Файл не загружен — номера введены вручную.</p>
  if (f.kind === 'docx' && f.html) return <div className="docx" dangerouslySetInnerHTML={{ __html: f.html }} />
  if (f.kind === 'pdf' && f.dataUrl) return <iframe className="pdf" src={f.dataUrl} title="Оригинал PDF" />
  if (f.kind === 'image' && f.dataUrl) return <img className="orig-img" src={f.dataUrl} alt="Оригинал заданий" />
  return <p className="muted">Предпросмотр недоступен.</p>
}
