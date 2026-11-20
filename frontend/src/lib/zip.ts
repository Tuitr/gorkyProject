/**
 * Минимальная работа с ZIP в браузере без библиотек:
 * - zipStore — архив без сжатия (для DOCX, который собираем из распознанных заданий);
 * - unzip — чтение архива (stored и deflate через DecompressionStream) — DOCX из ZIP с работами учеников;
 * - makeDocx — простой DOCX из абзацев (кабинет из main принимает работу только в DOCX).
 */

const CRC_TABLE = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c >>> 0
  }
  return t
})()

function crc32(data: Uint8Array) {
  let c = 0xffffffff
  for (let i = 0; i < data.length; i++) c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

/** altName — то же имя в CP437 (так его прочитает Python), если в архиве нет отметки UTF-8 */
export type ZipEntry = { name: string; altName?: string; data: Uint8Array }

/** CP437 (кодировка ZIP по умолчанию в Python) — в браузерном TextDecoder её нет. */
const CP437_HIGH =
  'ÇüéâäàåçêëèïîìÄÅÉæÆôöòûùÿÖÜ¢£¥₧ƒáíóúñÑªº¿⌐¬½¼¡«»░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀αßΓπΣσµτΦΘΩδ∞φε∩≡±≥≤⌠⌡÷≈°∙·√ⁿ²■ '
export function decodeCp437(bytes: Uint8Array) {
  let s = ''
  for (const b of bytes) s += b < 128 ? String.fromCharCode(b) : CP437_HIGH[b - 128]
  return s
}

/** ZIP без сжатия (метод 0). Имена — UTF-8. */
export function zipStore(entries: ZipEntry[]): Uint8Array<ArrayBuffer> {
  const enc = new TextEncoder()
  const locals: Uint8Array[] = []
  const centrals: Uint8Array[] = []
  let offset = 0
  for (const e of entries) {
    const name = enc.encode(e.name)
    const crc = crc32(e.data)
    const local = new Uint8Array(30 + name.length)
    const lv = new DataView(local.buffer)
    lv.setUint32(0, 0x04034b50, true)
    lv.setUint16(4, 20, true) // версия
    lv.setUint16(6, 0x0800, true) // имена в UTF-8
    lv.setUint16(8, 0, true) // без сжатия
    lv.setUint32(14, crc, true)
    lv.setUint32(18, e.data.length, true)
    lv.setUint32(22, e.data.length, true)
    lv.setUint16(26, name.length, true)
    local.set(name, 30)

    const central = new Uint8Array(46 + name.length)
    const cv = new DataView(central.buffer)
    cv.setUint32(0, 0x02014b50, true)
    cv.setUint16(4, 20, true)
    cv.setUint16(6, 20, true)
    cv.setUint16(8, 0x0800, true)
    cv.setUint16(10, 0, true)
    cv.setUint32(16, crc, true)
    cv.setUint32(20, e.data.length, true)
    cv.setUint32(24, e.data.length, true)
    cv.setUint16(28, name.length, true)
    cv.setUint32(42, offset, true)
    central.set(name, 46)

    locals.push(local, e.data)
    centrals.push(central)
    offset += local.length + e.data.length
  }
  const centralSize = centrals.reduce((n, c) => n + c.length, 0)
  const end = new Uint8Array(22)
  const ev = new DataView(end.buffer)
  ev.setUint32(0, 0x06054b50, true)
  ev.setUint16(8, entries.length, true)
  ev.setUint16(10, entries.length, true)
  ev.setUint32(12, centralSize, true)
  ev.setUint32(16, offset, true)

  const out = new Uint8Array(offset + centralSize + 22)
  let p = 0
  for (const part of [...locals, ...centrals, end]) {
    out.set(part, p)
    p += part.length
  }
  return out
}

async function inflateRaw(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([data as Uint8Array<ArrayBuffer>]).stream().pipeThrough(new DecompressionStream('deflate-raw'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

/** Читает ZIP по центральному каталогу. Каталоги и неподдерживаемые методы сжатия пропускаются. */
export async function unzip(buf: ArrayBuffer): Promise<ZipEntry[]> {
  const v = new DataView(buf)
  const bytes = new Uint8Array(buf)
  let eocd = -1
  for (let i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 22 - 0xffff); i--) {
    if (v.getUint32(i, true) === 0x06054b50) {
      eocd = i
      break
    }
  }
  if (eocd < 0) throw new Error('Файл не является ZIP-архивом')
  const count = v.getUint16(eocd + 10, true)
  let p = v.getUint32(eocd + 16, true)
  const utf8 = new TextDecoder('utf-8')
  const cp866 = (() => {
    try {
      return new TextDecoder('ibm866')
    } catch {
      return utf8
    }
  })()
  const out: ZipEntry[] = []
  for (let i = 0; i < count; i++) {
    if (v.getUint32(p, true) !== 0x02014b50) break
    const flags = v.getUint16(p + 8, true)
    const method = v.getUint16(p + 10, true)
    const size = v.getUint32(p + 20, true)
    const nameLen = v.getUint16(p + 28, true)
    const extraLen = v.getUint16(p + 30, true)
    const commentLen = v.getUint16(p + 32, true)
    const localOffset = v.getUint32(p + 42, true)
    const rawName = bytes.subarray(p + 46, p + 46 + nameLen)
    // имена без флага UTF-8 в архивах из Windows обычно в CP866;
    // Python (кабинет) читает такие имена как CP437 — запоминаем и это прочтение, чтобы сопоставить файлы
    const utf = !!(flags & 0x0800)
    const name = (utf ? utf8 : cp866).decode(rawName)
    const altName = utf ? undefined : decodeCp437(rawName)
    p += 46 + nameLen + extraLen + commentLen
    if (name.endsWith('/')) continue
    const lNameLen = v.getUint16(localOffset + 26, true)
    const lExtraLen = v.getUint16(localOffset + 28, true)
    const start = localOffset + 30 + lNameLen + lExtraLen
    const data = bytes.subarray(start, start + size)
    if (method === 0) out.push({ name, altName, data: data.slice() })
    else if (method === 8) out.push({ name, altName, data: await inflateRaw(data) })
  }
  return out
}

const esc = (s: string) =>
  s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // недопустимые в XML управляющие символы
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')

/** Простой DOCX: каждый элемент — абзац; bold — жирный абзац. */
export function makeDocx(paragraphs: { text: string; bold?: boolean }[]): Blob {
  const enc = new TextEncoder()
  const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
  const body = paragraphs
    .map(
      (p) =>
        `<w:p><w:r>${p.bold ? '<w:rPr><w:b/></w:rPr>' : ''}<w:t xml:space="preserve">${esc(p.text)}</w:t></w:r></w:p>`,
    )
    .join('')
  const files: ZipEntry[] = [
    {
      name: '[Content_Types].xml',
      data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
          '<Default Extension="xml" ContentType="application/xml"/>' +
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
          '</Types>',
      ),
    },
    {
      name: '_rels/.rels',
      data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
          '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>' +
          '</Relationships>',
      ),
    },
    {
      name: 'word/document.xml',
      data: enc.encode(
        `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="${W}"><w:body>${body}<w:sectPr/></w:body></w:document>`,
      ),
    },
  ]
  return new Blob([zipStore(files)], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
}
