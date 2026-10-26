#!/usr/bin/env python
"""Check FHIR resources to see if patient data is being generated."""
import json
from services.data_ingestion_pallavi.app.db import session_scope
from services.data_ingestion_pallavi.app.models import FhirResource

with session_scope() as session:
    # Get first 3 Patient resources
    patients = session.query(FhirResource).filter(
        FhirResource.resource_type == "Patient"
    ).limit(3).all()
    
    print(f"\n📋 Found {len(patients)} Patient FHIR resources\n")
    
    for i, patient in enumerate(patients, 1):
        print(f"{'='*80}")
        print(f"Resource #{i}")
        print(f"{'='*80}")
        print(f"ID: {patient.resource_id}")
        print(f"Source File: {patient.source_file}")
        print(f"Validation Status: {patient.validation_status}")
        print(f"\n📄 Full FHIR JSON:")
        print(json.dumps(patient.resource_json, indent=2))
        print()
