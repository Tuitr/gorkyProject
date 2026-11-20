import { useEffect, useState } from 'react'
import { setConfirmHost, type ConfirmRequest } from '../lib/confirm'
import { Modal } from '../ui'

/** Окно подтверждения для askConfirm(): одно на приложение. */
export function ConfirmHost() {
  const [req, setReq] = useState<ConfirmRequest | null>(null)

  useEffect(() => {
    setConfirmHost(setReq)
    return () => setConfirmHost(null)
  }, [])

  if (!req) return null
  const answer = (ok: boolean) => {
    req.resolve(ok)
    setReq(null)
  }
  return (
    <Modal
      title="Подтверждение"
      onClose={() => answer(false)}
      footer={
        <>
          <button className="btn" onClick={() => answer(false)}>
            Отмена
          </button>
          <button className={`btn ${req.danger ? 'btn--danger' : 'btn--primary'}`} autoFocus onClick={() => answer(true)}>
            {req.ok ?? 'Да'}
          </button>
        </>
      }
    >
      <p className="confirm__text">{req.text}</p>
    </Modal>
  )
}
