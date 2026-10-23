import sys
sys.path.insert(0, 'services/data_ingestion_pallavi')

from app.db import engine
from sqlalchemy import inspect

inspector = inspect(engine)

# Check patient table columns
if 'patient' in inspector.get_table_names():
    cols = inspector.get_columns('patient')
    print('Patient table columns:')
    for col in cols:
        name = col['name']
        col_type = col['type']
        print(f'  {name}: {col_type}')
else:
    print('Patient table does not exist!')

# Check all tables
print()
print('All tables:')
for table in sorted(inspector.get_table_names()):
    print(f'  {table}')
