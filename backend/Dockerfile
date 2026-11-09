# API image. Build from the repository root so the shipped suites and agents
# are available for demo seeding: docker build -f backend/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    AGENTPROOF_HOME=/app

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra postgres --extra otlp --no-install-project

COPY backend/src ./src
RUN uv sync --frozen --no-dev --extra postgres --extra otlp

COPY suites /app/suites
COPY agents /app/agents

RUN useradd --system --uid 10001 agentproof
USER agentproof

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["agentproof", "serve", "--host", "0.0.0.0", "--port", "8000"]
