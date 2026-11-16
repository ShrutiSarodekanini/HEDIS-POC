# The measure engine (fqm-execution) compiled with Node; api and measures copy its build and runtime packages.
FROM node:22-slim
WORKDIR /engine
COPY services/measure_calc/engine/package.json services/measure_calc/engine/package-lock.json ./
# --ignore-scripts: "prepare" runs the build, and the sources are not copied yet.
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY services/measure_calc/engine/ ./
RUN npm run build && test -f build/cli.js && npm prune --omit=dev --ignore-scripts --no-audit --no-fund
