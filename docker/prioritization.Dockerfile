# Gap prioritization worker: scores and ranks open gaps; no OCR and no engine.
FROM hedis-base:latest

USER app
CMD ["python", "scripts/run_workers.py", "gaps"]
