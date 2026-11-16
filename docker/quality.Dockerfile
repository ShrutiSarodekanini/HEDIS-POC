# Data quality worker: SQL checks on the loaded batch; no OCR and no engine.
FROM hedis-base:latest

USER app
CMD ["python", "scripts/run_workers.py", "quality"]
