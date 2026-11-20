import { Component, type ErrorInfo, type ReactNode } from 'react'

/**
 * Перехват ошибок отрисовки: вместо белого экрана — понятное сообщение и выход.
 * full — на весь экран (последняя линия защиты вокруг всего приложения);
 * без full — внутри страницы, шапка с навигацией остаётся рабочей.
 * Сбрасывается при смене key (у экранов — адрес страницы).
 */
type Props = { children: ReactNode; full?: boolean }
type State = { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Ошибка интерфейса:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className={this.props.full ? 'crash crash--full' : 'crash'} role="alert">
        <div className="crash__card">
          <h1>{this.props.full ? 'Приложение не смогло открыться' : 'Не удалось открыть этот раздел'}</h1>
          <p className="muted">
            Ваши данные не потеряны: они сохраняются отдельно. Обновите страницу или вернитесь к списку работ.
          </p>
          <div className="crash__actions">
            <button className="btn btn--primary" onClick={() => location.reload()}>
              Обновить страницу
            </button>
            <a
              className="btn"
              href="#/"
              onClick={() => {
                // на главную и сброс ошибки (в полноэкранном режиме — перезагрузка)
                if (this.props.full) setTimeout(() => location.reload(), 0)
                else this.setState({ error: null })
              }}
            >
              К списку работ
            </a>
          </div>
          <details className="crash__details">
            <summary>Текст ошибки</summary>
            <code>{error.message || String(error)}</code>
          </details>
        </div>
      </div>
    )
  }
}
