# One image for every Python component (api, outbox, workers, migrate, seed); only the start command differs.
#   docker compose -p hedis-deploy -f docker-compose.deploy.yml up -d --build

# The measure engine (fqm-execution) is compiled here; only its build output and runtime packages reach the image.
FROM node:22-slim AS engine
WORKDIR /engine
COPY services/measure_calc/engine/package.json services/measure_calc/engine/package-lock.json ./
# --ignore-scripts: "prepare" runs the build, and the sources are not copied yet.
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY services/measure_calc/engine/ ./
RUN npm run build && test -f build/cli.js && npm prune --omit=dev --ignore-scripts --no-audit --no-fund

FROM node:22-slim AS node

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract reads scanned documents (document intake); libstdc++ is needed by the Node binary.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN node --version

# Dataset uploads are saved under ./.uploaded_datasets (fixed in the code); the link puts them in the shared volume.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/logs /data/work /data/generated_evidence /data/intake_store /data/uploaded_datasets \
    && ln -s /data/uploaded_datasets /app/.uploaded_datasets \
    && chown -R app:app /app /data

WORKDIR /app
COPY requirements.txt ./
# requirements.txt has no upper bound on fastapi; 0.16x breaks prometheus-fastapi-instrumentator 7.1 (500 on every
# route). Pin the version the dev environment runs.
ARG FASTAPI_VERSION=0.136.3
RUN pip install -r requirements.txt "fastapi==${FASTAPI_VERSION}"

COPY --chown=app:app . .
COPY --from=engine --chown=app:app /engine/build services/measure_calc/engine/build
COPY --from=engine --chown=app:app /engine/node_modules services/measure_calc/engine/node_modules

USER app
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
