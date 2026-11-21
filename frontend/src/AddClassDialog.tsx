import { useState, type CSSProperties } from 'react'
import { api, type ClassItem } from './api'

// Название класса: номер от 1 до 10 и заглавные буквы, например 8А или 10Б.
// Всё, кроме цифр и букв, отбрасываем, строчные буквы сразу делаем заглавными.
const normalizeName = (v: string) => v.toUpperCase().replace(/[^0-9А-ЯЁ]/g, '')
const CLASS_NAME = /^(10|[1-9])[А-ЯЁ]*$/

// Окно «Добавить класс»: название и список учеников → POST /v1/classes и POST /v1/classes/{id}/students.
export function AddClassDialog(props: { onCreated: (c: ClassItem) => void; onClose: () => void }) {
  const [name, setName] = useState('')
  const [students, setStudents] = useState<string[]>([''])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()

  const nameOk = CLASS_NAME.test(name)
  const filled = students.map((s) => s.trim().replace(/\s+/g, ' ')).filter(Boolean)

  const setStudent = (i: number, v: string) => setStudents(students.map((s, j) => (j === i ? v : s)))
  const removeStudent = (i: number) => setStudents(students.length > 1 ? students.filter((_, j) => j !== i) : [''])

  const create = async () => {
    if (!nameOk || busy) return
    setBusy(true)
    setError(undefined)
    try {
      const c = await api.createClass(name)
      for (const s of filled) await api.addStudent(c.classId, s)
      props.onCreated(c)
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <div style={st.backdrop} onClick={props.onClose}>
      <form
        style={st.dialog}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault()
          create()
        }}
      >
        <h2 style={st.h2}>Новый класс</h2>

        <label style={st.field}>
          <span style={st.label}>Название класса</span>
          <input
            autoFocus
            maxLength={8}
            placeholder="Например, 8А"
            value={name}
            onChange={(e) => setName(normalizeName(e.target.value))}
            style={st.input}
          />
          {name && !nameOk && <span style={st.error}>Номер класса от 1 до 10, затем буква: 5А, 10Б</span>}
        </label>

        <div style={st.field}>
          <span style={st.label}>Ученики (фамилия и имя)</span>
          <div style={st.list}>
            {students.map((s, i) => (
              <div key={i} style={st.row}>
                <input
                  // Новое поле получает фокус — список сам прокручивается к нему.
                  autoFocus={i > 0 && i === students.length - 1}
                  placeholder="Иванов Иван"
                  maxLength={200}
                  value={s}
                  onChange={(e) => setStudent(i, e.target.value)}
                  style={{ ...st.input, flex: 1 }}
                />
                <button type="button" title="Убрать" onClick={() => removeStudent(i)} style={st.remove}>
                  ×
                </button>
              </div>
            ))}
          </div>
          <button type="button" style={{ ...st.button, alignSelf: 'flex-start' }} onClick={() => setStudents([...students, ''])}>
            + Добавить ещё ученика
          </button>
        </div>

        {error && <p style={st.error}>{error}</p>}

        <div style={st.actions}>
          <button type="button" style={st.button} onClick={props.onClose}>
            Отмена
          </button>
          <button style={{ ...st.button, ...st.primary, opacity: nameOk && !busy ? 1 : 0.5 }} disabled={!nameOk || busy}>
            {busy ? 'Создаём…' : `Добавить${filled.length ? ` (учеников: ${filled.length})` : ''}`}
          </button>
        </div>
      </form>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  backdrop: { position: 'fixed', inset: 0, background: 'rgba(17, 24, 39, 0.45)', display: 'grid', placeItems: 'center', padding: 16, zIndex: 10 },
  dialog: { background: '#fff', borderRadius: 12, padding: 20, width: '100%', maxWidth: 440, maxHeight: 'calc(100vh - 32px)', overflowY: 'auto', boxSizing: 'border-box', boxShadow: '0 10px 40px rgba(0,0,0,0.2)', display: 'flex', flexDirection: 'column', gap: 16 },
  h2: { margin: 0, fontSize: 18 },
  field: { display: 'flex', flexDirection: 'column', gap: 6 },
  label: { fontSize: 13, color: '#6b7280' },
  // Видно три ученика (поле ~38px + отступ 6px), дальше — прокрутка.
  list: { display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 3 * 38 + 2 * 6, overflowY: 'auto', paddingRight: 2 },
  row: { display: 'flex', gap: 6, alignItems: 'center' },
  input: { padding: '8px 10px', font: 'inherit', border: '1px solid #d1d5db', borderRadius: 6, minWidth: 0, height: 38, boxSizing: 'border-box', flexShrink: 0 },
  remove: { border: 'none', background: 'none', cursor: 'pointer', color: '#9ca3af', fontSize: 20, padding: '0 6px' },
  error: { color: '#b91c1c', margin: 0, fontSize: 13 },
  actions: { display: 'flex', justifyContent: 'flex-end', gap: 8 },
  button: {},
  primary: { background: '#2563eb', color: '#fff', borderColor: '#2563eb' },
}
