FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends exiftool && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml .
RUN uv sync --no-dev

COPY server.py .

EXPOSE 8051

CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8051"]
