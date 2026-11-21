import { useState, type FormEvent } from 'react'
import { api, type ClassItem, type Student, type Work } from './api'
import { ClassOptions, Dialog, DialogActions, ErrorText, errorText } from './components'
import { extension, PHOTO_FORMATS, prepare, TASK_FORMATS } from './convert'
import { number, requirementsOf, today } from './workspace'

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
        <ClassOptions classes={classes} />
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
      <DialogActions busy={busy} onCancel={onClose} cancelLabel={added ? 'Закрыть' : 'Отмена'} disabled={!ready || Boolean(tooLong)}>
        {busy ? 'Сохраняем…' : creating ? (names.length ? 'Создать класс и добавить ' + names.length : 'Создать класс')
          : names.length > 1 ? 'Добавить ' + names.length : 'Добавить'}</DialogActions>
    </form>
  </Dialog>
}

export function CreateWorkDialog({ classes, selectedClass, onClose, onCreated }: {
  classes: ClassItem[]; selectedClass: string; onClose: () => void; onCreated: (work: Work) => void
}) {
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File>()
  const [notes, setNotes] = useState('')
  const [classId, setClassId] = useState(selectedClass || (classes.length === 1 ? classes[0].classId : ''))
  const [date, setDate] = useState(today)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const parsed = requirementsOf(notes)
  const formatError = file && !TASK_FORMATS.includes(extension(file)) ? 'Выберите документ DOCX, TXT или фотографию.' : ''
  const classroom = classes.find((item) => item.classId === classId)
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!file || !classroom || !title.trim() || !date || parsed.error || formatError) return
    setBusy(true)
    setError('')
    try {
      const created = await api.uploadTask(classId, await prepare(file), 'math', title.trim(), {
        assignmentDate: date, requirements: parsed.requirements, taskPoints: parsed.taskPoints,
      })
      onCreated({ ...created, assignmentDate: date, title: title.trim(), subject: 'math', classId, className: classroom.name })
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
      <div className="field-row">
        <label>Класс<select aria-label="Класс" required value={classId} onChange={(event) => setClassId(event.target.value)}>
          <option value="" disabled>Выберите класс</option>
          <ClassOptions classes={classes} />
        </select></label>
        <label>Дата работы<input type="date" required value={date} onChange={(event) => setDate(event.target.value)} /></label>
      </div>
      <ErrorText message={error || formatError || parsed.error} />
      <DialogActions busy={busy} onCancel={onClose} disabled={!classes.length || Boolean(formatError || parsed.error)}>
        {busy ? 'Создаём…' : 'Далее →'}</DialogActions>
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
      <DialogActions busy={Boolean(busy)} onCancel={onClose} disabled={!files.length || invalid}>{busy || 'Загрузить'}</DialogActions>
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
