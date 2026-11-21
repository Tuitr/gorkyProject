import { useState } from 'react'
import { Dialog } from './Dialog'
import { ErrorText, errorText } from './ErrorText'

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
