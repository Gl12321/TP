FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_XET=1
WORKDIR /app
COPY requirements-bootstrap.txt ./
RUN pip install --no-cache-dir -r requirements-bootstrap.txt

FROM base AS prepare

COPY src ./src
COPY config.yaml ./
ENTRYPOINT ["python", "-m", "src.runtime.prepare"]

FROM base AS api

RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
ENV CMAKE_ARGS="-DGGML_CUDA=OFF -DGGML_NATIVE=OFF" CMAKE_BUILD_PARALLEL_LEVEL=2
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY config.yaml ./
EXPOSE 8000
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
