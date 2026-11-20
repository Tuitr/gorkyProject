# Image for the services of developers 2 and 3: grading (results API and worker), the reference registry and
# the model gateway. One image, the command chosen in deploy/compose.yaml.
FROM python:3.13-slim

WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.4

COPY pyproject.toml uv.lock ./
COPY services/cabinet/pyproject.toml services/cabinet/
COPY tools/dataset/pyproject.toml tools/dataset/
COPY contracts contracts
COPY packages/shared packages/shared
COPY services/grading services/grading
COPY services/references services/references
RUN uv sync --locked --package gorky-grading --package gorky-references --no-dev --no-cache

ENV PATH="/app/.venv/bin:${PATH}"
USER nobody
