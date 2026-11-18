# Star rating worker: computes ratings from stored measure rates in Python; no OCR and no engine.
FROM hedis-base:latest

USER app
CMD ["python", "scripts/run_workers.py", "stars"]
