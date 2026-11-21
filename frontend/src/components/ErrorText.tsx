export const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить действие.'

export function ErrorText({ message }: { message?: string }) {
  return message ? <p className="notice-error" role="alert">{message}</p> : null
}
