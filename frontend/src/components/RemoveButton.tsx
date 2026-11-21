// Круглый крестик удаления в углу карточки.
export function RemoveButton({ className, label, title, onClick }: {
  className: string; label: string; title: string; onClick: () => void
}) {
  return <button type="button" className={className} aria-label={label} title={title} onClick={onClick}>×</button>
}
