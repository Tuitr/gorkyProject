export function StatusBadge({ status }: { status: { label: string; tone: string } }) {
  return <span className={'status ' + status.tone}>{status.label}</span>
}
