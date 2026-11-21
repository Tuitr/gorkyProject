import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { api, type ClassItem, type Student, type Work } from './api'
import { extension, PHOTO_FORMATS, prepare, TASK_FORMATS } from './convert'
import { number, requirementsOf, today } from './workspace'

export function Dialog({ title, children, onClose, wide = false }: {
  title: string; children: ReactNode; onClose: () => void; wide?: boolean
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current!
    element.showModal()
    return () => element.close()
  }, [])
  return (
    <dialog className={'dialog' + (wide ? ' dialog-wide' : '')} ref={dialog} aria-labelledby="dialog-title"
      onCancel={(event) => { event.preventDefault(); onClose() }}>
      <div className="dialog-head">
        <h2 id="dialog-title">{title}</h2>
        <button className="icon-button close-button" type="button" aria-label="Закрыть" onClick={onClose}>×</button>
      </div>
      {children}
    </dialog>
  )
}

export function NameDialog({ title, label, placeholder, action, onClose }: {
  title: string; label: string; placeholder: string; action: (name: string) => Promise<void>; onClose: () => void
}) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try { await action(name.trim()); onClose() }
    catch (cause) { setError(errorText(cause)); setBusy(false) }
  }
  return <Dialog title={title} onClose={busy ? () => {} : onClose}>
    <form className="form" onSubmit={submit}>
      <label>{label}<input required autoFocus maxLength={120} placeholder={placeholder} value={name} onChange={(event) => setName(event.target.value)} /></label>
      <ErrorText message={error} />
      <div className="actions"><button type="button" className="button" disabled={busy} onClick={onClose}>Отмена</button>
        <button className="button primary" disabled={busy || !name.trim()}>{busy ? 'Сохраняем…' : 'Добавить'}</button></div>
    </form>
  </Dialog>
}

// Класс и ученики в одном окне: выбрать класс или создать новый, учеников — по одному на строку.
// Добавляем по очереди, при сбое оставляем в поле тех, кто не добавлен.
const NEW_CLASS = '__new__'
export function ClassStudentsDialog({ classes, selectedClass, onClassCreated, onClose }: {
  classes: ClassItem[]; selectedClass: string; onClassCreated: (item: ClassItem) => void; onClose: () => void
}) {
  const [classId, setClassId] = useState(selectedClass || classes[0]?.classId || NEW_CLASS)
  const [className, setClassName] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [added, setAdded] = useState(0)
  const creating = classId === NEW_CLASS
  const names = [...new Set(text.split(/\r?\n/).map((line) => line.trim().replace(/\s+/g, ' ')).filter(Boolean))]
  const tooLong = names.find((name) => name.length > 120)
  const ready = creating ? Boolean(className.trim()) : names.length > 0

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    let target = classId
    let done = 0
    try {
      if (creating) {
        const created = await api.createClass(className.trim())
        onClassCreated(created)
        // Класс уже есть: при сбое на учениках повторная попытка не создаст его второй раз.
        target = created.classId
        setClassId(created.classId)
      }
      for (const name of names) {
        await api.addStudent(target, name)
        done += 1
        setAdded((count) => count + 1)
      }
      onClose()
    } catch (cause) {
      setText(names.slice(done).join('\n'))
      setError(errorText(cause))
      setBusy(false)
    }
  }

  return <Dialog title="Класс и ученики" onClose={busy ? () => {} : onClose}>
    <form className="form" onSubmit={submit}>
      <label>Класс<select required value={classId} onChange={(event) => setClassId(event.target.value)}>
        {classes.map((item) => <option key={item.classId} value={item.classId}>{item.name}</option>)}
        <option value={NEW_CLASS}>+ Новый класс</option>
      </select></label>
      {creating && <label>Название нового класса<input required autoFocus maxLength={120} placeholder="Например, 7А"
        value={className} onChange={(event) => setClassName(event.target.value)} /></label>}
      <label>Ученики<small>{creating ? 'Необязательно. ' : ''}Фамилия и имя, каждый ученик с новой строки.</small>
        <textarea required={!creating} autoFocus={!creating} rows={8} placeholder={'Смирнова Анна\nИванов Пётр'} value={text}
          onChange={(event) => setText(event.target.value)} /></label>
      {tooLong && <p className="notice-error">Слишком длинное имя: «{tooLong.slice(0, 40)}…». Не больше 120 символов.</p>}
      {added > 0 && <p className="hint" role="status">Добавлено учеников: {added}.</p>}
      <ErrorText message={error} />
      <div className="actions"><button type="button" className="button" disabled={busy} onClick={onClose}>{added ? 'Закрыть' : 'Отмена'}</button>
        <button className="button primary" disabled={busy || !ready || Boolean(tooLong)}>
          {busy ? 'Сохраняем…' : creating ? (names.length ? 'Создать класс и добавить ' + names.length : 'Создать класс')
            : names.length > 1 ? 'Добавить ' + names.length : 'Добавить'}</button></div>
    </form>
  </Dialog>
}

