import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { api, type ClassItem, type Work } from './api'
import { extension, FORMATS, PHOTO_FORMATS, prepare, TASK_FORMATS } from './convert'
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

export function UploadSolutionsDialog({ work, onClose, onUpload }: {
  work: Work; onClose: () => void; onUpload: (ids: string[]) => void
}) {
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const onlyPhotos = files.every((file) => PHOTO_FORMATS.includes(extension(file)))
  const invalid = files.some((file) => !FORMATS.includes(extension(file))) ||
    (files.length > 1 && !onlyPhotos)
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!files.length || invalid) return
    setBusy(true)
    setError('')
    try {
      const prepared = await Promise.all(files.map(prepare))
      const response = await api.uploadWork(work, prepared)
      onUpload([response.uploadId])
    } catch (cause) { setError(errorText(cause)); setBusy(false) }
  }
  return <Dialog title="Загрузить решения" onClose={busy ? () => {} : onClose}>
    <form className="form" onSubmit={submit}>
      <label className="file-field">Выберите файлы<input type="file" required multiple autoFocus
        accept={FORMATS.map((format) => '.' + format).join(',')}
        onChange={(event) => { setFiles(Array.from(event.target.files ?? [])); setError('') }} /></label>
      <div className="upload-hint">
        <p>Один ZIP со снимками или несколько фотографий одним запросом. Также можно загрузить один DOCX или TXT.</p>
        <p>Для нескольких листов одного ученика используйте <code>Смирнова Анна__01.jpg</code> и <code>Смирнова Анна__02.jpg</code>.</p>
      </div>
      <ErrorText message={invalid ? 'Выберите несколько фото либо один архив или документ.' : error} />
      <div className="actions"><button type="button" className="button" disabled={busy} onClick={onClose}>Отмена</button>
        <button className="button primary" disabled={!files.length || invalid || busy}>{busy ? 'Загружаем…' : 'Загрузить'}</button></div>
    </form>
  </Dialog>
}

export const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить действие.'
export function ErrorText({ message }: { message?: string }) {
  return message ? <p className="notice-error" role="alert">{message}</p> : null
}
