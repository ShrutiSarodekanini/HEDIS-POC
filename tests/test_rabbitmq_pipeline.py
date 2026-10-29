from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pika
import pytest
from fastapi.testclient import TestClient


def test_start_job_enqueues_existing_job_and_batch_in_transaction(monkeypatch, tmp_path):
    from services import reliable_messaging
    from services.data_ingestion.app import api

    batch_id = "12345678-1234-5678-1234-567812345678"
    job_id = "job-1"
    enqueued = []

    class Adapter:
        def discover(self, _input_dir):
            return SimpleNamespace(missing_required_files=[])

    class Session:
        pass

    @contextmanager
    def session_scope():
        yield Session()

    monkeypatch.setattr(api.registry, "has_adapter", lambda _name: True)
    monkeypatch.setattr(api.registry, "get_adapter", lambda _name: Adapter())
    monkeypatch.setattr(
        api,
        "create_job_record",
        lambda _session, _path, _adapter: SimpleNamespace(id=job_id, batch_id=batch_id),
    )
    monkeypatch.setattr(api.job_manager, "ensure_state", lambda _job_id, _batch_id: None)
    monkeypatch.setattr(api, "session_scope", session_scope)
    monkeypatch.setattr(
        reliable_messaging,
        "enqueue_outbox_session",
        lambda session, queue_name, message: enqueued.append((session, queue_name, message)),
    )

    result = api.start_conversion_job(str(tmp_path), "claims_837", async_processing=True)

    assert result == {"job_id": job_id, "batch_id": batch_id, "status": "queued", "adapter": "claims_837"}
    assert isinstance(enqueued[0][0], Session)
    assert enqueued[0][1] == "ingestion.convert"
    assert enqueued[0][2].job_id == job_id
    assert enqueued[0][2].batch_id == batch_id
    assert enqueued[0][2].adapter_name == "claims_837"


def test_async_upload_does_not_store_or_return_client_filename(monkeypatch, tmp_path):
    from services import reliable_messaging
    from services.data_ingestion import main
    from services.data_ingestion.app import api

    class Session:
        def add(self, _value):
            return None

    @contextmanager
    def session_scope():
        yield Session()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(api, "session_scope", session_scope)
    monkeypatch.setattr(reliable_messaging, "enqueue_outbox_session", lambda *_args: None)

    response = TestClient(main.create_app()).post(
        "/api/upload-async",
        files={"file": ("Patient Name.csv", b"synthetic,data\n", "text/csv")},
    )

    assert response.status_code == 200
    assert "Patient Name.csv" not in response.text
    assert list((tmp_path / "data" / "uploads").glob("*/source.csv"))


def test_conversion_worker_resolves_registered_adapter_names():
    from services.workers.conversion_worker import ConversionWorker

    worker = ConversionWorker()

    assert worker._get_adapter_name("synthea", "csv") == "synthea_csv"
    assert worker._get_adapter_name("api_upload", "fhir") == "ehr_fhir_r4"
    assert worker._get_adapter_name("ccda_hie", "ccda") == "ccda_hie"
    assert worker._get_adapter_name("claims_837", "csv") == "claims_837"
    assert worker._get_adapter_name("ncpdp_pharmacy", "csv") == "ncpdp_pharmacy"


def test_job_status_prefers_durable_worker_state(monkeypatch):
    from services.data_ingestion.app import api

    job = SimpleNamespace(
        status="partially_completed",
        summary={},
        total_source_records=10,
        resources_generated=8,
        resources_validated=7,
        resources_persisted=7,
        started_at=None,
        completed_at=None,
    )

    class Session:
        def get(self, _model, _job_id):
            return job

    @contextmanager
    def session_scope():
        yield Session()

    monkeypatch.setattr(api, "session_scope", session_scope)
    monkeypatch.setattr(api, "list_resources_for_job", lambda _session, _job_id: [])
    monkeypatch.setattr(api.job_manager, "get_state", lambda _job_id: SimpleNamespace(status="pending"))

    payload = api._job_status_payload("job-1")

    assert payload["status"] == "partially_completed"
    assert [stage["status"] for stage in payload["stages"]] == ["completed"] * 5


