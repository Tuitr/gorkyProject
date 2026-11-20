/**
 * Подтверждение действия в стиле приложения (вместо системного confirm(), который блокирует страницу).
 * askConfirm('Удалить?') → Promise<boolean>; окно рисует ConfirmHost (components/ConfirmHost.tsx).
 */
export type ConfirmRequest = {
  text: string
  /** надпись кнопки подтверждения */
  ok?: string
  /** действие необратимое — кнопка красная */
  danger?: boolean
  resolve: (ok: boolean) => void
}

let show: ((r: ConfirmRequest) => void) | null = null

/** Подключает окно подтверждения (вызывает ConfirmHost при монтировании). */
export function setConfirmHost(fn: ((r: ConfirmRequest) => void) | null) {
  show = fn
}

export function askConfirm(text: string, opts: { ok?: string; danger?: boolean } = {}): Promise<boolean> {
  return new Promise((resolve) => {
    if (show) show({ text, ...opts, resolve })
    else resolve(window.confirm(text)) // окно ещё не подключено (например, до входа)
  })
}
