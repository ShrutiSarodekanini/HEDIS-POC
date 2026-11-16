# The API: Tesseract for document upload (intake runs in-process) and Node for Run Calculation (measure engine).
FROM hedis-base:latest

# libstdc++ is needed by the Node binary.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
RUN node --version

COPY --from=hedis-engine:latest --chown=app:app /engine/build services/measure_calc/engine/build
COPY --from=hedis-engine:latest --chown=app:app /engine/node_modules services/measure_calc/engine/node_modules

USER app
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
