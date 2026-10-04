FROM node:24.21.0-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_XET=1
WORKDIR /app
COPY requirements-bootstrap.txt ./
RUN pip install --no-cache-dir -r requirements-bootstrap.txt
COPY runtime/ ./runtime/
COPY config.yaml ./

FROM base AS prepare

ENTRYPOINT ["python", "-m", "runtime.prepare"]

FROM base AS backend
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY packages/sql_agent/ ./packages/sql_agent/
RUN pip install --no-cache-dir ./packages/sql_agent
COPY backend/ ./backend/
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /app/data && chown app:app /app/data
ENV APP_FRONTEND_DIST=/app/frontend/dist APP_CONFIG_PATH=/app/config.yaml

FROM backend AS api
COPY --from=frontend /build/dist ./frontend/dist/
USER app
EXPOSE 8000
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM backend AS worker
USER root
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake libgomp1 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
ENV CMAKE_ARGS="-DGGML_CUDA=OFF -DGGML_NATIVE=OFF" CMAKE_BUILD_PARALLEL_LEVEL=2
RUN pip install --no-cache-dir './packages/sql_agent[local]'
USER app
ENTRYPOINT ["python", "-m", "backend.app.worker"]