def test_load_worker_persists_by_job_id(monkeypatch):
    from services.workers import load_worker

    calls = []

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [object()]

    class Session:
        def execute(self, _statement):
            return Result()

    @contextmanager
    def session_scope():
        yield Session()

    monkeypatch.setattr(load_worker, "session_scope", session_scope)
    monkeypatch.setattr(
        load_worker,
        "mark_validated_resources_persisted",
        lambda _session, job_id: calls.append(job_id) or 1,
    )

    assert load_worker.LoadWorker()._load_fhir_resources("job-1") == 1
    assert calls == ["job-1"]


def test_load_worker_propagates_team_schema_failure(monkeypatch):
    from services.workers import load_worker

    @contextmanager
    def connect():
        yield object()

    monkeypatch.setattr(load_worker.db, "connect", connect)
    monkeypatch.setattr(
        load_worker.team_schema_loader,
        "load_job",
        lambda _conn, _job_id, _batch_id: (_ for _ in ()).throw(LookupError("missing Patient resource")),
    )

    with pytest.raises(LookupError):
        load_worker.LoadWorker()._extract_team_schema(
            "12345678-1234-5678-1234-567812345678",
            "job-1",
            SimpleNamespace(),
        )


def test_publisher_accepts_pika_none_return_on_confirmed_delivery():
    from services.rabbitmq_messages import ConversionMessage, Publisher

    published = []
    publisher = Publisher()
    publisher.channel = SimpleNamespace(basic_publish=lambda **kwargs: published.append(kwargs))

    publisher.publish(
        "ingestion.convert",
        ConversionMessage(
            batch_id="batch-1",
            source_system="synthea",
            file_path="synthetic-input",
            file_type="csv",
        ),
    )

    assert published[0]["routing_key"] == "ingestion.convert"


def test_worker_handoff_uses_same_transaction_for_outbox_and_inbox(monkeypatch):
    from services import rabbitmq_messages

    calls = []
    connection = object()
    worker = rabbitmq_messages.Worker("quality.check")
    worker.current_message_id = "incoming-1"
    monkeypatch.setattr(
        rabbitmq_messages,
        "enqueue_outbox",
        lambda conn, queue, message, source_message_id: calls.append(
            ("outbox", conn, queue, message, source_message_id)
        )
        or "outgoing-1",
    )
    monkeypatch.setattr(
        rabbitmq_messages,
        "complete_inbox",
        lambda conn, consumer, message_id: calls.append(("inbox", conn, consumer, message_id)),
    )

    outgoing_id = worker.enqueue_next(connection, "measures.run", {"batch_id": "batch-1"})

    assert outgoing_id == "outgoing-1"
    assert calls[0][1] is connection
    assert calls[1][1] is connection
    assert calls[0][-1] == "incoming-1"


def test_worker_acknowledges_processed_duplicate_without_running(monkeypatch):
    from services import rabbitmq_messages

    worker = rabbitmq_messages.Worker("quality.check")
    channel = SimpleNamespace(acknowledged=[])
    channel.basic_ack = lambda delivery_tag: channel.acknowledged.append(delivery_tag)
    monkeypatch.setattr(rabbitmq_messages, "claim_inbox", lambda _consumer, _message_id: "processed")

    worker._message_callback(
        channel,
        SimpleNamespace(delivery_tag=11),
        pika.BasicProperties(message_id="message-1"),
        b"{}",
    )

    assert channel.acknowledged == [11]


def test_worker_requeues_delivery_with_active_lease(monkeypatch):
    from services import rabbitmq_messages

    worker = rabbitmq_messages.Worker("quality.check")
    worker.connection = SimpleNamespace(sleep=lambda _seconds: None)
    channel = SimpleNamespace(rejected=[])
    channel.basic_nack = lambda delivery_tag, requeue: channel.rejected.append((delivery_tag, requeue))
    monkeypatch.setattr(rabbitmq_messages, "claim_inbox", lambda _consumer, _message_id: "busy")

    worker._message_callback(
        channel,
        SimpleNamespace(delivery_tag=12),
        pika.BasicProperties(message_id="message-2"),
        b"{}",
    )

    assert channel.rejected == [(12, True)]


