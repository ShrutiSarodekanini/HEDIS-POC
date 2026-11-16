#!/usr/bin/env python3
"""Test Synthea FHIR dataset ingestion."""

import time
import requests

BASE_URL = "http://localhost:8000"

# Use the previous uploaded Synthea dataset
input_dir = r"C:\Users\Administrator\OneDrive - Kanini\Desktop\Poc\HEDIS-POC\.uploaded_datasets\2a63b637-d254-43da-810c-45ae0f6cd202"

print("=" * 70)
print("SYNTHEA DATASET INGESTION TEST")
print("=" * 70)
print("Testing full FHIR resource pipeline with Organization, Location,")
print("Practitioner, and PractitionerRole resources")
print()

# Start job with Synthea files
print("Starting Synthea ingestion...")
resp = requests.post(
    f"{BASE_URL}/api/jobs/start",
    params={"input_dir": input_dir, "adapter_name": "ehr_fhir_r4"},
    timeout=10,
)

if resp.status_code != 200:
    print(f"Error: {resp.text}")
    exit(1)

job_id = resp.json()["job_id"]
print(f"✓ Job started: {job_id}")
print()

# Wait for completion
last_status = None
last_gen = 0

for attempt in range(180):
    time.sleep(2)
    resp = requests.get(f"{BASE_URL}/api/jobs/{job_id}")
    job_data = resp.json()
    status = job_data["status"]
    gen = job_data["resources_generated"]
    per = job_data["resources_persisted"]
    
    if status != last_status or gen > last_gen:
        print(f"  [{attempt*2}s] Status: {status:15s} | Generated: {gen:3d} | Persisted: {per:3d}")
        last_status = status
        last_gen = gen
    
    if status in ["completed", "failed"]:
        summary = job_data["summary"]
        
        print()
        print("=" * 70)
        print("FINAL RESULT")
        print("=" * 70)
        print(f"Status: {status}")
        print(f"Resources generated: {summary.get('resources_generated', 0)}")
        print(f"Resources persisted: {summary.get('resources_persisted', 0)}")
        print(f"Error count: {summary.get('error_count', 0)}")
        
        if summary.get("error"):
            print(f"Error: {summary['error']}")
            print("✗ FAILED")
            exit(1)
        else:
            print("✓ NO ERRORS - Ingestion successful!")
            print("=" * 70)
            exit(0)

print("✗ Job timed out after 6 minutes")
exit(1)
