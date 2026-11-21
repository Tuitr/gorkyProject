import { useEffect, useRef, type ReactNode } from 'react'

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

// Кнопки внизу формы окна: «Отмена» и основное действие (кнопка отправки формы).
export function DialogActions({ busy, onCancel, cancelLabel = 'Отмена', disabled = false, children }: {
  busy: boolean; onCancel: () => void; cancelLabel?: string; disabled?: boolean; children: ReactNode
}) {
  return <div className="actions"><button type="button" className="button" disabled={busy} onClick={onCancel}>{cancelLabel}</button>
    <button className="button primary" disabled={busy || disabled}>{children}</button></div>
}
