# Outbox dispatcher: publishes committed outbox rows to RabbitMQ; no OCR and no engine.
FROM hedis-base:latest

USER app
CMD ["python", "-m", "services.outbox_dispatcher"]
