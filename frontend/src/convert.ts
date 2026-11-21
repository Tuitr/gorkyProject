// Кабинет принимает DOCX, ZIP со сдачами и фото JPG/PNG/WebP.
// Остальные поддерживаемые форматы переводим в браузере перед отправкой.

export const extension = (f: File) => f.name.split('.').pop()?.toLowerCase() ?? ''
const rename = (name: string, ext: string) => name.replace(/\.[^.]+$/, '') + '.' + ext

/** Все форматы, которые можно выбрать. */
export const FORMATS = ['docx', 'txt', 'zip', 'jpg', 'jpeg', 'jfif', 'png', 'webp', 'heic', 'heif']
export const PHOTO_FORMATS = ['jpg', 'jpeg', 'jfif', 'png', 'webp', 'heic', 'heif']
export const TASK_FORMATS = ['docx', 'txt', ...PHOTO_FORMATS]

/** Готовит файл к отправке: HEIC → JPG, TXT → DOCX, остальное как есть. */
export async function prepare(file: File): Promise<File> {
  const ext = extension(file)
  if (ext === 'heic' || ext === 'heif') return heicToJpg(file)
  if (ext === 'txt') return txtToDocx(file)
  // .jfif — тот же JPEG (так его часто сохраняет Windows), но кабинет узнаёт фото только по .jpg/.jpeg.
  if (ext === 'jfif') return new File([file], rename(file.name, 'jpg'), { type: 'image/jpeg' })
  return file
}

async function heicToJpg(file: File): Promise<File> {
  const { default: heic2any } = await import('heic2any')
  let out: Blob | Blob[]
  try {
    out = await heic2any({ blob: file, toType: 'image/jpeg', quality: 0.9 })
  } catch {
    throw new Error('Не удалось прочитать фото HEIC')
  }
  const blob = Array.isArray(out) ? out[0] : out
  return new File([blob], rename(file.name, 'jpg'), { type: 'image/jpeg' })
}

async function txtToDocx(file: File): Promise<File> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  // Блокнот Windows часто сохраняет в cp1251: если UTF-8 не читается, пробуем её.
  let text: string
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    text = new TextDecoder('windows-1251').decode(bytes)
  }
  text = text.replace(/^﻿/, '')
  if (!text.trim()) throw new Error('Текстовый файл пустой')

  const { Document, Packer, Paragraph, TextRun } = await import('docx')
  const doc = new Document({
    sections: [{ children: text.split(/\r?\n/).map((line) => new Paragraph({ children: [new TextRun(line)] })) }],
  })
  const blob = await Packer.toBlob(doc)
  return new File([blob], rename(file.name, 'docx'), {
    type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  })
}