def test_controlled_worker_failure_uses_bounded_retry(monkeypatch):
    from services import rabbitmq_messages

    class FailingWorker(rabbitmq_messages.Worker):
        def process_message(self, _message):
            return False

    worker = FailingWorker("quality.check")
    channel = SimpleNamespace(published=[], acknowledged=[])
    channel.basic_publish = lambda **kwargs: channel.published.append(kwargs)
    channel.basic_ack = lambda delivery_tag: channel.acknowledged.append(delivery_tag)
    monkeypatch.setattr(rabbitmq_messages, "claim_inbox", lambda _consumer, _message_id: "claimed")
    monkeypatch.setattr(rabbitmq_messages, "fail_inbox", lambda *_args: None)

    worker._message_callback(
        channel,
        SimpleNamespace(delivery_tag=13),
        pika.BasicProperties(message_id="message-3", headers={"retry_count": 0}),
        b"{}",
    )

    assert channel.published[0]["exchange"] == "pipeline"
    assert channel.published[0]["properties"].headers["retry_count"] == 1
    assert channel.acknowledged == [13]


def test_outbox_dispatcher_preserves_message_id(monkeypatch):
    from services import outbox_dispatcher

    updates = []
    published = []

    class Result:
        def fetchall(self):
            return [(7, "outgoing-1", "quality.check", {"batch_id": "batch-1"}, {"retry_count": 0}, 0)]

    class Connection:
        def execute(self, statement, parameters):
            if statement.lstrip().startswith("SELECT"):
                return Result()
            updates.append((statement, parameters))
            return SimpleNamespace()

    @contextmanager
    def connect():
        yield Connection()

    class Publisher:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def publish(self, queue_name, message, *, message_id, headers):
            published.append((queue_name, message, message_id, headers))

    monkeypatch.setattr(outbox_dispatcher.db, "connect", connect)
    monkeypatch.setattr(outbox_dispatcher, "Publisher", Publisher)

    assert outbox_dispatcher.dispatch_once() == 1
    assert published == [("quality.check", {"batch_id": "batch-1"}, "outgoing-1", {"retry_count": 0})]
    assert updates[0][1] == (7,)


def test_worker_retries_with_incremented_attempt_header():
    from services.rabbitmq_messages import Worker

    worker = Worker("quality.check")
    channel = SimpleNamespace(published=[], acknowledged=[])
    channel.basic_publish = lambda **kwargs: channel.published.append(kwargs)
    channel.basic_ack = lambda delivery_tag: channel.acknowledged.append(delivery_tag)
    properties = pika.BasicProperties(
        content_type="application/json",
        message_id="message-1",
        correlation_id="batch-1",
        headers={"retry_count": 1},
    )

    worker._retry_or_dead_letter(channel, 7, properties, b"{}")

    assert channel.published[0]["exchange"] == "pipeline"
    assert channel.published[0]["properties"].headers["retry_count"] == 2
    assert channel.acknowledged == [7]


def test_worker_dead_letters_after_retry_limit():
    from services.rabbitmq_messages import MAX_RETRIES, Worker

    worker = Worker("quality.check")
    channel = SimpleNamespace(published=[], acknowledged=[])
    channel.basic_publish = lambda **kwargs: channel.published.append(kwargs)
    channel.basic_ack = lambda delivery_tag: channel.acknowledged.append(delivery_tag)
    properties = pika.BasicProperties(headers={"retry_count": MAX_RETRIES})

    worker._retry_or_dead_letter(channel, 8, properties, b"{}")

    assert channel.published[0]["exchange"] == "pipeline.dead"
    assert channel.published[0]["routing_key"] == "quality.check"
    assert channel.acknowledged == [8]


