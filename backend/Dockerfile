# Backend image: FastAPI service, the migration runner and the Go ingest CLI.
# Build context = repository root:  docker build -f backend/Dockerfile .
ARG REGISTRY=
FROM ${REGISTRY}golang:1.24-bookworm AS ingest-build
WORKDIR /src/ingest-go
COPY ingest-go/go.mod ingest-go/go.sum ./
RUN go mod download
COPY ingest-go/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/finrag-ingest ./cmd/finrag-ingest

FROM ${REGISTRY}python:3.13-slim
RUN pip install --no-cache-dir uv==0.11.32 \
 && useradd --system --create-home --uid 10001 finrag \
 && mkdir -p /models /app/cache /app/backend && chown -R finrag /models /app
WORKDIR /app/backend
# dependencies first: this layer is rebuilt only when the lockfile changes
COPY --chown=finrag backend/pyproject.toml backend/uv.lock ./
USER finrag
RUN uv sync --frozen --no-dev --group embed --no-install-project
COPY --chown=finrag backend/app ./app
COPY --chown=finrag db /app/db
COPY --from=ingest-build /out/finrag-ingest /usr/local/bin/finrag-ingest
# sentence-transformers downloads bge models on first use; mount a volume at /models to keep them
ENV PATH=/app/backend/.venv/bin:$PATH \
    HF_HOME=/models \
    UV_NO_SYNC=1 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "app.api.main:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
