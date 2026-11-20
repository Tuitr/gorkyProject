import { useEffect, useRef, useState } from 'react'
import { askConfirm } from '../lib/confirm'

type Props = {
  /** готовые снимки (data:URL); в обычном режиме — один */
  onCapture: (images: string[]) => void
  onClose: () => void
  /** несколько снимков подряд, отправка кнопкой «Готово» */
  multi?: boolean
  title?: string
}

const readAll = (files: FileList) =>
  Promise.all(
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

/** Окно камеры: живое видео → снимок → «Переснять» / «Отправить». В режиме multi снимки копятся. */
export function CameraCapture({ onCapture, onClose, multi = false, title = 'Сфотографируйте работу' }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [error, setError] = useState<string | null>(() =>
    typeof navigator.mediaDevices?.getUserMedia === 'function'
      ? null
      : 'Браузер не даёт доступ к камере на этой странице (нужен адрес https:// или localhost). Можно снять фото камерой телефона:',
  )
  const [shot, setShot] = useState<string | null>(null)
  const [shots, setShots] = useState<string[]>([])

  useEffect(() => {
    let cancelled = false
    if (typeof navigator.mediaDevices?.getUserMedia !== 'function') return
    navigator.mediaDevices
      .getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1440 } },
        audio: false,
      })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        streamRef.current = stream
        if (videoRef.current) videoRef.current.srcObject = stream
      })
      .catch((e: unknown) => {
        const name = e instanceof DOMException ? e.name : ''
        setError(
          name === 'NotAllowedError'
            ? 'Доступ к камере запрещён. Разрешите доступ в настройках браузера или снимите фото камерой устройства:'
            : name === 'NotFoundError'
              ? 'Камера не найдена. Можно выбрать готовое фото:'
              : 'Не удалось включить камеру. Можно снять фото камерой устройства:',
        )
      })
    return () => {
      cancelled = true
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  // закрытие с неотправленными снимками — только после подтверждения
  const close = async () => {
    if (multi && shots.length && !(await askConfirm(`Снимков не отправлено: ${shots.length}. Закрыть камеру без отправки?`, { ok: 'Закрыть', danger: true })))
      return
    onClose()
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && void close()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const takeShot = () => {
    const video = videoRef.current
    if (!video || !video.videoWidth) return
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d')!.drawImage(video, 0, 0)
    setShot(canvas.toDataURL('image/jpeg', 0.9))
  }

  const keep = () => {
    if (!shot) return
    if (multi) {
      setShots((l) => [...l, shot])
      setShot(null)
    } else onCapture([shot])
  }

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return
    const list = await readAll(files)
    if (multi) setShots((l) => [...l, ...list])
    else onCapture(list.slice(0, 1))
  }

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Камера" onClick={() => void close()}>
      <div className="camera" onClick={(e) => e.stopPropagation()}>
        <div className="camera__head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={() => void close()} aria-label="Закрыть">
            ✕
          </button>
        </div>

        <div className="camera__frame">
          {error ? (
            <div className="camera__error">
              <p>{error}</p>
              <label className="btn btn--primary">
                📷 Снять камерой устройства или выбрать фото
                <input
                  type="file"
                  accept="image/*"
                  capture="environment"
                  multiple={multi}
                  hidden
                  onChange={(e) => {
                    void onFiles(e.target.files)
                    e.target.value = ''
                  }}
                />
              </label>
            </div>
          ) : (
            <>
              <video ref={videoRef} autoPlay playsInline muted hidden={!!shot} />
              {shot ? <img src={shot} alt="Снимок работы" /> : <div className="camera__guide" aria-hidden="true" />}
            </>
          )}
        </div>

        {multi && shots.length > 0 && (
          <ol className="camera__shots">
            {shots.map((s, i) => (
              <li key={i}>
                <img src={s} alt={`Снимок ${i + 1}`} />
                <button
                  className="camera__shot-del"
                  title="Убрать снимок"
                  aria-label={`Убрать снимок ${i + 1}`}
                  onClick={() => setShots((l) => l.filter((_, j) => j !== i))}
                >
                  ✕
                </button>
              </li>
            ))}
          </ol>
        )}

        <div className="camera__actions">
          {shot ? (
            <>
              <button className="btn" onClick={() => setShot(null)}>
                Переснять
              </button>
              <button className="btn btn--primary" onClick={keep}>
                {multi ? 'Оставить снимок' : 'Отправить фото'}
              </button>
            </>
          ) : (
            <>
              <span className="muted small">{multi ? (shots.length ? `Снимков: ${shots.length}` : 'Снимите все страницы по очереди') : ''}</span>
              {!error && <button className="shutter" onClick={takeShot} aria-label="Сделать снимок" />}
              {multi ? (
                <button className="btn btn--primary" disabled={!shots.length} onClick={() => onCapture(shots)}>
                  Готово{shots.length ? ` (${shots.length})` : ''}
                </button>
              ) : (
                <span />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