export function ConfirmDialog({ title, text, action, onClose }: {
  title: string; text: string; action: () => Promise<void>; onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function confirm() {
    setBusy(true)
    setError('')
    try { await action(); onClose() }
    catch (cause) { setError(errorText(cause)); setBusy(false) }
  }
  return <Dialog title={title} onClose={busy ? () => {} : onClose}>
    <div className="form"><p>{text}</p><ErrorText message={error} />
      <div className="actions"><button className="button" autoFocus disabled={busy} onClick={onClose}>Отмена</button>
        <button className="button destructive" disabled={busy} onClick={() => void confirm()}>{busy ? 'Удаляем…' : 'Удалить'}</button></div>
    </div>
  </Dialog>
}

export function CreateWorkDialog({ classes, selectedClass, onClose, onCreated }: {
  classes: ClassItem[]; selectedClass: string; onClose: () => void; onCreated: (work: Work) => void
}) {
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File>()
  const [notes, setNotes] = useState('')
  const [subject, setSubject] = useState('')
  const [classId, setClassId] = useState(selectedClass || (classes.length === 1 ? classes[0].classId : ''))
  const [date, setDate] = useState(today)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const parsed = requirementsOf(notes)
  const formatError = file && !TASK_FORMATS.includes(extension(file)) ? 'Выберите документ DOCX, TXT или фотографию.' : ''
  const classroom = classes.find((item) => item.classId === classId)
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!file || !classroom || !subject || !title.trim() || !date || parsed.error || formatError) return
    setBusy(true)
    setError('')
    try {
      const created = await api.uploadTask(classId, await prepare(file), subject, title.trim(), {
        assignmentDate: date, requirements: parsed.requirements, taskPoints: parsed.taskPoints,
      })
      onCreated({ ...created, assignmentDate: date, title: title.trim(), subject, classId, className: classroom.name })
    } catch (cause) { setError(errorText(cause)); setBusy(false) }
  }
  return <Dialog title="Создать работу" onClose={busy ? () => {} : onClose}>
    <form className="form" onSubmit={submit}>
      {!classes.length && <p className="hint">Сначала добавьте класс на главной странице.</p>}
      <label>Название работы<input required autoFocus maxLength={120} placeholder="Например, Линейные уравнения" value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <label className="file-field">Документ с заданиями
        <input type="file" required accept={TASK_FORMATS.map((format) => '.' + format).join(',')} onChange={(event) => setFile(event.target.files?.[0])} />
        <small>DOCX, TXT или фото</small>
      </label>
      <label>Критерии проверки<textarea rows={3} placeholder="Например, № 1 — 3 балла. № 2: требовать пояснение."
        value={notes} onChange={(event) => setNotes(event.target.value)} /></label>
      {parsed.taskPoints.length > 0 && <p className="hint">Максимум по заданиям · {parsed.taskPoints.map((item) => '№ ' + item.taskNumber + ' — ' + number(item.maxScore)).join(', ')}</p>}
      <fieldset className="subject-field"><legend>Предмет</legend>
        <label><input type="radio" name="subject" required checked={subject === 'math'} onChange={() => setSubject('math')} />Математика</label>
        <label><input type="radio" name="subject" required checked={subject === 'physics'} onChange={() => setSubject('physics')} />Физика</label>
      </fieldset>
      <div className="field-row">
        <label>Класс<select aria-label="Класс" required value={classId} onChange={(event) => setClassId(event.target.value)}>
          <option value="" disabled>Выберите класс</option>
          {classes.map((item) => <option key={item.classId} value={item.classId}>{item.name}</option>)}
        </select></label>
        <label>Дата работы<input type="date" required value={date} onChange={(event) => setDate(event.target.value)} /></label>
      </div>
      <ErrorText message={error || formatError || parsed.error} />
      <div className="actions"><button type="button" className="button" disabled={busy} onClick={onClose}>Отмена</button>
        <button className="button primary" disabled={busy || !classes.length || Boolean(formatError || parsed.error)}>{busy ? 'Создаём…' : 'Далее →'}</button></div>
    </form>
  </Dialog>
}

