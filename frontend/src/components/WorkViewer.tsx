import type { Box, Work } from '../types'

type Props = {
  work: Work
  activeMistake: string | null
  onSelectMistake: (id: string | null) => void
  showMarks: boolean
  showTask: boolean
  dismissed: string[]
}

const pos = (b: Box) => ({
  left: `${b.x * 100}%`,
  top: `${b.y * 100}%`,
  width: `${b.w * 100}%`,
  height: `${b.h * 100}%`,
})

/** Фото работы с подсветкой ошибок (красные рамки) и ✓ у верных номеров. */
export function WorkViewer({ work, activeMistake, onSelectMistake, showMarks, showTask, dismissed }: Props) {
  if (showTask && work.taskImage) {
    return (
      <div className="viewer">
        <div className="viewer__paper">
          <img src={work.taskImage} alt="Текст задания" draggable={false} />
        </div>
      </div>
    )
  }

  const r = work.result
  const off = (id: string) => dismissed.includes(id)
  const taskOk = (taskId: string, ok: boolean) =>
    ok || (r?.mistakes.filter((m) => m.taskId === taskId).every((m) => off(m.id)) ?? false)

  return (
    <div className="viewer">
      <div className="viewer__paper">
        <img src={work.image} alt={work.title} draggable={false} />
        {showMarks && r && (
          <>
            {r.tasks
              .filter((t) => t.box.w > 0 && taskOk(t.id, t.ok))
              .map((t) => (
                <span
                  key={t.id}
                  className="mark-ok"
                  style={{ left: `${(t.box.x + t.box.w) * 100}%`, top: `${(t.box.y + t.box.h / 2) * 100}%` }}
                  title={`${t.label}: верно`}
                >
                  ✓
                </span>
              ))}
            {r.mistakes.map((m, i) =>
              off(m.id) || m.box.w <= 0 ? null : (
                <button
                  key={m.id}
                  className={`mark-err${activeMistake === m.id ? ' is-active' : ''}`}
                  style={pos(m.box)}
                  onClick={() => onSelectMistake(activeMistake === m.id ? null : m.id)}
                  aria-label={`Ошибка ${i + 1}: ${m.title}`}
                >
                  <span className="mark-err__num">{i + 1}</span>
                </button>
              ),
            )}
          </>
        )}
      </div>
    </div>
  )
}
