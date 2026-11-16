# Gap identifier worker: records care gaps from the measure results; no OCR and no engine.
FROM hedis-base:latest

USER app
CMD ["python", "scripts/run_workers.py", "gap_identifier"]
