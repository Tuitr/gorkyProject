import { useEffect, useRef, useState } from 'react'
import { useActions } from '../actions'
import { bindFile, bindNewStudent, readUpload, uploadWork, type UploadFile } from '../api/cabinet'
import { pullClasses, withRemoteClasses } from '../api/sync'
import { uid, type Assignment, type CabinetUploadRecord, type ClassGroup, type Submission } from '../domain'
import { unzip } from '../lib/zip'
import { patch, useStore } from '../store'
import { Badge, Modal, type Tone } from '../ui'

/**
 * Загрузка работ учеников в кабинет (бэкенд из main): DOCX или ZIP с DOCX.
 * Кабинет сам подбирает ученика по подписи и имени файла; где не уверен — учитель выбирает здесь
 * (или создаёт нового ученика). Загрузки запоминаются: окно показывает их снова, и неподтверждённые
 * привязки можно доделать позже. Привязанная работа появляется в сборе работ (текстом из DOCX) и
 * уходит на нашу проверку, пока своей проверки в кабинете нет.
 */

const STATUS: Record<UploadFile['status'], [string, Tone]> = {
  pending_binding: ['определяется ученик…', 'muted'],
  matched: ['ученик найден', 'info'],
  needs_confirmation: ['выберите ученика', 'warn'],
  bound: ['принято', 'ok'],
  rejected: ['отклонено', 'err'],
}
const NEW = '__new__'

type Item = { record: CabinetUploadRecord; files: UploadFile[]; error?: string; sending?: boolean }

const baseName = (n: string) => n.split(/[\\/]/).pop() ?? n
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** Текст DOCX (без картинок) — для нашей проверки, пока в кабинете её нет. */
async function docxText(buf: ArrayBuffer) {
  const mammoth = await import('mammoth')
  return (await mammoth.extractRawText({ arrayBuffer: buf })).value
}

