import { useEffect } from 'react'
import { WORK_TYPE_LABEL } from '../domain'
import { useStore } from '../store'

/**
 * Печатные формы: «Сохранить как PDF» в окне печати браузера.
 * Ученические листы и листы ключей — РАЗНЫЕ страницы: ученический компонент ответы не получает вообще.
 */
export function PrintView({ path }: { path: string[] }) {
  const [kind, id] = path
  useEffect(() => {
    document.body.classList.add('print-mode')
    const t = setTimeout(() => print(), 500)
    return () => {
      clearTimeout(t)
      document.body.classList.remove('print-mode')
    }
  }, [])

  return (
    <div className="print">
      <div className="print__bar no-print">
        <button className="btn btn--primary" onClick={() => print()}>
          Печать / сохранить в PDF
        </button>
        <span className="muted small">В окне печати выберите «Сохранить как PDF».</span>
      </div>
      {kind === 'variants' ? (
        <StudentSheets id={id} />
      ) : kind === 'keys' ? (
        <KeySheet id={id} />
      ) : kind === 'practice' ? (
        <PracticeSheets id={id} />
      ) : kind === 'practice-keys' ? (
        <PracticeKeys id={id} />
      ) : (
        <p>Неизвестная форма</p>
      )}
    </div>
  )
}

function Missing() {
  return <p>Не найдено — откройте форму из приложения заново.</p>
}

/** Только условия: ответы сюда не передаются. */
function StudentSheets({ id }: { id: string }) {
  const { state } = useStore()
  const a = state.assignments.find((x) => x.id === id)
  if (!a) return <Missing />
  const variants = a.variants.slice(0, a.variantCount).map((v) => ({
    name: v.name,
    // вариант А всегда берём из исходника — его правят на экране «Исходник»
    tasks: a.tasks.map((t) => ({ num: t.num, text: v.name === 'А' ? t.text : (v.tasks.find((x) => x.taskId === t.id)?.text ?? t.text) })),
  }))
  return (
    <>
      {variants.map((v) => (
        <section key={v.name} className="sheet-page">
          <header className="sheet-page__head">
            <div>
              <b>{a.title}</b>
              <div>{WORK_TYPE_LABEL[a.type]}</div>
            </div>
            <div className="sheet-page__variant">Вариант {v.name}</div>
          </header>
          <div className="sheet-page__fio">Фамилия, имя ______________________________ Класс ______</div>
          <ol className="sheet-page__tasks">
            {v.tasks.map((t) => (
              <li key={t.num} value={Number.parseInt(t.num) || undefined}>
                <span className="n">№{t.num}.</span> {t.text}
              </li>
            ))}
          </ol>
        </section>
      ))}
    </>
  )
}

function KeySheet({ id }: { id: string }) {
  const { state } = useStore()
  const a = state.assignments.find((x) => x.id === id)
  if (!a) return <Missing />
  const vs = a.variants.slice(0, a.variantCount)
  return (
    <section className="sheet-page">
      <header className="sheet-page__head">
        <div>
          <b>{a.title}</b>
          <div>Лист ключей — только для учителя</div>
        </div>
      </header>
      <table className="keys">
        <thead>
          <tr>
            <th>№</th>
            {vs.map((v) => (
              <th key={v.id}>Вариант {v.name}</th>
            ))}
            <th>Макс.</th>
          </tr>
        </thead>
        <tbody>
          {a.tasks.map((t) => (
            <tr key={t.id}>
              <td>№{t.num}</td>
              {vs.map((v) => (
                <td key={v.id}>{v.tasks.find((x) => x.taskId === t.id)?.answer || '—'}</td>
              ))}
              <td>{t.max}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function PracticeSheets({ id }: { id: string }) {
  const { state } = useStore()
  const p = state.practices.find((x) => x.id === id)
  const a = state.assignments.find((x) => x.id === p?.assignmentId)
  const cls = state.classes.find((c) => c.id === p?.classId)
  if (!p || !a || !cls) return <Missing />
  const name = (sid: string) => cls.students.find((s) => s.id === sid)?.name ?? ''
  if (p.mode === 'board')
    return p.board ? (
      <section className="sheet-page">
        <header className="sheet-page__head">
          <b>Разбор ошибки — {a.title}</b>
        </header>
        <h3>Найдите ошибку в решении</h3>
        <p className="board-example">{p.board.example}</p>
        <h3>Задача</h3>
        <p>{p.board.task.text}</p>
      </section>
    ) : (
      <Missing />
    )
  return (
    <>
      {p.groups
        .filter((g) => g.tasks.length)
        .map((g) => (
          <section key={g.id} className="sheet-page">
            <header className="sheet-page__head">
              <div>
                <b>Работа над ошибками — {a.title}</b>
                <div>{p.mode === 'individual' ? g.name : `${g.name} · ${g.studentIds.map(name).join(', ')}`}</div>
              </div>
            </header>
            {p.mode !== 'individual' && <div className="sheet-page__fio">Фамилия, имя ______________________________</div>}
            <ol className="sheet-page__tasks">
              {g.tasks.map((t) => (
                <li key={t.id}>{t.text}</li>
              ))}
            </ol>
          </section>
        ))}
    </>
  )
}

function PracticeKeys({ id }: { id: string }) {
  const { state } = useStore()
  const p = state.practices.find((x) => x.id === id)
  const a = state.assignments.find((x) => x.id === p?.assignmentId)
  if (!p || !a) return <Missing />
  return (
    <section className="sheet-page">
      <header className="sheet-page__head">
        <div>
          <b>Ключи к практике — {a.title}</b>
          <div>Только для учителя</div>
        </div>
      </header>
      {p.mode === 'board' && p.board && (
        <p>
          Задача у доски: <b>{p.board.task.answer}</b>
        </p>
      )}
      {p.groups
        .filter((g) => g.tasks.length)
        .map((g) => (
          <div key={g.id} className="keys-group">
            <h3>{g.name}</h3>
            <ol>
              {g.tasks.map((t) => (
                <li key={t.id}>{t.answer || '—'}</li>
              ))}
            </ol>
          </div>
        ))}
    </section>
  )
}
