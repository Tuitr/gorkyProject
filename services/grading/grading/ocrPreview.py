"""Serve a local preview of the production photo transcriber."""

import os
from html import escape
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from grading.photoOcr import OcrRejected, OcrUnavailable, renderTranscript, transcribePhoto
from shared.dotenv import loadDotEnv
from shared.ocrconfig import parseOcrUrl
from shared.submissionmedia import MAX_SOURCE_BYTES, imageMediaType

HOST = "127.0.0.1"
PORT = 8765
ERRORS = {
    "ocr_not_configured": (503, "Задайте OCR_URL, OCR_API_KEY и OCR_MODEL в .env и перезапустите тестовый сервер."),
    "ocr_invalid_url": (503, "Проверьте HTTPS-адрес в OCR_URL."),
    "ocr_invalid_photo": (422, "Не удалось открыть фото. Выберите JPEG, PNG или WebP."),
    "ocr_incomplete": (422, "Распознавание не завершилось. Попробуйте более чёткое фото."),
}


def createApp() -> FastAPI:
    app = FastAPI(openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"])

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        try:
            hostname = parseOcrUrl(os.environ.get("OCR_URL", "")).hostname
            notice = f"Фото будет отправлено на {hostname}."
        except ValueError:
            notice = "Адрес сервиса распознавания задаётся в OCR_URL."
        page = Path(__file__).with_suffix(".html").read_text(encoding="utf-8")
        return page.replace("{{notice}}", escape(notice))

    @app.post("/transcribe")
    async def transcribe(request: Request) -> dict[str, str]:
        origin = request.headers.get("origin")
        if origin is not None and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Запрос должен идти со страницы теста.")

        original = bytearray()
        async for chunk in request.stream():
            if len(original) + len(chunk) > MAX_SOURCE_BYTES:
                raise HTTPException(413, "Фото должно быть не больше 20 МБ.")
            original.extend(chunk)

        photo = bytes(original)
        media = imageMediaType(photo)
        if media is None:
            raise HTTPException(422, "Выберите фото в JPEG, PNG или WebP.")
        try:
            lines = await run_in_threadpool(transcribePhoto, photo, media)
        except OcrRejected as error:
            status, detail = ERRORS.get(str(error), (502, "Сервис распознавания отклонил запрос."))
            raise HTTPException(status, detail) from None
        except OcrUnavailable:
            raise HTTPException(502, "Сервис распознавания недоступен. Попробуйте ещё раз.") from None
        return {"markdown": renderTranscript(lines)}

    return app


if __name__ == "__main__":
    loadDotEnv()
    uvicorn.run(createApp(), host=HOST, port=PORT)