// Решение одного ученика. Файлы называем его именем (по имени кабинет и привязывает),
// а то, что кабинет не смог привязать сам, подтверждаем за этого ученика.
export function StudentUploadDialog({ work, student, onClose, onUpload }: {
  work: Work; student: Student; onClose: () => void; onUpload: (ids: string[]) => void
}) {
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const photos = files.every((file) => PHOTO_FORMATS.includes(extension(file)))
  const invalid = files.some((file) => !STUDENT_FORMATS.includes(extension(file))) || (files.length > 1 && !photos)
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!files.length || invalid) return
    setError('')
    try {
      setBusy('Загружаем…')
      const base = student.name.replace(/[\\/:*?"<>|_]+/g, ' ').replace(/\s+/g, ' ').trim() || 'Ученик'
      const prepared = await Promise.all(files.map(prepare))
      const named = prepared.map((file, index) => {
        const page = prepared.length > 1 ? '__' + String(index + 1).padStart(2, '0') : ''
        return new File([file], base + page + '.' + extension(file), { type: file.type })
      })
      const { uploadId } = await api.uploadWork(work, named)
      setBusy('Привязываем к ученику…')
      await bindToStudent(uploadId, student.studentId)
      onUpload([uploadId])
    } catch (cause) { setError(errorText(cause)); setBusy('') }
  }
  return <Dialog title={'Решение: ' + student.name} onClose={busy ? () => {} : onClose}>
    <form className="form" onSubmit={submit}>
      <label className="file-field">Выберите файлы<input type="file" required multiple autoFocus
        accept={STUDENT_FORMATS.map((format) => '.' + format).join(',')}
        onChange={(event) => { setFiles(Array.from(event.target.files ?? [])); setError('') }} /></label>
      <div className="upload-hint">
        <p>Фотографии листов (в порядке выбора — страница 1, 2, …) или один DOCX / TXT.</p>
      </div>
      <ErrorText message={invalid ? 'Выберите фотографии либо один документ.' : error} />
      <div className="actions"><button type="button" className="button" disabled={Boolean(busy)} onClick={onClose}>Отмена</button>
        <button className="button primary" disabled={!files.length || invalid || Boolean(busy)}>{busy || 'Загрузить'}</button></div>
    </form>
  </Dialog>
}

const STUDENT_FORMATS = ['docx', 'txt', ...PHOTO_FORMATS]

// Ждём, пока кабинет разберёт загрузку (до ~30 с), и подтверждаем ученика там, где кабинет спрашивает.
async function bindToStudent(uploadId: string, studentId: string) {
  for (let attempt = 0; attempt < 20; attempt++) {
    const upload = await api.upload(uploadId)
    if (!upload.files.some((file) => file.status === 'pending_binding')) {
      const seen = new Set<string>()
      for (const file of upload.files) {
        if (file.status !== 'needs_confirmation' || file.reasonCode === 'invalid_page_order') continue
        const group = file.groupId ?? file.fileId
        if (seen.has(group)) continue
        seen.add(group)
        await api.bind(uploadId, file.fileId, { studentId })
      }
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 1500))
  }
}

export const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить действие.'
export function ErrorText({ message }: { message?: string }) {
  return message ? <p className="notice-error" role="alert">{message}</p> : null
}
