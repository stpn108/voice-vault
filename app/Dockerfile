FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Application modules ---
# All top-level .py files are copied; tests and tooling are excluded via
# .dockerignore. A new module never needs a Dockerfile change (template decision T-001).
COPY *.py .

# --- Templates & static assets ---
COPY templates ./templates

# Build-time version info
ARG APP_VERSION=0.0
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown
ENV APP_VERSION=$APP_VERSION
ENV GIT_COMMIT=$GIT_COMMIT
ENV BUILD_TIME=$BUILD_TIME

CMD ["python", "main.py"]
