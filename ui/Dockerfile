# React UI served by nginx. Build from the repo root so the root .dockerignore applies:
#   docker build -f ui/Dockerfile .
FROM node:22-slim AS build
WORKDIR /app/ui

# Same values as .env.example; Vite reads VITE_* from the environment at build time.
ENV VITE_APP_NAME=HEDIS \
    VITE_API_BASE_URL=/api \
    VITE_API_TIMEOUT_MS=15000 \
    VITE_API_OPENAPI_PATH=/openapi.json \
    VITE_DASHBOARD_CATALOG_PATH=/star-rating/catalog \
    VITE_DASHBOARD_SUMMARY_PATH=/star-dashboard/calculate \
    VITE_DASHBOARD_DEFAULT_MEASURE_ID=BCS-E \
    VITE_INTEGRATION_SEED_PATH=/star-dashboard/seed-sample-data \
    VITE_INTEGRATION_PROCESS_PATH=/automation/process-gaps \
    VITE_DEFAULT_USER_NAME="Local Reviewer" \
    VITE_DEFAULT_USER_ROLE="Clinical Abstractor"

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
COPY ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/ui/dist /usr/share/nginx/html
EXPOSE 80
