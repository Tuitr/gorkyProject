import { useRef, useState } from 'react'
import { useActions } from '../actions'
import { CameraCapture } from '../components/CameraCapture'
import { SUBMISSION_STATUS_LABEL, uid, type Page, type State, type Submission } from '../domain'
import { compressImage, rotateImage } from '../photos'
import { go, href } from '../router'
import { hashString, useStore } from '../store'
import { Badge, JobStatus, Modal, TextPage } from '../ui'
import { STATUS_TONE } from './Collect'
import { NotFound, useAssignment } from './common'

type Camera = { mode: 'add' } | { mode: 'replace'; pageId: string }

/** Экран 6. Карточка ученика: вариант, съёмка страниц, предпросмотр и правка, «Готово». */
export function StudentCard({ id, classId, studentId }: { id: string; classId?: string; studentId?: string }) {
  const { state, update, jobs, toast } = useStore()
  const { process } = useActions()
  const { a, classes } = useAssignment(id)
  const [camera, setCamera] = useState<Camera | null>(null)
  const [zoom, setZoom] = useState<string | null>(null)
  const [moving, setMoving] = useState<Page | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const cls = classes.find((c) => c.id === classId)
  const student = cls?.students.find((s) => s.id === studentId)
  if (!a || !cls || !student) return <NotFound what={!a ? 'Работа' : 'Ученик'} />

  const sub = state.submissions.find((s) => s.assignmentId === a.id && s.studentId === student.id)
  const job = sub ? jobs[`check:${sub.id}`] : undefined
  const idx = cls.students.findIndex((s) => s.id === student.id)
  const next = cls.students[idx + 1]

  /** Изменяет сдачу ученика, создавая её при первом действии. */
  const setSub = (fn: (s: Submission) => Submission) =>
    update((st) => {
      const cur = st.submissions.find((s) => s.assignmentId === a.id && s.studentId === student.id)
      const base: Submission = cur ?? {
        id: uid('s'),
        assignmentId: a.id,
        classId: cls.id,
        studentId: student.id,
        variantId: null,
        pages: [],
        status: 'none',
        results: [],
        approved: false,
      }
      const upd = fn(base)
      return { ...st, submissions: cur ? st.submissions.map((s) => (s.id === cur.id ? upd : s)) : [...st.submissions, upd] }
    })

  /** Правка страниц возвращает обработанную сдачу в «Сдано» — её надо проверить заново. */
  const touched = (s: Submission): Submission =>
    s.status === 'processed' || s.status === 'error' ? { ...s, status: s.pages.length ? 'submitted' : 'none' } : s

  const known = () =>
    new Set(state.submissions.filter((s) => s.assignmentId === a.id).flatMap((s) => s.pages.map((p) => p.hash)))

  const addImages = async (images: string[]) => {
    const seen = known()
    const pages: Page[] = []
    let dup = 0
    for (const raw of images) {
      const image = await compressImage(raw)
      const hash = hashString(image)
      if (seen.has(hash)) {
        dup++
        continue
      }
      seen.add(hash)
      pages.push({ id: uid('p'), image, hash })
    }
    if (pages.length) setSub((s) => touched({ ...s, pages: [...s.pages, ...pages] }))
    if (dup) toast(`Эта страница уже загружена, повторная загрузка пропущена (${dup})`, 'err')
  }

  const replacePage = async (pageId: string, raw: string) => {
    const image = await compressImage(raw)
    setSub((s) => touched({ ...s, pages: s.pages.map((p) => (p.id === pageId ? { id: p.id, image, hash: hashString(image) } : p)) }))
  }

  const rotate = async (p: Page) => {
    const image = await rotateImage(p.image, 90)
    setSub((s) => touched({ ...s, pages: s.pages.map((x) => (x.id === p.id ? { id: x.id, image, hash: hashString(image) } : x)) }))
  }

  const swap = (i: number, j: number) =>
    setSub((s) => {
      const pages = [...s.pages]
      ;[pages[i], pages[j]] = [pages[j], pages[i]]
      return touched({ ...s, pages })
    })

  const fromFiles = async (files: FileList) => {
    const list = await Promise.all(
      Array.from(files).map(
        (f) =>
          new Promise<string>((res, rej) => {
            const r = new FileReader()
            r.onload = () => res(String(r.result))
            r.onerror = () => rej(r.error)
            r.readAsDataURL(f)
          }),
      ),
    )
    await addImages(list)
  }

  const pages = sub?.pages ?? []
  // вариантов нет — все сдают одну общую работу (по исходным заданиям); один вариант — подставляется сам;
  // выбирать нужно, только если вариантов несколько
  const offered = a.variants.slice(0, a.variantCount)
  const needVariant = offered.length > 1
  const variantId = sub?.variantId ?? (offered.length === 1 ? offered[0].id : null)
  const canFinish = (!needVariant || !!variantId) && pages.length > 0
  const status = sub && sub.pages.length ? sub.status : 'none'

  const finish = (check: boolean) => {
    if (!sub) return
    setSub((s) => ({ ...s, variantId: s.variantId ?? variantId, status: s.status === 'processed' ? 'processed' : 'submitted' }))
    if (check) setTimeout(() => void process(sub.id), 0)
    toast(check ? `${student.name}: отправлено на проверку` : `${student.name}: сдано`)
    if (next) go('a', a.id, 's', cls.id, next.id)
    else go('a', a.id, 'collect', { c: cls.id })
  }

  return (
    <div className="page page--narrow student-card">
      <div className="muted small">
        <a href={href('a', a.id, 'collect', { c: cls.id })}>← {a.title} · {cls.name}</a>
      </div>
      <div className="page-head">
        <div>
          <h1>{student.name}</h1>
          <div className="muted">
            {cls.name} · <Badge tone={STATUS_TONE[status]}>{SUBMISSION_STATUS_LABEL[status]}</Badge>{' '}
            <JobStatus job={job} onRetry={() => sub && void process(sub.id)} />
          </div>
        </div>
        {sub?.status === 'processed' && (
          <a className="btn" href={href('a', a.id, 'report', sub.id)}>
            Отчёт
          </a>
        )}
      </div>

      {sub?.status === 'error' && sub.error && <div className="notice notice--err">{sub.error}</div>}

      {needVariant && (
        <section className="panel">
          <h2>
            Вариант <span className="req">обязательно</span>
          </h2>
          <div className="segmented segmented--big" role="radiogroup" aria-label="Вариант">
            {offered.map((v) => (
              <button
                key={v.id}
                role="radio"
                aria-checked={variantId === v.id}
                onClick={() => setSub((s) => touched({ ...s, variantId: v.id }))}
              >
                {v.name}
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="panel">
        <div className="panel__head">
          <h2>Страницы · {pages.length}</h2>
        </div>
        <div className="shoot">
          <button className="btn btn--primary btn--big" onClick={() => setCamera({ mode: 'add' })}>
            {pages.length ? 'Добавить фото страницы' : 'Отправить фото'}
          </button>
          <button className="btn btn--big" onClick={() => fileRef.current?.click()}>
            Выбрать фото из файлов
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files?.length) void fromFiles(e.target.files)
              e.target.value = ''
            }}
          />
        </div>

        {pages.length > 0 && (
          <ol className="pages">
            {pages.map((p, i) => (
              <li key={p.id} className="page-thumb">
                {p.kind === 'text' ? (
                  <div className="page-thumb__img">
                    <TextPage lines={p.lines} compact />
                  </div>
                ) : (
                <button className="page-thumb__img" onClick={() => setZoom(p.image)} aria-label={`Увеличить страницу ${i + 1}`}>
                  <img src={p.image} alt={`Страница ${i + 1}`} />
                  <span className="page-thumb__n">{i + 1}</span>
                </button>
                )}
                {p.kind !== 'text' && (
                <div className="page-thumb__tools">
                  <button className="icon-btn" title="Увеличить" onClick={() => setZoom(p.image)}>
                    🔍
                  </button>
                  <button className="icon-btn" title="Повернуть" onClick={() => void rotate(p)}>
                    ⟳
                  </button>
                  <button className="icon-btn" title="Заменить фото" onClick={() => setCamera({ mode: 'replace', pageId: p.id })}>
                    📷
                  </button>
                  <button className="icon-btn" title="Переместить на одну позицию выше" disabled={i === 0} onClick={() => swap(i, i - 1)}>
                    ←
                  </button>
                  <button className="icon-btn" title="Переместить на одну позицию ниже" disabled={i === pages.length - 1} onClick={() => swap(i, i + 1)}>
                    →
                  </button>
                  <button className="icon-btn" title="Перенести в работу другого ученика" onClick={() => setMoving(p)}>
                    ⇄
                  </button>
                  <button
                    className="icon-btn icon-btn--danger"
                    title="Удалить"
                    onClick={() =>
                      confirm(`Удалить страницу ${i + 1}?`) &&
                      setSub((s) => touched({ ...s, pages: s.pages.filter((x) => x.id !== p.id) }))
                    }
                  >
                    🗑
                  </button>
                </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      <div className="finish">
        {!canFinish && (
          <span className="muted small">{needVariant && !variantId ? 'Выберите вариант' : 'Добавьте как минимум одну страницу'}</span>
        )}
        <button className="btn btn--big" disabled={!canFinish} onClick={() => finish(false)}>
          Сохранить{next ? ' и перейти к следующему ученику' : ''}
        </button>
        <button className="btn btn--primary btn--big" disabled={!canFinish} onClick={() => finish(true)}>
          Сохранить и отправить на проверку
        </button>
      </div>

      {camera && (
        <CameraCapture
          onCapture={(img) => {
            if (camera.mode === 'add') void addImages([img])
            else void replacePage(camera.pageId, img)
            setCamera(null)
          }}
          onClose={() => setCamera(null)}
        />
      )}

      {zoom && (
        <Modal title="Страница" onClose={() => setZoom(null)} wide>
          <img className="zoom-img" src={zoom} alt="Страница крупно" />
        </Modal>
      )}

      {moving && sub && (
        <MovePage
          page={moving}
          from={sub}
          onClose={() => setMoving(null)}
          students={cls.students.filter((s) => s.id !== student.id)}
        />
      )}
    </div>
  )
}

/** Перенос страницы в сдачу другого ученика (сфотографировали не того). */
function MovePage({
  page,
  from,
  students,
  onClose,
}: {
  page: Page
  from: Submission
  students: { id: string; name: string }[]
  onClose: () => void
}) {
  const { update, toast } = useStore()
  const [to, setTo] = useState('')
  const move = () => {
    update((st: State) => {
      const target = st.submissions.find((s) => s.assignmentId === from.assignmentId && s.studentId === to)
      const strip = (s: Submission): Submission => {
        const pages = s.pages.filter((p) => p.id !== page.id)
        return { ...s, pages, status: pages.length ? 'submitted' : 'none', results: [] }
      }
      let subs = st.submissions.map((s) => (s.id === from.id ? strip(s) : s))
      if (target) subs = subs.map((s) => (s.id === target.id ? { ...s, pages: [...s.pages, page], status: 'submitted' } : s))
      else
        subs = [
          ...subs,
          {
            id: uid('s'),
            assignmentId: from.assignmentId,
            classId: from.classId,
            studentId: to,
            variantId: from.variantId,
            pages: [page],
            status: 'submitted',
            results: [],
            approved: false,
          },
        ]
      return { ...st, submissions: subs }
    })
    toast(`Страница перенесена: ${students.find((s) => s.id === to)?.name}`)
    onClose()
  }
  return (
    <Modal
      title="Перенести страницу"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn btn--primary" disabled={!to} onClick={move}>
            Перенести
          </button>
        </>
      }
    >
      <img className="move-thumb" src={page.image} alt="" />
      <label className="field">
        <span>Ученик</span>
        <select className="input" value={to} onChange={(e) => setTo(e.target.value)}>
          <option value="">— выберите ученика —</option>
          {students.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
    </Modal>
  )
}