export function CabinetUpload({ a, cls, onClose }: { a: Assignment; cls: ClassGroup; onClose: () => void }) {
  const { state, update, getState, toast } = useStore()
  const { publish, process } = useActions()
  const [items, setItems] = useState<Item[]>([])
  const [busy, setBusy] = useState(false)
  const [choice, setChoice] = useState<Record<string, string>>({})
  const [newName, setNewName] = useState<Record<string, string>>({})
  const fileRef = useRef<HTMLInputElement>(null)

  const setItem = (uploadId: string, fn: (x: Item) => Item) =>
    setItems((list) => list.map((x) => (x.record.uploadId === uploadId ? fn(x) : x)))

  /** Привязанный файл → сдача ученика в нашем состоянии (страница-текст) → проверка. */
  const attach = (record: CabinetUploadRecord, f: UploadFile) => {
    if (!f.studentId) return
    const text = record.texts[baseName(f.originalName)] ?? ''
    const lines = text
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter(Boolean)
      .map((t) => ({ text: t, box: { x: 0, y: 0, w: 0, h: 0 } }))
    const st = getState()
    const prev = st.submissions.find((s) => s.assignmentId === a.id && s.classId === cls.id && s.studentId === f.studentId)
    if (prev?.remote?.fileId === f.fileId) return // уже принят
    const id = prev?.id ?? uid('s')
    const sub: Submission = {
      id,
      assignmentId: a.id,
      classId: cls.id,
      studentId: f.studentId,
      variantId: prev?.variantId ?? null,
      pages: [{ id: uid('p'), image: '', hash: `cabinet:${f.fileId}`, kind: 'text', lines }],
      status: 'submitted',
      results: [],
      approved: false,
      remote: {
        uploadId: record.uploadId,
        fileId: f.fileId,
        fileName: f.originalName,
        attemptId: f.attemptId,
        checkId: f.checkId,
        assignmentRev: record.rev,
      },
    }
    update((s) => ({ ...s, submissions: prev ? patch(s.submissions, id, () => sub) : [...s.submissions, sub] }))
    // проверку запускаем, когда сдача уже в состоянии (обновление React применяется не сразу)
    if (lines.length)
      void (async () => {
        for (let k = 0; k < 30 && !getState().submissions.some((x) => x.id === id && x.remote?.fileId === f.fileId); k++)
          await sleep(100)
        await process(id)
      })()
  }

  /** Читает состояние загрузки, пока кабинет подбирает учеников. */
  const poll = async (record: CabinetUploadRecord) => {
    for (let k = 0; k < 20; k++) {
      const up = await readUpload(record.uploadId)
      setItem(record.uploadId, (x) => ({ ...x, files: up.files, sending: false }))
      up.files.filter((f) => f.status === 'bound').forEach((f) => attach(record, f))
      if (!up.files.some((f) => f.status === 'pending_binding' || f.status === 'matched')) return
      await sleep(1500)
    }
  }

  // прошлые загрузки этой работы и класса — показать и досмотреть
  useEffect(() => {
    const records = (state.uploads ?? []).filter((r) => r.assignmentId === a.id && r.classId === cls.id)
    setItems(records.map((record) => ({ record, files: [], sending: true })))
    records.forEach((r) =>
      poll(r).catch((e) => setItem(r.uploadId, (x) => ({ ...x, sending: false, error: e instanceof Error ? e.message : 'Ошибка' }))),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onFiles = async (picked: File[]) => {
    if (!picked.length) return
    setBusy(true)
    try {
      // работа должна быть на сервере (и в актуальной ревизии) — иначе некуда загружать
      const remote = (await publish(a.id))?.[cls.id]
      if (!remote) throw new Error('Работа не сохранена на сервере для этого класса.')
      for (const file of picked) {
        try {
          const texts: Record<string, string> = {}
          const buf = await file.arrayBuffer()
          if (/\.zip$/i.test(file.name)) {
            for (const e of await unzip(buf))
              if (/\.docx$/i.test(e.name)) texts[baseName(e.name)] = await docxText(e.data.slice().buffer as ArrayBuffer)
          } else texts[file.name] = await docxText(buf)
          const { uploadId } = await uploadWork(remote.assignmentId, remote.rev, file)
          const record: CabinetUploadRecord = {
            uploadId,
            assignmentId: a.id,
            classId: cls.id,
            name: file.name,
            createdAt: Date.now(),
            rev: remote.rev,
            texts,
          }
          update((s) => ({ ...s, uploads: [...(s.uploads ?? []), record] }))
          setItems((list) => [{ record, files: [], sending: true }, ...list])
          await poll(record)
        } catch (e) {
          toast(`${file.name}: ${e instanceof Error ? e.message : 'ошибка загрузки'}`, 'err')
        }
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка загрузки', 'err')
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (record: CabinetUploadRecord, f: UploadFile) => {
    const picked = choice[f.fileId]
    if (!picked) return
    try {
      const isNew = picked === NEW
      const name = (newName[f.fileId] ?? '').trim()
      if (isNew && !name) return
      const r = isNew
        ? await bindNewStudent(record.uploadId, f.fileId, name)
        : await bindFile(record.uploadId, f.fileId, picked)
      if (isNew) {
        // кабинет добавил ученика в класс — обновляем список учеников с сервера
        const remote = await pullClasses()
        update((s) => withRemoteClasses(s, remote))
        for (let k = 0; k < 20 && !getState().classes.some((c) => c.students.some((x) => x.id === r.studentId)); k++)
          await sleep(100)
      }
      const bound = { ...f, status: 'bound' as const, studentId: r.studentId, attemptId: r.attemptId, checkId: r.checkId }
      setItem(record.uploadId, (x) => ({ ...x, files: x.files.map((y) => (y.fileId === f.fileId ? bound : y)) }))
      attach(record, bound)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось подтвердить ученика', 'err')
    }
  }

  const students = state.classes.find((c) => c.id === cls.id)?.students ?? cls.students
  const name = (id?: string) => students.find((s) => s.id === id)?.name
  /** тот же файл этого ученика уже сдан раньше — кабинет вернул ту же попытку */
  const repeat = (f: UploadFile) =>
    !!f.attemptId &&
    state.submissions.some((s) => s.remote?.attemptId === f.attemptId && s.remote.fileId !== f.fileId)

  return (
    <Modal
      title={`Работы учеников · ${cls.name}`}
      onClose={onClose}
      wide
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Закрыть
          </button>
          <button className="btn btn--primary" disabled={busy} onClick={() => fileRef.current?.click()}>
            {busy ? 'Загрузка…' : 'Выбрать DOCX или ZIP'}
          </button>
        </>
      }
    >
      <input
        ref={fileRef}
        type="file"
        accept=".docx,.zip,application/zip,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        multiple
        hidden
        onChange={(e) => {
          void onFiles(Array.from(e.target.files ?? []))
          e.target.value = ''
        }}
      />
      {!items.length ? (
        <p className="muted">DOCX до 20 МБ или ZIP до 50 МБ (до 100 работ).</p>
      ) : (
        <ul className="cab-up">
          {items.map(({ record, files, error, sending }) => (
            <li key={record.uploadId}>
              <div className="cab-up__head">
                <b>{record.name}</b>
                <span className="muted small">
                  {new Date(record.createdAt).toLocaleString('ru-RU', {
                    day: 'numeric',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                  {' · ревизия работы '}
                  {record.rev}
                </span>
              </div>
              {error && <p className="err-text">{error}</p>}
              {!error && sending && !files.length && <p className="muted small">Загрузка состояния…</p>}
              <ul>
                {files.map((f) => (
                  <li key={f.fileId} className="cab-up__file">
                    <span className="cab-up__name">{baseName(f.originalName)}</span>
                    <Badge tone={STATUS[f.status][1]}>{STATUS[f.status][0]}</Badge>
                    {f.studentId && <span>{name(f.studentId) ?? 'ученик'}</span>}
                    {f.status === 'bound' && repeat(f) && <Badge tone="info">повторная сдача того же файла</Badge>}
                    {f.reason && f.status !== 'bound' && <span className="muted small">{f.reason}</span>}
                    {f.status === 'needs_confirmation' && (
                      <span className="cab-up__pick">
                        <select
                          className="input input--sm"
                          value={choice[f.fileId] ?? ''}
                          onChange={(e) => setChoice((c) => ({ ...c, [f.fileId]: e.target.value }))}
                        >
                          <option value="">— ученик —</option>
                          {/* сначала кандидаты кабинета, затем остальные ученики класса */}
                          {[
                            ...(f.candidates ?? []).map((c) => ({ id: c.studentId, name: c.name })),
                            ...students.filter((s) => !f.candidates?.some((c) => c.studentId === s.id)),
                          ].map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.name}
                            </option>
                          ))}
                          <option value={NEW}>+ Новый ученик…</option>
                        </select>
                        {choice[f.fileId] === NEW && (
                          <input
                            className="input input--sm"
                            placeholder="Фамилия Имя"
                            value={newName[f.fileId] ?? ''}
                            onChange={(e) => setNewName((n) => ({ ...n, [f.fileId]: e.target.value }))}
                          />
                        )}
                        <button
                          className="btn"
                          disabled={!choice[f.fileId] || (choice[f.fileId] === NEW && !(newName[f.fileId] ?? '').trim())}
                          onClick={() => void confirm(record, f)}
                        >
                          Подтвердить
                        </button>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}
