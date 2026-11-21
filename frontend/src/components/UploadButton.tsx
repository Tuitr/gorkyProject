import type { MouseEvent } from 'react'

export function UploadButton({ studentName, onClick }: { studentName: string; onClick: (event: MouseEvent) => void }) {
  return <button type="button" className="button primary compact" aria-label={'Загрузить решение ученика ' + studentName}
    onClick={onClick}>↑ Загрузить</button>
}
