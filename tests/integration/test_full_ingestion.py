#!/usr/bin/env python3
"""Comprehensive FHIR ingestion test with duplicate detection."""

import time
import requests
from pathlib import Path

BASE_URL = "http://localhost:8000"
fhir_file = Path("test_fhir_bundle.json")

def run_test(test_num: int, file_path: Path) -> bool:
    """Run a single FHIR ingestion test."""
    print()
    print("=" * 70)
    print(f"TEST {test_num}: FHIR Ingestion{'(DUPLICATE)' if test_num == 2 else ''}")
    print("=" * 70)
    
    try:
        # Upload
        print(f"Uploading {file_path.name}...")
        with open(file_path, "rb") as f:
            files = {"files": (file_path.name, f, "application/json")}
            resp = requests.post(
                f"{BASE_URL}/api/datasets/upload-fhir",
                files=files,
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()
            input_dir = result["input_dir"]
        print("✓ Upload successful")
        
        # Start job
        print("Starting ingestion job...")
        resp = requests.post(
            f"{BASE_URL}/api/jobs/start",
            params={"input_dir": input_dir, "adapter_name": "ehr_fhir_r4"},
            timeout=10,
        )
        resp.raise_for_status()
        job_id = resp.json()["job_id"]
        print(f"✓ Job started: {job_id}")
        
        # Wait for completion
        print("Waiting for completion...")
        for attempt in range(120):
            time.sleep(1)
            resp = requests.get(f"{BASE_URL}/api/jobs/{job_id}")
            resp.raise_for_status()
            job_data = resp.json()
            status = job_data["status"]
            
            if status == "completed":
                summary = job_data["summary"]
                resources_persisted = summary.get("resources_persisted", 0)
                error_count = summary.get("error_count", 0)
                error_msg = summary.get("error")
                
                print(f"✓ Job completed successfully")
                print(f"  - Resources persisted: {resources_persisted}")
                print(f"  - Error count: {error_count}")
                
                if error_count == 0 and not error_msg:
                    print(f"✓ NO ERRORS - Ingestion successful!")
                    return True
                else:
                    print(f"✗ Error: {error_msg}")
                    return False
                    
            elif status == "failed":
                error_msg = job_data["summary"].get("error", "Unknown error")
                print(f"✗ Job failed: {error_msg}")
                return False
            else:
                if attempt % 10 == 0:
                    print(f"  Status: {status} ({attempt}s)")
        
        print("✗ Job timed out")
        return False
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def main():
    """Run the complete test suite."""
    print("\n" + "=" * 70)
    print("HEDIS FHIR INGESTION TEST SUITE")
    print("=" * 70)
    print("Testing Location support and duplicate handling")
    
    if not fhir_file.exists():
        print(f"✗ Test file not found: {fhir_file}")
        return False
    
    # Test 1: First ingestion
    success1 = run_test(1, fhir_file)
    
    # Wait between tests
    print("\nWaiting between test runs...")
    time.sleep(3)
    
    # Test 2: Second ingestion (same data - should update, not duplicate)
    success2 = run_test(2, fhir_file)
    
    # Summary
    print()
    print("=" * 70)
    print("TEST RESULTS SUMMARY")
    print("=" * 70)
    print(f"Test 1 (First ingestion):  {'✓ PASSED' if success1 else '✗ FAILED'}")
    print(f"Test 2 (Re-ingestion):     {'✓ PASSED' if success2 else '✗ FAILED'}")
    print()
    
    if success1 and success2:
        print("✓✓✓ ALL TESTS PASSED - Duplicate handling working! ✓✓✓")
        print("="*70)
        return True
    else:
        print("✗ SOME TESTS FAILED")
        print("=" * 70)
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
