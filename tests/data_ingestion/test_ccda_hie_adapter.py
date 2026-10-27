from services.data_ingestion.app.adapters.ccda_hie_adapter import CCDAdapter


def test_stream_skips_repeated_entry_identifier(tmp_path):
    document = """<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <id root="document-1" />
  <recordTarget>
    <patientRole><id extension="patient-1" /></patientRole>
  </recordTarget>
  <component><structuredBody><component><section>
    <code code="8716-3" />
    <entry><observation><id extension="observation-1" /><code code="8302-2" /></observation></entry>
    <entry><observation><id extension="observation-1" /><code code="8302-2" /></observation></entry>
  </section></component></structuredBody></component>
</ClinicalDocument>
"""
    source = tmp_path / "D-TEST.xml"
    source.write_text(document, encoding="utf-8")
    issues = []

    _, records = CCDAdapter().stream(str(source), issues.append)
    resources = list(records)

    assert [record.data["resourceType"] for record in resources] == ["Patient", "Observation"]
    assert len(issues) == 1
    assert issues[0].code == "duplicate_ccda_entry_id"
    assert issues[0].severity == "warning"