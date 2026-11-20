import { useState } from 'react'
import { uid, type ClassGroup } from '../domain'
import { useRoute, go } from '../router'
import { patch, useStore } from '../store'
import { Modal, PageHead, Tabs, plural } from '../ui'
import { useOwnClasses } from './common'

const parseNames = (text: string) =>
  text
    .split(/\r?\n/)
    .map((s) => s.replace(/^\s*\d+[.)]\s*/, '').replace(/\s+/g, ' ').trim())
    .filter(Boolean)

/** Экран 1. Мои классы: список, создание, вставка учеников по одному в строке, вкладки параллели. */
export function Classes() {
  const { update, toast } = useStore()
  const classes = useOwnClasses()
  const { query } = useRoute()
  const current = classes.find((c) => c.id === query.get('c')) ?? classes[0]
  const [creating, setCreating] = useState(false)
  const [paste, setPaste] = useState('')

  const setClass = (id: string, fn: (c: ClassGroup) => ClassGroup) =>
    update((s) => ({ ...s, classes: patch(s.classes, id, fn) }))

  const addStudents = () => {
    if (!current) return
    const names = parseNames(paste)
    const existing = new Set(current.students.map((s) => s.name.toLowerCase()))
    const fresh = names.filter((n) => !existing.has(n.toLowerCase()))
    setClass(current.id, (c) => ({ ...c, students: [...c.students, ...fresh.map((name) => ({ id: uid('st'), name }))] }))
    setPaste('')
    toast(
      `Добавлено: ${fresh.length}` + (names.length > fresh.length ? ` (дубликаты пропущены: ${names.length - fresh.length})` : ''),
    )
  }

  // параллели: «8А», «8Б» → 8
  const parallels = [...new Set(classes.map((c) => c.name.match(/^\d+/)?.[0] ?? 'Другие'))]

  return (
    <div className="page">
      <PageHead
        title="Мои классы"
        actions={
          <button className="btn btn--primary" onClick={() => setCreating(true)}>
            + Создать класс
          </button>
        }
      />

      {!classes.length ? (
        <div className="empty">
          <h2>Классов пока нет</h2>
          <button className="btn btn--primary" onClick={() => setCreating(true)}>
            Создать класс
          </button>
        </div>
      ) : (
        <>
          <div className="class-strip">
          {parallels.map((p) => {
            const list = classes.filter((c) => (c.name.match(/^\d+/)?.[0] ?? 'Другие') === p)
            return (
              <div key={p} className="parallel">
                <span className="muted small">{p === 'Другие' ? p : `${p} классы`}</span>
                <Tabs
                  label={`Параллель ${p}`}
                  items={list.map((c) => ({ id: c.id, label: c.name }))}
                  value={current?.id ?? ''}
                  onChange={(c) => go('classes', { c })}
                />
              </div>
            )
          })}
          </div>

          {current && (
            <div className="two-col class-cols">
              <section className="panel">
                <div className="panel__head">
                  <h2>
                    {current.name} — {current.students.length}{' '}
                    {plural(current.students.length, 'ученик', 'ученика', 'учеников')}
                  </h2>
                  <button
                    className="link-btn link-btn--danger"
                    onClick={() =>
                      confirm(`Удалить класс ${current.name}? Сданные работы останутся в базе.`) &&
                      update((s) => ({ ...s, classes: s.classes.filter((c) => c.id !== current.id) }))
                    }
                  >
                    Удалить класс
                  </button>
                </div>
                {!current.students.length ? (
                  <p className="muted">Учеников нет</p>
                ) : (
                  <ol className="students students--scroll">
                    {current.students.map((st) => (
                      <li key={st.id}>
                        <input
                          className="input input--plain"
                          value={st.name}
                          aria-label="Имя ученика"
                          onChange={(e) =>
                            setClass(current.id, (c) => ({
                              ...c,
                              students: c.students.map((x) => (x.id === st.id ? { ...x, name: e.target.value } : x)),
                            }))
                          }
                        />
                        <button
                          className="icon-btn"
                          aria-label={`Удалить ${st.name}`}
                          onClick={() =>
                            setClass(current.id, (c) => ({ ...c, students: c.students.filter((x) => x.id !== st.id) }))
                          }
                        >
                          ✕
                        </button>
                      </li>
                    ))}
                  </ol>
                )}
              </section>

              <section className="panel">
                <h2>Добавить учеников</h2>
                <textarea
                  className="input class-paste"
                  rows={10}
                  value={paste}
                  onChange={(e) => setPaste(e.target.value)}
                  placeholder={'Иванов Иван\nПетрова Анна\nСидоров Пётр'}
                />
                <div className="row">
                  <span className="muted small">Строк: {parseNames(paste).length}</span>
                  <button className="btn btn--primary" disabled={!parseNames(paste).length} onClick={addStudents}>
                    Добавить
                  </button>
                </div>
              </section>
            </div>
          )}
        </>
      )}

      {creating && <CreateClass onClose={() => setCreating(false)} />}
    </div>
  )
}

function CreateClass({ onClose }: { onClose: () => void }) {
  const { state, update } = useStore()
  const [name, setName] = useState('')
  const [list, setList] = useState('')
  const exists = state.classes.some((c) => c.name.toLowerCase() === name.trim().toLowerCase())
  const create = () => {
    const id = uid('c')
    update((s) => ({
      ...s,
      classes: [
        ...s.classes,
        {
          id,
          name: name.trim(),
          ownerId: s.teacher.id,
          students: parseNames(list).map((n) => ({ id: uid('st'), name: n })),
        },
      ],
    }))
    onClose()
    go('classes', { c: id })
  }
  return (
    <Modal
      title="Новый класс"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn btn--primary" disabled={!name.trim() || exists} onClick={create}>
            Создать
          </button>
        </>
      }
    >
      <label className="field">
        <span>Название</span>
        <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="8Г" />
        {exists && <span className="field__err">Такой класс уже есть</span>}
      </label>
      <label className="field">
        <span>Ученики (по одному в строке)</span>
        <textarea className="input" rows={8} value={list} onChange={(e) => setList(e.target.value)} />
        <span className="muted small">Учеников: {parseNames(list).length}</span>
      </label>
    </Modal>
  )
}
