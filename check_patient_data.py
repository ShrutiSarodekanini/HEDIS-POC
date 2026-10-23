#!/usr/bin/env python3
"""Check what patient data is in the database."""

from services.data_ingestion.app.db import SessionLocal
from services.data_ingestion.app.models import Patient

session = SessionLocal()
patients = session.query(Patient).all()

print(f'Total patients in database: {len(patients)}')
print()
print('Patient data:')
print('=' * 120)
print(f'{"ID":8} | {"Name":30} | {"Gender":10} | {"Birth Date":12} | {"Marital":20} | Address')
print('-' * 120)

for p in patients[:15]:
    name = p.name or 'NULL'
    gender = p.gender or 'NULL'
    birth = str(p.birth_date) if p.birth_date else 'NULL'
    marital = p.marital_status or 'NULL'
    addr = (p.address[:40] + '...') if p.address else 'NULL'
    pid = p.patient_id[:8] if p.patient_id else 'NULL'
    print(f'{pid:8} | {name:30} | {gender:10} | {birth:12} | {marital:20} | {addr}')

print()
print('=' * 120)

# Check the summary
print()
print('Summary Statistics:')
print('-' * 120)

total = len(patients)
with_name = len([p for p in patients if p.name])
with_gender = len([p for p in patients if p.gender and p.gender != 'unknown'])
with_marital = len([p for p in patients if p.marital_status])
with_address = len([p for p in patients if p.address])

print(f'Total patients: {total}')
print(f'  With name: {with_name} ({100*with_name/total if total else 0:.0f}%)')
print(f'  With gender (not unknown): {with_gender} ({100*with_gender/total if total else 0:.0f}%)')
print(f'  With marital status: {with_marital} ({100*with_marital/total if total else 0:.0f}%)')
print(f'  With address: {with_address} ({100*with_address/total if total else 0:.0f}%)')

session.close()
