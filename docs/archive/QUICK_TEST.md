# Quick Testing Guide

## 5-Minute Verification

### 1. Verify Connection Pool Configuration
```bash
python diagnose_connections.py | head -20
```

**✅ Expected Output**:
```
CONNECTION POOL CONFIGURATION
Pool Type: QueuePool
Pool Size (min connections kept open): 20
Max Overflow (additional connections): 10
```

### 2. Verify No Syntax Errors
```bash
python -c "from services.data_ingestion.app.db import engine; print('✓ Imports OK')"
python -c "from services.data_ingestion.app.repository import batch_fetch_fhir_resources; print('✓ Batch fetch function OK')"
```

### 3. Start Backend
```bash
python -m services.data_ingestion.main
```

Wait for:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

---

## 15-Minute Full Test

### 1. Check Connection Health Before Ingestion
```bash
# In another terminal (keep backend running)
python diagnose_connections.py
```

**✅ Expected**: Utilization <10%, all connections idle

### 2. Monitor During Ingestion
```bash
# Terminal 1: Run diagnostic in a loop
watch -n 2 python diagnose_connections.py

# Terminal 2: Start ingestion via UI at http://localhost:8000/ui
# Upload dataset → Start conversion

# Terminal 3: Tail logs to see batch fetching
# Should see: "batch-fetching 16348 FHIR resources"
# Should see: "fetched 16348 FHIR resources" (not 16348 individual queries)
```

**✅ Expected During Ingestion**:
- Utilization stays <30%
- No "too many clients" errors
- Batch fetch log messages
- Extraction completes in <10 seconds

### 3. Verify Patient Data Now Populated
```bash
# After ingestion completes, query database
# In pgAdmin:
SELECT COUNT(*), COUNT(DISTINCT patient_id) 
FROM patient 
WHERE name IS NOT NULL;
```

**✅ Expected**: 
- `count` = number of patients (e.g., 100+)
- `count_distinct` = same value (all have unique IDs)
- **NOT** all NULLs like before

---

## Automated Benchmark Test

### Prerequisites
- Backend running: `python -m services.data_ingestion.main`
- Test dataset: `synthea_sample.zip` (in project root)

### Run Benchmark
```bash
python benchmark_ingestion.py ./synthea_sample.zip
```

### Compare Before/After

**Before Fixes**:
```
Total Time: 191.914 seconds
Throughput: 85.2 resources/second
database_insertion_seconds: 191.9s (100%)
```

**After Fixes**:
```
Total Time: 60-80 seconds
Throughput: 200-270 resources/second
database_insertion_seconds: 15-25s (20-30%)
```

---

## Concurrent Load Test

### Test Multiple Ingestions
```bash
# Terminal 1: Start backend
python -m services.data_ingestion.main

# Terminals 2, 3, 4: In separate windows
python benchmark_ingestion.py ./synthea_sample.zip
python benchmark_ingestion.py ./synthea_sample.zip
python benchmark_ingestion.py ./synthea_sample.zip

# Monitor with:
python diagnose_connections.py
```

**✅ Expected**:
- All 3 jobs complete successfully
- No "too many clients" errors
- Connection utilization <40%

**❌ If You See "too many clients" Error** → Fix didn't work
- Check that `db.py` has `pool_size=20, max_overflow=10`
- Restart Python to flush connections
- Check PostgreSQL with: `SELECT COUNT(*) FROM pg_stat_activity;`

---

## Troubleshooting

### Issue: Still Seeing Slow Queries

**Check 1**: Verify batch fetch is being used
```bash
# In ingestion logs, search for:
grep "batch-fetching" <backend_output>
grep "fetched.*FHIR resources" <backend_output>
```

**Check 2**: Verify pool configuration
```python
python -c "
from services.data_ingestion.app.db import engine
pool = engine.pool
print(f'Pool size: {pool.pool_size}')
print(f'Max overflow: {pool.max_overflow}')
"
```

### Issue: "too many clients" Still Occurs

**Step 1**: Kill idle connections
```sql
-- In PostgreSQL
SELECT pg_terminate_backend(pid) 
FROM pg_stat_activity 
WHERE datname = 'hedis_db' 
  AND state = 'idle' 
  AND pid != pg_backend_pid();
```

**Step 2**: Check pool configuration again
```bash
python diagnose_connections.py | grep "Pool"
```

**Step 3**: Check for connection leaks
```bash
python diagnose_connections.py | grep "idle in transaction"
# Should be 0
```

### Issue: Patient Data Still NULL

**Check 1**: Verify fix was applied
```bash
grep -n "batch_id=fhir_resource.batch_id" services/data_ingestion/app/repository.py
# Should find many matches
```

**Check 2**: Check new patient records
```sql
SELECT patient_id, name, birth_date 
FROM patient 
WHERE created_at > NOW() - INTERVAL '1 hour'
LIMIT 5;
```

If `name` is still NULL:
1. Check extraction function is called: `grep "extract_patient_from_fhir" services/data_ingestion/app/pipeline.py`
2. Check FHIR resource has name data: `SELECT resource_json->>'name' FROM fhir_resources LIMIT 1;`

---

## Rollback

If anything breaks:

```bash
# Restore all files to original
git checkout services/data_ingestion/app/db.py
git checkout services/data_ingestion/app/pipeline.py
git checkout services/data_ingestion/app/repository.py

# Kill running backend
# Restart:
python -m services.data_ingestion.main
```

---

## Success Criteria Checklist

- [ ] `pool_size=20` shown in diagnostic
- [ ] No "too many clients" errors during ingestion
- [ ] Ingestion time <100 seconds for 16K resources
- [ ] Patient table populated with names and birth dates
- [ ] 3+ concurrent ingestions complete without errors
- [ ] Benchmark shows 200+ resources/second
- [ ] No SQL errors in logs

✅ All boxes checked = **Fix successful!**

---

## Next Steps

1. Run 5-minute verification above
2. Run 15-minute full test
3. Run benchmark on your test dataset
4. Deploy to production
5. Monitor with: `python diagnose_connections.py` (daily/weekly)

Questions? Check:
- `FIXES_IMPLEMENTED.md` - Detailed explanation
- `CONNECTION_ANALYSIS.md` - Root cause analysis
- `diagnose_connections.py --help` - Diagnostic tool