def test_all_architecture_component_queues_are_registered():
    from services.rabbitmq_config import QUEUES

    assert {
        "valuesets.resolve",
        "gaps.identify",
        "evidence.extract",
        "stars.calculate",
    } <= QUEUES.keys()


def test_evidence_worker_extracts_one_gap_and_queues_next(monkeypatch):
    from services.workers import evidence_worker

    connections = [SimpleNamespace(execute=lambda *_args: None), SimpleNamespace(execute=lambda *_args: None)]

    @contextmanager
    def connect():
        yield connections.pop(0)

    extracted = []
    handoffs = []
    worker = evidence_worker.EvidenceWorker()
    monkeypatch.setattr(evidence_worker.db, "connect", connect)
    monkeypatch.setattr(
        evidence_worker.evidence,
        "find_evidence",
        lambda conn, gap_id: extracted.append((conn, gap_id)) or {"saved": 2},
    )
    monkeypatch.setattr(
        worker,
        "enqueue_next",
        lambda conn, queue, message: handoffs.append((conn, queue, message)),
    )

    result = worker.process_message(
        {
            "batch_id": "batch-1",
            "job_id": "job-1",
            "gap_id": "gap-1",
            "remaining_gap_ids": ["gap-2"],
        }
    )

    assert result is True
    assert extracted[0][1] == "gap-1"
    assert handoffs[0][1] == "evidence.extract"
    assert handoffs[0][2].gap_id == "gap-2"
    assert handoffs[0][2].remaining_gap_ids == []


def test_measure_engine_uses_configured_node_heap(monkeypatch, tmp_path):
    from services.integration import measures

    captured = []
    settings = replace(
        measures.SETTINGS,
        engine_root=tmp_path,
        engine_node_heap_mb=4096,
    )
    cli = tmp_path / "build" / "cli.js"
    cli.parent.mkdir()
    cli.touch()
    bundle = measures.MeasureBundle("CBP", tmp_path / "measure.json", "CMS165", None)
    monkeypatch.setattr(
        measures,
        "_run_engine",
        lambda command, *_args: captured.append(command) or SimpleNamespace(returncode=0, stdout="[]", stderr=""),
    )

    measures._call_engine(bundle, "CMS165", ["patient.json"], settings)

    assert captured[0][:3] == ["node", "--max-old-space-size=4096", str(cli)]


def test_cbp_pruning_uses_declared_resource_types(tmp_path):
    import json

    from services.integration import measures

    definition_path = tmp_path / "measure.json"
    definition_path.write_text(
        json.dumps(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Library",
                            "dataRequirement": [
                                {"type": "Patient"},
                                {
                                    "type": "Observation",
                                    "codeFilter": [
                                        {
                                            "code": [
                                                {
                                                    "system": "http://loinc.org",
                                                    "code": "85354-9",
                                                }
                                            ]
                                        }
                                    ],
                                },
                                {"type": "Resource"},
                            ],
                        }
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    patient_path = tmp_path / "patient.json"
    patient_path.write_text(
        json.dumps(
            {
                "resourceType": "Bundle",
                "entry": [
                    {"resource": {"resourceType": "Patient", "id": "synthetic-patient"}},
                    {
                        "resource": {
                            "resourceType": "Observation",
                            "id": "synthetic-bp",
                            "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9"}]},
                        }
                    },
                    {
                        "resource": {
                            "resourceType": "Observation",
                            "id": "synthetic-unrelated",
                            "code": {"coding": [{"system": "http://loinc.org", "code": "12345-6"}]},
                        }
                    },
                    {"resource": {"resourceType": "Claim", "id": "synthetic-claim"}},
                    {"resource": {"resourceType": "ValueSet", "id": "synthetic-valueset"}},
                ],
            }
        ),
        encoding="utf-8",
    )

    measures._prune_patient_bundle(
        patient_path,
        measures.MeasureBundle("CBP", definition_path, "CMS165", None),
    )

    pruned = json.loads(patient_path.read_text(encoding="utf-8"))
    assert [entry["resource"]["resourceType"] for entry in pruned["entry"]] == ["Patient", "Observation"]