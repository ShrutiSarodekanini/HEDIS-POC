# Documents and evidence workers: Tesseract because attaching stored documents runs intake; no Node (no engine).
FROM hedis-base:latest

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

USER app
CMD ["python", "scripts/run_workers.py", "documents", "evidence"]
