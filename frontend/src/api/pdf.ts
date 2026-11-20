import { getDocument, GlobalWorkerOptions, InvalidPDFException, PasswordException } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { latexToText } from '../../server/latexToText'
import { MAX_OCR_IMAGE_SIDE, recognizeRaw } from './ocr'

GlobalWorkerOptions.workerSrc = workerUrl

export async function readPdfText(file: File): Promise<string> {
  const baseUrl = new URL(import.meta.env.BASE_URL, document.baseURI)
  const assets = new URL('pdfjs/', baseUrl).href
  const loading = getDocument({
    data: await file.arrayBuffer(),
    cMapUrl: `${assets}cmaps/`,
    iccUrl: `${assets}iccs/`,
    standardFontDataUrl: `${assets}standard_fonts/`,
    wasmUrl: `${assets}wasm/`,
    stopAtErrors: true,
  })
  try {
    const pdf = await loading.promise
    const canvas = document.createElement('canvas')
    const texts: string[] = []
    for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber++) {
      const page = await pdf.getPage(pageNumber)
      try {
        const size = page.getViewport({ scale: 1 })
        const viewport = page.getViewport({ scale: MAX_OCR_IMAGE_SIDE / Math.max(size.width, size.height) })
        canvas.width = Math.max(1, Math.ceil(viewport.width))
        canvas.height = Math.max(1, Math.ceil(viewport.height))
        await page.render({ canvas, viewport }).promise

        const content = canvas.toDataURL('image/jpeg', 0.9).split(',')[1]
        const { lines, latex } = await recognizeRaw(content, 'JPEG')
        const textLines = lines.length ? lines.map((line) => line.text) : latex.split(/\r?\n/)
        texts.push(textLines.map(latexToText).join('\n'))
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Не удалось обработать страницу'
        throw new Error(`Страница ${pageNumber}: ${message}`, { cause: error })
      } finally {
        page.cleanup()
        canvas.width = canvas.height = 0
      }
    }
    return texts.join('\n')
  } catch (error) {
    if (error instanceof PasswordException) throw new Error('PDF защищён паролем. Загрузите копию без пароля.')
    if (error instanceof InvalidPDFException) throw new Error('Не удалось открыть PDF. Проверьте файл и загрузите его снова.')
    throw error
  } finally {
    await loading.destroy()
  }
}
