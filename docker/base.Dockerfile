# Shared base for every Python component: Python packages and the code, no Tesseract and no Node.
#   docker compose -p hedis-deploy -f docker-compose.deploy.yml --profile build build base engine
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dataset uploads are saved under ./.uploaded_datasets (fixed in the code); the link puts them in the shared volume.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/logs /data/work /data/generated_evidence /data/uploaded_datasets \
    && ln -s /data/uploaded_datasets /app/.uploaded_datasets \
    && chown -R app:app /app /data

WORKDIR /app
COPY requirements.txt ./
# requirements.txt has no upper bound on fastapi; 0.16x breaks prometheus-fastapi-instrumentator 7.1 (500 on every
# route). Pin the version the dev environment runs.
ARG FASTAPI_VERSION=0.136.3
RUN pip install -r requirements.txt "fastapi==${FASTAPI_VERSION}"

COPY --chown=app:app . .
