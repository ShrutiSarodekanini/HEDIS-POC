#!/usr/bin/env python3
"""Verify database schema."""

from services.data_ingestion.app.db import Base, engine
from sqlalchemy import inspect

# Create all tables
Base.metadata.create_all(engine)

# Verify Location table exists
inspector = inspect(engine)
tables = inspector.get_table_names()

print("Checking database schema...")
print()

location_found = False
for table in sorted(tables):
    if "location" in table.lower():
        location_found = True
        print(f"✓ Table: {table}")
        cols = inspector.get_columns(table)
        for col in cols:
            col_type = str(col["type"])
            print(f"    - {col['name']:<25s} {col_type}")

print()
if location_found:
    print("✓ Location table exists and ready")
else:
    print("✗ Location table not found")
    print()
    print("All tables:")
    for table in sorted(tables):
        print(f"  - {table}")
