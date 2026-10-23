import sys
sys.path.insert(0, 'services/data_ingestion_pallavi')
from app.db import SessionLocal
from app.models import Patient, Encounter, Condition, Observation, Procedure, Medication, Immunization, Coverage, FhirResource

session = SessionLocal()

print('📊 Denormalized Table Population Summary')
print('=' * 70)
print()

# FHIR Resources
fhir_count = session.query(FhirResource).count()
print(f'fhir_resources: {fhir_count} records')

# Denormalized tables
patient_count = session.query(Patient).count()
encounter_count = session.query(Encounter).count()
condition_count = session.query(Condition).count()
observation_count = session.query(Observation).count()
procedure_count = session.query(Procedure).count()
medication_count = session.query(Medication).count()
immunization_count = session.query(Immunization).count()
coverage_count = session.query(Coverage).count()

print()
print('Denormalized Tables:')
print(f'  patient: {patient_count} records')
print(f'  encounter: {encounter_count} records')
print(f'  condition: {condition_count} records')
print(f'  observation: {observation_count} records')
print(f'  procedure: {procedure_count} records')
print(f'  medication: {medication_count} records')
print(f'  immunization: {immunization_count} records')
print(f'  coverage: {coverage_count} records')

total_denormalized = patient_count + encounter_count + condition_count + observation_count + procedure_count + medication_count + immunization_count + coverage_count
print()
print(f'Total denormalized records: {total_denormalized}')
print(f'Total FHIR resources: {fhir_count}')

# Show sample records
print()
print('=' * 70)
print('Sample Data:')
print('=' * 70)
print()

if patient_count > 0:
    print(f'✅ Patient sample (first 2):')
    patients = session.query(Patient).limit(2).all()
    for p in patients:
        print(f'   - ID: {p.patient_id}, Name: {p.name}, DOB: {p.birth_date}')
    print()

if encounter_count > 0:
    print(f'✅ Encounter sample (first 2):')
    encounters = session.query(Encounter).limit(2).all()
    for e in encounters:
        print(f'   - ID: {e.encounter_id}, Type: {e.encounter_type}, Status: {e.status}')
    print()

if condition_count > 0:
    print(f'✅ Condition sample (first 2):')
    conditions = session.query(Condition).limit(2).all()
    for c in conditions:
        print(f'   - Code: {c.code}, Display: {c.display}, Onset: {c.onset_date}')
    print()

if observation_count > 0:
    print(f'✅ Observation sample (first 2):')
    observations = session.query(Observation).limit(2).all()
    for o in observations:
        print(f'   - Code: {o.code}, Value: {o.value} {o.unit}, Date: {o.effective_date}')
    print()

if coverage_count > 0:
    print(f'✅ Coverage sample (first 2):')
    coverages = session.query(Coverage).limit(2).all()
    for c in coverages:
        print(f'   - ID: {c.coverage_id}, Period: {c.period_start} to {c.period_end}')
        print(f'     Gap Compliant: {c.continuous_enrollment_compliant}, Gap Days: {c.gap_days}')
    print()

session.close()
