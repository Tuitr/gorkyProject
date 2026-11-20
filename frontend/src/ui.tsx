import { useEffect, useState, type ReactNode } from 'react'
import type { Job } from './domain'
import type { Box } from './types'

/** Статус всегда = цвет + подпись. */
export type Tone = 'ok' | 'err' | 'warn' | 'info' | 'muted'
export function Badge({ tone = 'muted', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {children}
    </span>
  )
}

export function PageHead({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <div className="muted">{sub}</div>}
      </div>
      {actions && <div className="page-head__actions">{actions}</div>}
    </div>
  )
}

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: { id: T; label: ReactNode }[]
  value: T
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((it) => (
        <button key={it.id} role="tab" aria-selected={it.id === value} onClick={() => onChange(it.id)}>
          {it.label}
        </button>
      ))}
    </div>
  )
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  title: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
}) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [onClose])
  return (
    <div className="modal" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog${wide ? ' dialog--wide' : ''}`} role="dialog" aria-modal="true">
        <div className="dialog__head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>
        <div className="dialog__body">{children}</div>
        {footer && <div className="dialog__foot">{footer}</div>}
      </div>
    </div>
  )
}

/** Боковая панель (открывается поверх страницы справа). */
export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [onClose])
  return (
    <div className="drawer-wrap" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="dialog__head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>
        <div className="drawer__body">{children}</div>
      </aside>
    </div>
  )
}

/** Состояние фоновой задачи + «Повторить» при сбое. */
export function JobStatus({ job, onRetry, idle }: { job?: Job; onRetry?: () => void; idle?: ReactNode }) {
  if (!job) return <>{idle}</>
  if (job.status === 'running')
    return (
      <span className="job">
        <span className="spinner spinner--sm" aria-hidden="true" /> {job.title}…
      </span>
    )
  if (job.status === 'error')
    return (
      <span className="job job--err">
        <Badge tone="err">Ошибка</Badge> <span className="small">{job.error}</span>
        {onRetry && (
          <button className="link-btn" onClick={onRetry}>
            Повторить
          </button>
        )}
      </span>
    )
  return <>{idle}</>
}

const pos = (b: Box) => ({ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` })

/** Фото страницы с рамками (ошибки — красные, выделение — синее). */
export function Photo({
  src,
  marks = [],
  alt = 'Страница работы',
  onMark,
}: {
  src: string
  marks?: { id: string; box: Box; tone?: 'err' | 'focus' | 'warn'; label?: string }[]
  alt?: string
  onMark?: (id: string) => void
}) {
  return (
    <div className="photo">
      <img src={src} alt={alt} draggable={false} />
      {marks
        .filter((m) => m.box.w > 0)
        .map((m) => (
          <button
            key={m.id}
            className={`photo__mark photo__mark--${m.tone ?? 'err'}`}
            style={pos(m.box)}
            onClick={() => onMark?.(m.id)}
            aria-label={m.label ?? 'Фрагмент'}
            tabIndex={onMark ? 0 : -1}
          >
            {m.label && <span>{m.label}</span>}
          </button>
        ))}
    </div>
  )
}

/** Вырезанный фрагмент фото (для карточек очереди и панели ошибки). */
export function Crop({ src, box, height = 120 }: { src: string; box?: Box; height?: number }) {
  const [ratio, setRatio] = useState(0.75) // ширина/высота фото, уточняется после загрузки
  if (!box || box.w <= 0) return <img className="crop crop--full" src={src} alt="Фрагмент работы" style={{ maxHeight: height * 2 }} />
  const pad = 0.03
  const x = Math.max(0, box.x - pad)
  const y = Math.max(0, box.y - pad)
  const b = { x, y, w: Math.min(1 - x, box.w + 2 * pad), h: Math.min(1 - y, box.h + 2 * pad) }
  return (
    <div className="crop" style={{ aspectRatio: `${b.w * ratio} / ${b.h}` }}>
      <img
        src={src}
        alt="Фрагмент работы"
        onLoad={(e) => setRatio(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight || 0.75)}
        style={{ width: `${100 / b.w}%`, left: `${(-b.x / b.w) * 100}%`, top: `${(-b.y / b.h) * 100}%` }}
      />
    </div>
  )
}

export const plural = (n: number, one: string, few: string, many: string) => {
  const m10 = n % 10
  const m100 = n % 100
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many
}

/** Работа, сданная документом (DOCX через кабинет): фото нет — показываем её текст. */
export function TextPage({ lines, compact }: { lines?: { text: string }[]; compact?: boolean }) {
  return (
    <div className={`text-page${compact ? ' text-page--compact' : ''}`}>
      {lines?.length ? lines.map((l, i) => <p key={i}>{l.text}</p>) : <p className="muted">Текст документа не извлечён</p>}
    </div>
  )
}
