import { useActions } from '../actions'
import { ReferenceLine, ReferenceStatus } from '../components/ReferenceAnswers'
import { useReferences } from '../useReferences'
import { patch, useStore } from '../store'
import { AssignmentHeader, NotFound, useAssignment } from './common'

/**
 * Экран 4. Эталонные ответы и утверждение. Кабинет проверяет все работы по одному общему варианту —
 * DOCX, сохранённому на сервере; ответы к нему берёт из реестра решателей. Разные варианты пока не формируются.
 */
export function Variants({ id }: { id: string }) {
  const { update, toast } = useStore()
  const { publish } = useActions()
  const { a } = useAssignment(id)
  const refs = useReferences(a)
  if (!a) return <NotFound />

  const saved = !!a.remote && Object.keys(a.remote).length > 0

  return (
    <div className="page page--wide">
      <AssignmentHeader a={a} step="variants" />

      <div className="notice">
        Все ученики выполняют один общий вариант: кабинет проверяет работы по сохранённому DOCX и сверяет ответы с
        эталонами из реестра решателей. Формирование разных вариантов пока отключено.
      </div>

      <div className="toolbar">
        <span className="spacer" />
        {a.status === 'approved' ? (
          <span className="ok-line">✓ Утверждено</span>
        ) : (
          <button
            className="btn btn--primary"
            disabled={!a.tasks.length}
            onClick={() => {
              update((s) => ({ ...s, assignments: patch(s.assignments, a.id, (x) => ({ ...x, status: 'approved' })) }))
              void publish(a.id) // работа (новая ревизия, если менялась) уходит на сервер
              toast('Работа утверждена: все ученики выполняют один общий вариант')
            }}
          >
            Утвердить
          </button>
        )}
      </div>

      <section className="panel">
        <div className="panel__head">
          <h2>Эталонные ответы</h2>
        </div>
        <ReferenceStatus refs={refs} saved={saved} />
        <ol className="src-tasks">
          {a.tasks.map((t) => (
            <li key={t.id} className="src-task">
              <b>№{t.num}</b> <span className="vcell__text">{t.text}</span>
              <ReferenceLine refs={refs} num={t.num} />
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
