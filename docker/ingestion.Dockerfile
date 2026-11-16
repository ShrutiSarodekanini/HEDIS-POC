# Conversion and load workers: turn uploaded datasets into FHIR and load them; no OCR and no engine.
FROM hedis-base:latest

USER app
CMD ["python", "scripts/run_workers.py", "conversion", "load"]
