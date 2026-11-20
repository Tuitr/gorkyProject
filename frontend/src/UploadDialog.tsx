import { useState, type CSSProperties } from 'react'
import { FORMATS, FORMATS_TEXT, TASK_FORMATS, extension } from './convert'

export type Subject = 'math' | 'physics'
export type UploadKind = 'task' | 'solution'


// Окно загрузки: предмет, что загружаем (задание или решение ученика) и файлы.
export function UploadDialog(props: {
  hasTask: (s: Subject) => boolean
  onSubmit: (p: { subject: Subject; kind: UploadKind; files: File[] }) => void
  onClose: () => void
}) {
  const [subject, setSubject] = useState<Subject | ''>('')
  const [kind, setKind] = useState<UploadKind | ''>('')
  const [files, setFiles] = useState<File[]>([])

  const noTask = subject !== '' && kind === 'solution' && !props.hasTask(subject)
  const wrongFormat = files.filter((f) => !FORMATS.includes(extension(f)))
  const taskNotDocx = kind === 'task' && wrongFormat.length === 0 && files.some((f) => !TASK_FORMATS.includes(extension(f)))
  const ready = subject !== '' && kind !== '' && files.length > 0 && !noTask && wrongFormat.length === 0 && !taskNotDocx

  return (
    <div style={st.backdrop} onClick={props.onClose}>
      <div style={st.dialog} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h2 style={st.h2}>Загрузка файлов</h2>

        <fieldset style={st.group}>
          <legend style={st.legend}>Предмет</legend>
          <Choice name="subject" value="math" current={subject} onChange={setSubject} label="Математика" />
          <Choice name="subject" value="physics" current={subject} onChange={setSubject} label="Физика" />
        </fieldset>

        <fieldset style={st.group}>
          <legend style={st.legend}>Что загружаем</legend>
          <Choice
            name="kind"
            value="task"
            current={kind}
            onChange={(v) => {
              setKind(v)
              setFiles([])
            }}
            label="Задание (вариант контрольной)"
          />
          <Choice
            name="kind"
            value="solution"
            current={kind}
            onChange={(v) => {
              setKind(v)
              setFiles([])
            }}
            label="Решение ученика"
          />
        </fieldset>

        {noTask && <p style={st.error}>В этом классе ещё нет задания по этому предмету — сначала загрузите задание.</p>}

        {kind && (
          <label style={st.file}>
            <input
              type="file"
              multiple={kind === 'solution'}
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
          </label>
        )}

        {wrongFormat.length > 0 && (
          <p style={st.error}>
            Загрузите файл нужного формата: {FORMATS_TEXT}. Не подходит: {wrongFormat.map((f) => f.name).join(', ')}
          </p>
        )}
        {taskNotDocx && <p style={st.error}>Задание загружается только как документ: DOCX или TXT.</p>}

        <div style={st.actions}>
          <button style={st.button} onClick={props.onClose}>
            Отмена
          </button>
          <button
            style={{ ...st.button, ...st.primary, opacity: ready ? 1 : 0.5 }}
            disabled={!ready}
            onClick={() => ready && props.onSubmit({ subject, kind, files })}
          >
            Загрузить
          </button>
        </div>
      </div>
    </div>
  )
}

function Choice<T extends string>(p: { name: string; value: T; current: T | ''; onChange: (v: T) => void; label: string }) {
  return (
    <label style={st.choice}>
      <input type="radio" name={p.name} checked={p.current === p.value} onChange={() => p.onChange(p.value)} />
      {p.label}
    </label>
  )
}

const st: Record<string, CSSProperties> = {
  backdrop: { position: 'fixed', inset: 0, background: 'rgba(17, 24, 39, 0.45)', display: 'grid', placeItems: 'center', padding: 16, zIndex: 10 },
  dialog: { background: '#fff', borderRadius: 12, padding: 20, width: '100%', maxWidth: 440, boxShadow: '0 10px 40px rgba(0,0,0,0.2)', display: 'flex', flexDirection: 'column', gap: 14 },
  h2: { margin: 0, fontSize: 18 },
  group: { border: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 },
  legend: { fontSize: 13, color: '#6b7280', marginBottom: 6, padding: 0 },
  choice: { display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' },
  file: { display: 'block' },
  error: { color: '#b91c1c', margin: 0, fontSize: 14 },
  actions: { display: 'flex', justifyContent: 'flex-end', gap: 8 },
  button: { padding: '8px 16px', font: 'inherit', cursor: 'pointer' },
  primary: { background: '#2563eb', color: '#fff', border: '1px solid #2563eb', borderRadius: 4 },
}
