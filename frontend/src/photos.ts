/** Работа с фото страниц в браузере: сжатие перед сохранением и поворот (поворот «запекается» в картинку). */

const MAX_SIDE = 1800

function load(src: string) {
  const img = new Image()
  img.src = src
  return img.decode().then(() => img)
}

function draw(img: HTMLImageElement, rotation: number, maxSide = MAX_SIDE) {
  const k = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight))
  const w = Math.round(img.naturalWidth * k)
  const h = Math.round(img.naturalHeight * k)
  const turned = rotation % 180 !== 0
  const canvas = document.createElement('canvas')
  canvas.width = turned ? h : w
  canvas.height = turned ? w : h
  const ctx = canvas.getContext('2d')!
  ctx.translate(canvas.width / 2, canvas.height / 2)
  ctx.rotate((rotation * Math.PI) / 180)
  ctx.drawImage(img, -w / 2, -h / 2, w, h)
  return canvas.toDataURL('image/jpeg', 0.85)
}

const readFile = (f: File) =>
  new Promise<string>((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result))
    r.onerror = () => reject(r.error)
    r.readAsDataURL(f)
  })

/** Файл или data:URL → сжатый JPEG (≈200–400 КБ), чтобы фото помещались в хранилище браузера. */
export async function compressImage(src: File | string) {
  const url = typeof src === 'string' ? src : await readFile(src)
  return draw(await load(url), 0)
}

export async function rotateImage(src: string, deg: 90 | -90 | 180) {
  return draw(await load(src), (deg + 360) % 360)
}
