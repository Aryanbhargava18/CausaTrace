FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install --no-install-recommends -y make tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock .python-version README.md LICENSE Dockerfile Makefile .env.example ./
COPY src ./src
COPY tests ./tests
COPY examples ./examples

RUN uv sync --locked --all-groups

ENV PATH="/app/.venv/bin:${PATH}"

CMD ["make", "test"]
