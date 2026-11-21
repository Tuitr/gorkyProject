import { useState, type FormEvent } from 'react'
import { Dialog, DialogActions } from './Dialog'
import { ErrorText, errorText } from './ErrorText'

export function NameDialog({ title, label, placeholder, action, onClose }: {
  title: string; label: string; placeholder: string; action: (name: string) => Promise<void>; onClose: () => void
}) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try { await action(name.trim()); onClose() }
    catch (cause) { setError(errorText(cause)); setBusy(false) }
  }
  return <Dialog title={title} onClose={busy ? () => {} : onClose}>
    <form className="form" onSubmit={submit}>
      <label>{label}<input required autoFocus maxLength={120} placeholder={placeholder} value={name} onChange={(event) => setName(event.target.value)} /></label>
      <ErrorText message={error} />
      <DialogActions busy={busy} onCancel={onClose} disabled={!name.trim()}>{busy ? 'Сохраняем…' : 'Добавить'}</DialogActions>
    </form>
  </Dialog>
}
