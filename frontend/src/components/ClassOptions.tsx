import type { ClassItem } from '../api'

// Пункты <select> со списком классов.
export function ClassOptions({ classes }: { classes: ClassItem[] }) {
  return <>{classes.map((item) => <option key={item.classId} value={item.classId}>{item.name}</option>)}</>
}
