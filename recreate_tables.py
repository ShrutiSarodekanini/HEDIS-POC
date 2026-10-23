import sys
sys.path.insert(0, 'services/data_ingestion_pallavi')
from app.db import engine
from sqlalchemy import text

# Drop all tables using direct SQL
with engine.connect() as conn:
    # Disable foreign key constraints temporarily
    conn.execute(text("DROP TABLE IF EXISTS patient CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS encounter CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS condition CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS observation CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS procedure CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS medication CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS immunization CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS coverage CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS validation_issues CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS fhir_resources CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS conversion_jobs CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS source_batch CASCADE"))
    conn.commit()
    print('All tables dropped')

# Now recreate
from app.models import Base
Base.metadata.create_all(bind=engine)
print('All tables recreated')

# Verify
from sqlalchemy import inspect as sa_inspect
inspector = sa_inspect(engine)
print()
print('Tables created:')
for table in sorted(inspector.get_table_names()):
    cols = inspector.get_columns(table)
    print(f'  {table} ({len(cols)} cols)')
