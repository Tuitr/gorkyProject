import { zipStore, type ZipEntry } from './zip'

/**
 * Минимальная запись .xlsx без библиотек: книга из листов, первая строка — шапка
 * (жирная, закреплена, с фильтром), числа — настоящие числа Excel.
 */
export type XCell =
  | string
  | number
  | null
  | undefined
  | { pct: number } // доля 0..1 → «85%»
  | { date: string } // ГГГГ-ММ-ДД → дата Excel «20.11.2026»
  | { num: number } // число с одним знаком после запятой
  | { bold: string } // жирный текст (строка «Итого»)

export type XSheet = {
  name: string
  /** ширина колонок в символах */
  widths: number[]
  header: string[]
  rows: XCell[][]
  /** колонки с длинным текстом — с переносом строк */
  wrap?: number[]
}

// индексы стилей в styles.xml (cellXfs)
const S = { text: 0, header: 1, pct: 2, date: 3, num: 4, wrap: 5, bold: 6 }

const esc = (s: string) =>
  s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')

function colName(i: number) {
  let s = ''
  for (let n = i + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s
  return s
}

function excelDate(iso: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return null
  return (Date.UTC(+m[1], +m[2] - 1, +m[3]) - Date.UTC(1899, 11, 30)) / 86_400_000
}

function cellXml(v: XCell, ref: string, wrap: boolean): string {
  if (v == null || v === '') return ''
  const str = (text: string, style: number) =>
    `<c r="${ref}" t="inlineStr"${style ? ` s="${style}"` : ''}><is><t xml:space="preserve">${esc(text.slice(0, 32000))}</t></is></c>`
  const num = (n: number, style: number) =>
    Number.isFinite(n) ? `<c r="${ref}"${style ? ` s="${style}"` : ''}><v>${n}</v></c>` : ''
  if (typeof v === 'string') return str(v, wrap ? S.wrap : S.text)
  if (typeof v === 'number') return num(v, S.text)
  if ('pct' in v) return num(v.pct, S.pct)
  if ('num' in v) return num(Math.round(v.num * 10) / 10, S.num)
  if ('bold' in v) return str(v.bold, S.bold)
  const d = excelDate(v.date)
  return d == null ? str(v.date, S.text) : num(d, S.date)
}

function sheetXml(sh: XSheet): string {
  const wrap = new Set(sh.wrap ?? [])
  const lastCol = colName(Math.max(0, sh.header.length - 1))
  const rows = [
    `<row r="1">${sh.header.map((h, c) => `<c r="${colName(c)}1" t="inlineStr" s="${S.header}"><is><t>${esc(h)}</t></is></c>`).join('')}</row>`,
    ...sh.rows.map(
      (row, r) => `<row r="${r + 2}">${row.map((v, c) => cellXml(v, `${colName(c)}${r + 2}`, wrap.has(c))).join('')}</row>`,
    ),
  ]
  const cols = sh.widths.map((w, i) => `<col min="${i + 1}" max="${i + 1}" width="${w}" customWidth="1"/>`).join('')
  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
    '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>' +
    '<sheetFormatPr defaultRowHeight="15"/>' +
    (cols ? `<cols>${cols}</cols>` : '') +
    `<sheetData>${rows.join('')}</sheetData>` +
    (sh.rows.length ? `<autoFilter ref="A1:${lastCol}${sh.rows.length + 1}"/>` : '') +
    '</worksheet>'
  )
}

const STYLES =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
  '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
  '<numFmts count="2"><numFmt numFmtId="164" formatCode="dd.mm.yyyy"/><numFmt numFmtId="165" formatCode="0.0"/></numFmts>' +
  '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>' +
  '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>' +
  '<fill><patternFill patternType="solid"><fgColor rgb="FFFFE3C2"/><bgColor indexed="64"/></patternFill></fill></fills>' +
  '<borders count="2"><border/><border><bottom style="thin"><color rgb="FFB0B7C3"/></bottom></border></borders>' +
  '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' +
  '<cellXfs count="7">' +
  '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' +
  '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>' +
  '<xf numFmtId="9" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>' +
  '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>' +
  '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>' +
  '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>' +
  '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>' +
  '</cellXfs>' +
  '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>' +
  '</styleSheet>'

/** Имя листа Excel: до 31 символа, без []:*?/\ и уникальное. */
function sheetNames(names: string[]) {
  const used = new Set<string>()
  return names.map((n) => {
    const base = n.replace(/[[\]:*?/\\]/g, ' ').trim().slice(0, 31) || 'Лист'
    let name = base
    for (let i = 2; used.has(name.toLowerCase()); i++) name = `${base.slice(0, 28)} ${i}`
    used.add(name.toLowerCase())
    return name
  })
}

export function makeXlsx(sheets: XSheet[]): Blob {
  const enc = new TextEncoder()
  const names = sheetNames(sheets.map((s) => s.name))
  const files: ZipEntry[] = [
    {
      name: '[Content_Types].xml',
      data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
          '<Default Extension="xml" ContentType="application/xml"/>' +
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' +
          sheets
            .map(
              (_, i) =>
                `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`,
            )
            .join('') +
          '</Types>',
      ),
    },
    {
      name: '_rels/.rels',
      data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
          '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
          '</Relationships>',
      ),
    },
    {
      name: 'xl/workbook.xml',
      data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
          `<sheets>${names.map((n, i) => `<sheet name="${esc(n)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join('')}</sheets>` +
          `<definedNames>${sheets
            .map((s, i) =>
              s.rows.length
                ? `<definedName name="_xlnm._FilterDatabase" localSheetId="${i}" hidden="1">'${esc(names[i].replace(/'/g, "''"))}'!$A$1:$${colName(Math.max(0, s.header.length - 1))}$${s.rows.length + 1}</definedName>`
                : '',
            )
            .join('')}</definedNames>` +
          '</workbook>',
      ),
    },
    {
      name: 'xl/_rels/workbook.xml.rels',
      data: enc.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
          sheets
            .map(
              (_, i) =>
                `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`,
            )
            .join('') +
          `<Relationship Id="rId${sheets.length + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>` +
          '</Relationships>',
      ),
    },
    { name: 'xl/styles.xml', data: enc.encode(STYLES) },
    ...sheets.map((s, i) => ({ name: `xl/worksheets/sheet${i + 1}.xml`, data: enc.encode(sheetXml(s)) })),
  ]
  return new Blob([zipStore(files)], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
}
