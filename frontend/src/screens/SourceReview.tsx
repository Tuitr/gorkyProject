import { useEffect, useRef, useState } from 'react'
import { splitTasksAi } from '../api/generate'
import { parseRules, readSource, splitTasks } from '../api/source'
import { sourceFiles, uid, type Assignment, type SourceFile, type SourceTask } from '../domain'
import { go, href, useRoute } from '../router'
import { patch, useStore } from '../store'
import { Badge } from '../ui'
import { AssignmentHeader, NotFound, useAssignment } from './common'

/** Экран 3. Сверка исходника: оригинал рядом со списком номеров, правка, объединение, правила проверки. */
export function SourceReview({ id }: { id: string }) {
  const { update, toast } = useStore()
  const { a, classes } = useAssignment(id)
  const { query } = useRoute()
  const wantAdd = query.get('add') === '1'
  // только что создана из формы «Добавить работу»: проверить номера и сохранить
  const isNew = query.get('new') === '1'
  const added = useRef(false)
  // фото условия: куда положить текст — в номер (id) или новыми номерами ('new')
  const photoInput = useRef<HTMLInputElement>(null)
  const photoTarget = useRef<string>('new')
  const [photoBusy, setPhotoBusy] = useState<string | null>(null)

  // «Добавить номер» со страницы работы: один пустой номер в конец, курсор — в его условие
  useEffect(() => {
    if (!wantAdd || !a || added.current) return
    added.current = true
    update((s) => ({
      ...s,
      assignments: patch(s.assignments, id, (x) => ({
        ...x,
        status: 'draft',
        tasks: [...x.tasks, { id: uid('t'), num: String(x.tasks.length + 1), text: '', manual: false, max: 1 }],
      })),
    }))
    history.replaceState(null, '', href('a', id, 'source', { f: query.get('f') ?? undefined }))
    setTimeout(() => {
      const last = [...document.querySelectorAll<HTMLTextAreaElement>('.src-task textarea')].pop()
      last?.scrollIntoView({ block: 'center' })
      last?.focus()
    }, 50)
  }, [wantAdd, a, id, update, query])

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

  const pickPhoto = (target: string) => {
    photoTarget.current = target
    photoInput.current?.click()
  }

  /** Фото условия → OCR → текст в номер (или новые номера); фото добавляется к оригиналу. */
  const onPhotos = async (fs: File[]) => {
    const target = photoTarget.current
    if (!fs.length) return
    setPhotoBusy(target)
    try {
      const read: Awaited<ReturnType<typeof readSource>>[] = []
      for (const f of fs) read.push(await readSource(f)) // по очереди — у OCR лимит запросов
      const text = read.map((r) => r.text.trim()).filter(Boolean).join('\n')
      const sources = read.map((r) => r.source)
      if (!text) toast('Текст на фотографии не обнаружен. Введите условие вручную.', 'err')

      let fresh: SourceTask[] = []
      if (target === 'new' && text) {
        fresh = splitTasks(text)
        try {
          const ai = await splitTasksAi(text)
          if (ai.length) fresh = ai.map((t) => ({ id: uid('t'), num: t.num, text: t.text, manual: false, max: 1 }))
        } catch (e) {
          console.warn('Автоматическое разделение не выполнено: задания разделены по нумерации «1)», «2.»', e)
        }
      }
      setA((x) => {
        const files = [...sourceFiles(x), ...sources]
        let tasks = x.tasks
        if (target !== 'new') {
          tasks = tasks.map((t) => (t.id === target ? { ...t, text: [t.text.trim(), text].filter(Boolean).join('\n') } : t))
        } else if (text) {
          // на фото один номер без «1)» — одним номером с продолжением нумерации
          const list = fresh.length ? fresh : [{ id: uid('t'), num: '', text, manual: false, max: 1 }]
          tasks = [...tasks, ...list.map((t, i) => (fresh.length > 1 ? t : { ...t, num: String(x.tasks.length + i + 1) }))]
        }
        return { ...x, files, file: x.file ?? files[0], tasks }
      })
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось распознать фото', 'err')
    } finally {
      setPhotoBusy(null)
    }
  }

  const { rules, unrecognized } = parseRules(a.notes)
  const classRules = classes
    .filter((c) => a.classNotes[c.id]?.trim())
    .map((c) => ({ c, ...parseRules(a.classNotes[c.id]) }))
  const manual = a.tasks.filter((t) => t.manual)
  const total = a.tasks.reduce((s, t) => s + t.max, 0)

  return (
    <div className="page page--wide">
      {isNew ? (
        <div className="page-head">
          <div>
            <h1>Проверка распознавания</h1>
            <p className="muted">Сверьте задания с исходным документом и сохраните работу.</p>
          </div>
          <div className="page-head__actions">
            <button
              className="btn"
              onClick={() => {
                update((s) => ({ ...s, assignments: s.assignments.filter((x) => x.id !== id) }))
                go()
              }}
            >
              Отмена
            </button>
            <button className="btn btn--primary" disabled={!a.tasks.length} onClick={() => go('a', a.id)}>
              Сохранить
            </button>
          </div>
        </div>
      ) : (
        <AssignmentHeader
          a={a}
          step="source"
          actions={
            <a className="btn btn--primary" href={href('a', a.id, 'variants')}>
              Далее: варианты →
            </a>
          }
        />
      )}

      <div className="source">
        <section className="panel source__orig">
          <div className="panel__head">
            <h2>Исходный документ</h2>
            <span className="muted small">
              {sourceFiles(a).map((f) => f.name).join(', ') || 'файл не загружен'}
            </span>
          </div>
          <Original a={a} />
        </section>

        <section className="panel">
          <div className="panel__head">
            <h2>
              Задания: {a.tasks.length} · максимальный балл: {total}
            </h2>
            <Badge tone={a.status === 'draft' ? 'warn' : 'ok'}>{a.status === 'draft' ? 'Черновик' : 'Утверждена'}</Badge>
          </div>
          <p className="muted small">
            Сверьте задания с исходным документом. Если одно задание разделено на два, используйте «Объединить с предыдущим».
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
                      aria-label="Номер задания"
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
                  <button
                    className="link-btn src-task__tool"
                    onClick={() => pickPhoto(t.id)}
                    disabled={!!photoBusy}
                    title="Распознать условие с фото и добавить в это задание"
                  >
                    {photoBusy === t.id ? 'распознавание…' : 'Распознать с фото'}
                  </button>
                  {i > 0 && (
                    <button className="link-btn src-task__tool" onClick={() => merge(i)} title="Объединить с предыдущим заданием">
                      Объединить с предыдущим
                    </button>
                  )}
                  <button
                    className="icon-btn icon-btn--danger src-task__tool"
                    onClick={() => remove(t.id)}
                    aria-label={`Удалить задание №${t.num}`}
                    title="Удалить задание"
                  >
                    ✕
                  </button>
                </div>
                <textarea
                  className="input"
                  rows={Math.min(6, Math.max(2, t.text.split('\n').length + 1))}
                  value={t.text}
                  onChange={(e) => setTask(t.id, { text: e.target.value })}
                  aria-label={`Условие задания №${t.num}`}
                />
                {!t.text.trim() && (
                  <button className="btn" onClick={() => pickPhoto(t.id)} disabled={!!photoBusy}>
                    {photoBusy === t.id ? (
                      <>
                        <span className="spinner spinner--sm" /> Выполняется распознавание…
                      </>
                    ) : (
                      'Заполнить условие по фото'
                    )}
                  </button>
                )}
              </li>
            ))}
          </ol>
          <div className="src-add">
            <button className="btn" onClick={add}>
              + Задание
            </button>
            <button className="btn" onClick={() => pickPhoto('new')} disabled={!!photoBusy}>
              {photoBusy === 'new' ? (
                <>
                  <span className="spinner spinner--sm" /> Выполняется распознавание…
                </>
              ) : (
                'Добавить задания по фото'
              )}
            </button>
          </div>
          <input
            ref={photoInput}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              void onPhotos(Array.from(e.target.files ?? []))
              e.target.value = ''
            }}
          />
        </section>

        <section className="panel how">
          <h2>Критерии проверки</h2>
          {rules.length === 0 && !classRules.length && !manual.length && (
            <p className="muted">Дополнительных правил нет. Проверка выполняется по эталонному ответу: оцениваются правильность ответа и хода решения.</p>
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
                    <Badge tone="warn">Не учитывается автоматической проверкой</Badge> {r}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {manual.length > 0 && (
            <p>
              <Badge tone="warn">Ручная проверка</Badge> {manual.map((t) => `№${t.num}`).join(', ')} — автоматическая проверка не выставляет
              балл; итоговый балл будет предварительным до оценки учителем.
            </p>
          )}
          {unrecognized.length > 0 && (
            <div className="unrec">
              <h3>Не учитывается автоматической проверкой</h3>
              <ul>
                {unrecognized.map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
              <p className="muted small">
                Эти указания будут переданы на проверку без изменений, однако однозначного правила в них нет. Проверьте такие задания вручную или
                уточните формулировку: «вычитать 1 балл за…», «не засчитывать…», «допускается…».
              </p>
            </div>
          )}
          <a className="link-btn" href={href('a', a.id, 'edit')}>
            Изменить указания
          </a>
        </section>
      </div>
    </div>
  )
}

function Original({ a }: { a: Assignment }) {
  const list = sourceFiles(a)
  if (!list.length) return <p className="muted">Файл не загружен: задания введены вручную.</p>
  return (
    <div className="orig-list">
      {list.map((f, i) => (
        <div key={i} className="orig-item">
          {list.length > 1 && <div className="orig-item__name muted small">{f.name}</div>}
          <OriginalFile f={f} />
        </div>
      ))}
    </div>
  )
}

function OriginalFile({ f }: { f: SourceFile }) {
  if (f.kind === 'docx' && f.html) return <div className="docx" dangerouslySetInnerHTML={{ __html: f.html }} />
  if (f.kind === 'pdf' && f.dataUrl) return <iframe className="pdf" src={f.dataUrl} title={`Исходный документ: ${f.name}`} />
  if (f.kind === 'image' && f.dataUrl) return <img className="orig-img" src={f.dataUrl} alt={f.name} />
  return <p className="muted">Предпросмотр недоступен.</p>
}
