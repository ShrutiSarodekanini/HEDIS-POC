#!/usr/bin/env python3
"""
Clear ingested data to allow re-mapping with updated patient fields.
Uses raw SQL to bypass foreign key constraints.
"""

from services.data_ingestion_pallavi.app.db import SessionLocal
from sqlalchemy import text

session = SessionLocal()

try:
    print("🗑️  Clearing database...")
    
    # Disable foreign key constraints temporarily
    session.execute(text("ALTER TABLE public.basic_resource DISABLE TRIGGER ALL;"))
    
    # Delete in dependency order
    tables = [
        "validation_issues",
        "basic_resource",
        "coverage",
        "immunization",
        "medication",
        "procedure",
        "observation",
        "condition",
        "encounter",
        "patient",
        "fhir_resources",
        "conversion_jobs",
        "source_batches",
    ]
    
    total_deleted = 0
    for table in tables:
        try:
            result = session.execute(text(f"DELETE FROM public.{table};"))
            count = result.rowcount
            if count > 0:
                print(f"  ✓ Deleted {count:5} from {table}")
                total_deleted += count
        except Exception as e:
            print(f"  ⚠ Could not delete from {table}: {e}")
    
    # Re-enable constraints
    session.execute(text("ALTER TABLE public.basic_resource ENABLE TRIGGER ALL;"))
    
    session.commit()
    print(f"\n✅ Total records deleted: {total_deleted}")
    print("\n📝 Ready to re-ingest with updated mapping:")
    print("   ✓ Marital status (MARITAL field)")
    print("   ✓ Address (ADDRESS/CITY/STATE/ZIP fields)")
    
except Exception as e:
    session.rollback()
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    session.close()
