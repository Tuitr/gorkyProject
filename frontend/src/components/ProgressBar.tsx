// Полоска выполнения; share — доля от 0 до 1.
export function ProgressBar({ share, thin = false, label }: { share: number; thin?: boolean; label?: string }) {
  return <div className={'progress' + (thin ? ' thin' : '')} role={label ? 'img' : undefined} aria-label={label}>
    <span style={{ width: share * 100 + '%' }} />
  </div>
}
