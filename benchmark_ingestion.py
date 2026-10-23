#!/usr/bin/env python3
"""
Ingestion Performance Benchmarking Script

Measures end-to-end ingestion performance including:
- Total time
- Resources/second throughput
- Database operation breakdown
- Connection pool usage during ingestion
"""

import sys
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(__file__).rsplit("\\", 2)[0])


class BenchmarkRunner:
    """Run ingestion benchmark and collect metrics"""
    
    def __init__(self, dataset_path: str, output_dir: str = "./benchmark_results"):
        self.dataset_path = dataset_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.timestamp = datetime.now().isoformat()
        self.report_file = self.output_dir / f"benchmark_{self.timestamp.replace(':', '-')}.json"
        
    def run_diagnostic_before(self):
        """Run diagnostic before benchmark"""
        print("\n" + "="*80)
        print("PRE-BENCHMARK DIAGNOSTIC")
        print("="*80)
        
        result = subprocess.run([sys.executable, "diagnose_connections.py"], 
                              capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        
        return result.returncode == 0
    
    def run_diagnostic_after(self):
        """Run diagnostic after benchmark"""
        print("\n" + "="*80)
        print("POST-BENCHMARK DIAGNOSTIC")
        print("="*80)
        
        result = subprocess.run([sys.executable, "diagnose_connections.py"], 
                              capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
    
    def calculate_metrics(self, benchmark_data):
        """Calculate throughput and efficiency metrics"""
        if not benchmark_data.get("timings"):
            return {}
        
        timings = benchmark_data["timings"]
        total_time = sum(timings.values())
        total_resources = benchmark_data.get("resources_persisted", 0)
        
        metrics = {
            "total_resources": total_resources,
            "total_time_seconds": total_time,
            "throughput_resources_per_second": (
                total_resources / total_time if total_time > 0 else 0
            ),
            "time_breakdown": timings,
            "time_percentage": {
                key: round(100 * value / total_time, 1) if total_time > 0 else 0
                for key, value in timings.items()
            },
        }
        
        return metrics
    
    def wait_for_ingestion(self, job_id: str, timeout: int = 3600):
        """Wait for ingestion job to complete"""
        from services.data_ingestion.app.db import SessionLocal
        from services.data_ingestion.app.models import ConversionJob
        
        start_time = time.perf_counter()
        session = SessionLocal()
        
        while True:
            job = session.query(ConversionJob).filter(ConversionJob.id == job_id).first()
            
            if not job:
                print(f"Job {job_id} not found!")
                session.close()
                return None
            
            if job.status in {"completed", "failed"}:
                elapsed = time.perf_counter() - start_time
                result = {
                    "job_id": job_id,
                    "status": job.status,
                    "elapsed_seconds": elapsed,
                    "total_source_records": job.total_source_records,
                    "resources_generated": job.resources_generated,
                    "resources_validated": job.resources_validated,
                    "resources_persisted": job.resources_persisted,
                    "error_count": job.error_count,
                    "warning_count": job.warning_count,
                    "timings": job.summary.get("timings", {}),
                }
                session.close()
                return result
            
            # Check timeout
            elapsed = time.perf_counter() - start_time
            if elapsed > timeout:
                print(f"Timeout after {elapsed:.1f}s")
                session.close()
                return None
            
            # Print status every 10 seconds
            if int(elapsed) % 10 == 0:
                print(f"[{elapsed:.0f}s] Job {job.status}: "
                      f"{job.resources_generated}/{job.resources_validated} resources")
            
            time.sleep(2)
    
    def trigger_ingestion(self, dataset_path: str) -> str | None:
        """Trigger ingestion via REST API and return job_id"""
        import requests
        
        print(f"\nUploading dataset from: {dataset_path}")
        
        # Upload dataset
        with open(dataset_path, "rb") as f:
            files = {"file": f}
            response = requests.post("http://localhost:8000/api/datasets/upload", files=files)
        
        if response.status_code != 200:
            print(f"Upload failed: {response.status_code}")
            print(response.text)
            return None
        
        result = response.json()
        upload_dir = result.get("upload_dir")
        print(f"Upload successful: {upload_dir}")
        
        # Start conversion
        response = requests.post(
            "http://localhost:8000/api/jobs/start",
            params={
                "input_dir": upload_dir,
                "adapter_name": "synthea_csv"
            }
        )
        
        if response.status_code != 200:
            print(f"Job start failed: {response.status_code}")
            print(response.text)
            return None
        
        result = response.json()
        job_id = result.get("id")
        print(f"Job started: {job_id}")
        
        return job_id
    
    def run_benchmark(self, dataset_path: str | None = None):
        """Run full benchmark"""
        if dataset_path is None:
            dataset_path = self.dataset_path
        
        print("\n\n")
        print("╔" + "="*78 + "╗")
        print("║" + " "*78 + "║")
        print("║" + " INGESTION PERFORMANCE BENCHMARK ".center(78) + "║")
        print("║" + " "*78 + "║")
        print("╚" + "="*78 + "╝")
        print(f"\nTimestamp: {self.timestamp}")
        print(f"Dataset: {dataset_path}")
        
        # Pre-benchmark diagnostics
        self.run_diagnostic_before()
        
        # Trigger ingestion
        print("\n" + "="*80)
        print("STARTING INGESTION")
        print("="*80)
        
        job_id = self.trigger_ingestion(dataset_path)
        if not job_id:
            print("Failed to start ingestion!")
            return
        
        # Wait for completion
        print("\nWaiting for ingestion to complete...")
        benchmark_data = self.wait_for_ingestion(job_id)
        
        if not benchmark_data:
            print("Ingestion did not complete!")
            return
        
        # Post-benchmark diagnostics
        self.run_diagnostic_after()
        
        # Calculate metrics
        metrics = self.calculate_metrics(benchmark_data)
        
        # Save report
        report = {
            "timestamp": self.timestamp,
            "dataset": dataset_path,
            "benchmark_data": benchmark_data,
            "metrics": metrics,
        }
        
        with open(self.report_file, "w") as f:
            json.dump(report, f, indent=2)
        
        print("\n" + "="*80)
        print("BENCHMARK RESULTS")
        print("="*80)
        print(f"\nTotal Time: {benchmark_data['elapsed_seconds']:.1f}s")
        print(f"Resources Processed: {benchmark_data['resources_persisted']:,}")
        print(f"Throughput: {metrics['throughput_resources_per_second']:.1f} resources/second")
        print(f"\nTime Breakdown:")
        for key, value in metrics['time_percentage'].items():
            print(f"  {key:30s}: {value:6.1f}% ({metrics['time_breakdown'][key]:.1f}s)")
        
        print(f"\nErrors: {benchmark_data['error_count']}")
        print(f"Warnings: {benchmark_data['warning_count']}")
        
        print(f"\nReport saved to: {self.report_file}")
        
        return report


def main():
    # Check for dataset parameter
    if len(sys.argv) < 2:
        print("Usage: python benchmark_ingestion.py <path_to_dataset.zip>")
        print("\nExample:")
        print("  python benchmark_ingestion.py ./synthea_sample.zip")
        sys.exit(1)
    
    dataset_path = sys.argv[1]
    
    # Check if backend is running
    import requests
    try:
        response = requests.get("http://localhost:8000/health", timeout=2)
        if response.status_code != 200:
            print("ERROR: FastAPI backend not responding")
            print("Start backend with: python -m services.data_ingestion.main")
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("ERROR: Cannot connect to FastAPI backend at http://localhost:8000")
        print("Start backend with: python -m services.data_ingestion.main")
        sys.exit(1)
    
    # Run benchmark
    benchmark = BenchmarkRunner(dataset_path)
    benchmark.run_benchmark()


if __name__ == "__main__":
    main()
