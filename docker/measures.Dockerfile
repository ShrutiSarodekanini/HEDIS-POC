# Value sets and measure workers: Node runs the measure engine; no documents are read, so no Tesseract.
FROM hedis-base:latest

# libstdc++ is needed by the Node binary.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
RUN node --version

COPY --from=hedis-engine:latest --chown=app:app /engine/build services/measure_calc/engine/build
COPY --from=hedis-engine:latest --chown=app:app /engine/node_modules services/measure_calc/engine/node_modules

USER app
CMD ["python", "scripts/run_workers.py", "valuesets", "measures"]
