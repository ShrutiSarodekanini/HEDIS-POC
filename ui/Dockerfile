# React UI served by nginx. Build from the repo root so the root .dockerignore applies:
#   docker build -f ui/Dockerfile .
FROM node:22-slim AS build
WORKDIR /app/ui

# Vite reads VITE_* from the build environment.
ARG VITE_APP_NAME=HEDIS
ARG VITE_API_BASE_URL=/api
ARG VITE_API_TIMEOUT_MS=15000
ARG VITE_API_OPENAPI_PATH=/openapi.json
ARG VITE_DASHBOARD_CATALOG_PATH=/star-rating/catalog
ARG VITE_DASHBOARD_SUMMARY_PATH=/star-dashboard/calculate
ARG VITE_DASHBOARD_DEFAULT_MEASURE_ID=BCS-E
ARG VITE_INTEGRATION_SEED_PATH=/star-dashboard/seed-sample-data
ARG VITE_INTEGRATION_PROCESS_PATH=/automation/process-gaps
ARG VITE_DEFAULT_USER_NAME="Local Reviewer"
ARG VITE_DEFAULT_USER_ROLE="Clinical Abstractor"

ENV VITE_APP_NAME=${VITE_APP_NAME} \
    VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_API_TIMEOUT_MS=${VITE_API_TIMEOUT_MS} \
    VITE_API_OPENAPI_PATH=${VITE_API_OPENAPI_PATH} \
    VITE_DASHBOARD_CATALOG_PATH=${VITE_DASHBOARD_CATALOG_PATH} \
    VITE_DASHBOARD_SUMMARY_PATH=${VITE_DASHBOARD_SUMMARY_PATH} \
    VITE_DASHBOARD_DEFAULT_MEASURE_ID=${VITE_DASHBOARD_DEFAULT_MEASURE_ID} \
    VITE_INTEGRATION_SEED_PATH=${VITE_INTEGRATION_SEED_PATH} \
    VITE_INTEGRATION_PROCESS_PATH=${VITE_INTEGRATION_PROCESS_PATH} \
    VITE_DEFAULT_USER_NAME=${VITE_DEFAULT_USER_NAME} \
    VITE_DEFAULT_USER_ROLE=${VITE_DEFAULT_USER_ROLE}

COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
# npm sometimes skips a package's optional platform binary (npm/cli#4828); a lock file made on Windows can miss the
# Linux one, without which Vite exits. Same fix as scripts/run_all.ps1 applies for Windows.
RUN if [ ! -d node_modules/@rolldown/binding-linux-x64-gnu ]; then \
        version=$(node -p "require('./node_modules/rolldown/package.json').version") \
        && npm install --no-save --no-audit --no-fund "@rolldown/binding-linux-x64-gnu@$version"; \
    fi
COPY ui/ ./
RUN npm run build

FROM nginx:alpine
ENV API_UPSTREAM=http://api:8000
COPY ui/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/ui/dist /usr/share/nginx/html
EXPOSE 80
