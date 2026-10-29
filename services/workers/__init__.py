"""
RabbitMQ Workers - Async pipeline processing

Each worker consumes messages from a queue, processes them,
and publishes results to the next queue in the pipeline.

Workers:
- conversion_worker: CSV/JSON → FHIR
- load_worker: FHIR → Database
- quality_worker: Data quality checks
- measures_worker: HEDIS calculations
- gaps_worker: Gap prioritization
- documents_worker: Evidence attachment

Usage:
    python -m services.workers.conversion_worker
    python -m services.workers.load_worker
    # ... etc
"""
