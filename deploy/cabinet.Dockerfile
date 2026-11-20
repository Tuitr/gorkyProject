FROM python:3.13-slim

WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.4

COPY pyproject.toml uv.lock ./
COPY services/grading/pyproject.toml services/grading/
COPY services/references/pyproject.toml services/references/
COPY tools/dataset/pyproject.toml tools/dataset/
COPY contracts contracts
COPY packages/shared packages/shared
COPY services/cabinet services/cabinet
RUN uv sync --locked --package gorky-cabinet --no-dev --no-cache

ENV PATH="/app/.venv/bin:${PATH}"
USER nobody
CMD ["sh", "-ec", "alembic -c services/cabinet/alembic.ini upgrade head && exec uvicorn cabinet.main:createApp --factory --host 0.0.0.0 --port 8000"]
