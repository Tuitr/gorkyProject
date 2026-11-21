import { useState, type CSSProperties } from 'react'
import type { Assignment } from './api'
import { folderName } from './titles'
import { FORMATS, FORMATS_TEXT, TASK_FORMATS, extension } from './convert'

export type Subject = 'math' | 'physics'
export type UploadKind = 'task' | 'solution'
export type UploadRequest =
  | { kind: 'task'; subject: Subject; title: string; files: File[] }
  | { kind: 'solution'; assignmentId: string; files: File[] }

// Окно загрузки. Задание (вариант проверочной) создаёт новую папку — для него выбираем предмет.
// Ответ ученика кладётся в выбранную папку (задание).
export function UploadDialog(props: {
  folders: Assignment[]
  /** Открыть сразу на «Ответ ученика» в эту папку (кнопка в самой папке). */
  folderId?: string
  onSubmit: (r: UploadRequest) => void
  onClose: () => void
}) {
  const [kind, setKind] = useState<UploadKind | ''>(props.folderId ? 'solution' : '')
  const [subject, setSubject] = useState<Subject | ''>('')
  const [title, setTitle] = useState('')
  const [folderId, setFolderId] = useState(props.folderId ?? '')
  const [files, setFiles] = useState<File[]>([])

  const wrongFormat = files.filter((f) => !FORMATS.includes(extension(f)))
  const taskNotDoc = kind === 'task' && wrongFormat.length === 0 && files.some((f) => !TASK_FORMATS.includes(extension(f)))
  const target = kind === 'task' ? (title.trim() ? subject : '') : kind === 'solution' ? folderId : ''
  const ready = target !== '' && files.length > 0 && wrongFormat.length === 0 && !taskNotDoc

  const submit = () => {
    if (!ready) return
    if (kind === 'task') props.onSubmit({ kind, subject: subject as Subject, title: title.trim(), files })
    else props.onSubmit({ kind: 'solution', assignmentId: folderId, files })
  }

  return (
    <div style={st.backdrop} onClick={props.onClose}>
      <div style={st.dialog} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h2 style={st.h2}>Загрузка файлов</h2>

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
            label="Вариант проверочной работы (создаст новую папку)"
          />
          <Choice
            name="kind"
            value="solution"
            current={kind}
            onChange={(v) => {
              setKind(v)
              setFiles([])
            }}
            label="Ответ ученика"
          />
        </fieldset>

        {kind === 'task' && (
          <label style={st.group}>
            <span style={st.legend}>Название проверочной работы</span>
            <input
              autoFocus
              maxLength={120}
              placeholder="Например, Тепловые явления"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              style={st.select}
            />
          </label>
        )}

        {kind === 'task' && (
          <fieldset style={st.group}>
            <legend style={st.legend}>Предмет</legend>
            <Choice name="subject" value="math" current={subject} onChange={setSubject} label="Математика" />
            <Choice name="subject" value="physics" current={subject} onChange={setSubject} label="Физика" />
          </fieldset>
        )}

        {kind === 'solution' &&
          (props.folders.length === 0 ? (
            <p style={st.error}>Папок пока нет — сначала загрузите вариант проверочной работы.</p>
          ) : (
            <label style={st.group}>
              <span style={st.legend}>Папка</span>
              <select value={folderId} onChange={(e) => setFolderId(e.target.value)} style={st.select}>
                <option value="" disabled>
                  Выберите папку…
                </option>
                {props.folders.map((a) => (
                  <option key={a.assignmentId} value={a.assignmentId}>
                    📁 {folderName(a)}
                  </option>
                ))}
              </select>
            </label>
          ))}

        {kind && (
          <input type="file" multiple={kind === 'solution'} onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
        )}

        {wrongFormat.length > 0 && (
          <p style={st.error}>
            Загрузите файл нужного формата: {FORMATS_TEXT}. Не подходит: {wrongFormat.map((f) => f.name).join(', ')}
          </p>
        )}
        {taskNotDoc && <p style={st.error}>Для варианта выберите DOCX, TXT или фото.</p>}
        {kind === 'solution' && (
          <p style={st.legend}>Листы одного ученика выберите вместе или положите в ZIP. Назовите их «Смирнова Анна__01.jpg», «Смирнова Анна__02.jpg».</p>
        )}
        {files.length > 0 && target === '' && (
          <p style={st.error}>
            {kind === 'task' ? (title.trim() ? 'Выберите предмет.' : 'Введите название проверочной работы.') : 'Выберите папку, куда положить ответ.'}
          </p>
        )}

        <div style={st.actions}>
          <button style={st.button} onClick={props.onClose}>
            Отмена
          </button>
          <button className="primary" style={{ ...st.button, opacity: ready ? 1 : 0.5 }} disabled={!ready} onClick={submit}>
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
  backdrop: { position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.35)', backdropFilter: 'blur(2px)', display: 'grid', placeItems: 'center', padding: 16, zIndex: 10 },
  dialog: { background: '#fff', borderRadius: 16, padding: 24, width: '100%', maxWidth: 460, boxShadow: '0 20px 50px rgba(15, 23, 42, 0.18)', display: 'flex', flexDirection: 'column', gap: 14 },
  h2: { margin: 0, fontSize: 18 },
  group: { border: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 },
  legend: { fontSize: 13, color: '#6b7280', marginBottom: 6, padding: 0 },
  choice: { display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' },
  select: {},
  error: { color: '#b91c1c', margin: 0, fontSize: 14 },
  actions: { display: 'flex', justifyContent: 'flex-end', gap: 8 },
  button: {},
  primary: {},
}
