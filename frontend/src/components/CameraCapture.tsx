import { useEffect, useRef, useState } from 'react'

type Props = {
  onCapture: (dataUrl: string) => void
  onClose: () => void
}

/** Окно камеры: живое видео → снимок → «Переснять» / «Проверить». */
export function CameraCapture({ onCapture, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [error, setError] = useState<string | null>(() =>
    typeof navigator.mediaDevices?.getUserMedia === "function" ? null : 'Браузер не даёт доступ к камере (нужен https или localhost).',
  )
  const [shot, setShot] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    if (typeof navigator.mediaDevices?.getUserMedia !== "function") return
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
            ? 'Доступ к камере запрещён. Разрешите его в настройках браузера.'
            : name === 'NotFoundError'
              ? 'Камера не найдена.'
              : 'Не удалось включить камеру.',
        )
      })
    return () => {
      cancelled = true
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const takeShot = () => {
    const video = videoRef.current
    if (!video || !video.videoWidth) return
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d')!.drawImage(video, 0, 0)
    setShot(canvas.toDataURL('image/jpeg', 0.9))
  }

  const onFile = (file?: File) => {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => onCapture(String(reader.result))
    reader.readAsDataURL(file)
  }

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Камера" onClick={onClose}>
      <div className="camera" onClick={(e) => e.stopPropagation()}>
        <div className="camera__head">
          <h2>Сфотографируйте работу</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>

        <div className="camera__frame">
          {error ? (
            <div className="camera__error">
              <p>{error}</p>
              <label className="btn btn--primary">
                Сделать фото телефоном / выбрать файл
                <input type="file" accept="image/*" capture="environment" hidden onChange={(e) => onFile(e.target.files?.[0])} />
              </label>
            </div>
          ) : (
            <>
              <video ref={videoRef} autoPlay playsInline muted hidden={!!shot} />
              {shot ? (
                <img src={shot} alt="Снимок работы" />
              ) : (
                <div className="camera__guide" aria-hidden="true" />
              )}
            </>
          )}
        </div>

        {!error && (
          <div className="camera__actions">
            {shot ? (
              <>
                <button className="btn" onClick={() => setShot(null)}>
                  Переснять
                </button>
                <button className="btn btn--primary" onClick={() => onCapture(shot)}>
                  Проверить
                </button>
              </>
            ) : (
              <>
                <span className="muted">Положите лист ровно, чтобы он занял всю рамку</span>
                <button className="shutter" onClick={takeShot} aria-label="Сделать снимок" />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
