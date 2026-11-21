import type { CSSProperties } from 'react'

const COLOR: Record<number, string> = { 1: '#b91c1c', 2: '#dc2626', 3: '#d97706', 4: '#16a34a', 5: '#15803d' }

// Оценка учителя 1–5. Повторный клик по выбранной оценке снимает её.
export function GradePicker(props: {
  grade: number | null | undefined
  disabled?: boolean
  title?: string
  size?: 'small' | 'large'
  onChange: (grade: number | null) => void
}) {
  const big = props.size === 'large'
  return (
    <div style={st.row} title={props.title} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      {[1, 2, 3, 4, 5].map((g) => {
        const active = props.grade === g
        return (
          <button
            key={g}
            disabled={props.disabled}
            aria-pressed={active}
            aria-label={`Оценка ${g}`}
            onClick={() => props.onChange(active ? null : g)}
            style={{
              ...st.button,
              ...(big ? st.big : {}),
              ...(active ? { background: COLOR[g], borderColor: COLOR[g], color: '#fff' } : {}),
            }}
          >
            {g}
          </button>
        )
      })}
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  row: { display: 'flex', gap: 4 },
  button: { width: 28, height: 28, padding: 0, fontSize: 14, fontWeight: 600, borderRadius: 6 },
  big: { width: 40, height: 40, fontSize: 18, borderRadius: 8 },
}
