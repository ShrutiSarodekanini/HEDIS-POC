#!/usr/bin/env python3
"""
Cleanup script to terminate orphaned PostgreSQL connections.

Usage:
    python cleanup_connections.py
"""

import os
import sys
import time
from sqlalchemy import text, create_engine
from sqlalchemy.exc import OperationalError
from dotenv import load_dotenv

load_dotenv()

# Get database URL
database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/hedis_db")

# Extract database name
db_name = database_url.split("/")[-1]

print(f"🔍 Cleaning up PostgreSQL connections for database: {db_name}")
print(f"📊 Connecting to: {database_url.split('@')[1].split('/')[0]}")

try:
    # Create a direct connection using psycopg driver (not pooled)
    engine = create_engine(
        database_url,
        poolclass=None,  # No pooling for cleanup
        echo=False,
    )
    
    with engine.connect() as conn:
        # Step 1: Show current connection status
        print("\n📋 Current PostgreSQL Connection Status:")
        print("=" * 60)
        
        result = conn.execute(text("""
            SELECT 
                pid,
                usename,
                application_name,
                state,
                EXTRACT(EPOCH FROM (NOW() - state_change)) as idle_seconds,
                query
            FROM pg_stat_activity
            WHERE datname = :dbname
            ORDER BY state, pid;
        """), {"dbname": db_name})
        
        rows = result.fetchall()
        for row in rows:
            pid, usename, app, state, idle_sec, query = row
            idle_time = f"{idle_sec:.0f}s" if idle_sec else "N/A"
            query_text = (query[:50] + "...") if query and len(query) > 50 else query or "<none>"
            print(f"  PID {pid:5} | {state:20} | Idle: {idle_time:6} | {usename:10} | App: {app}")
            if query:
                print(f"           Query: {query_text}")
        
        print(f"\n  Total Connections: {len(rows)}")
        
        # Step 2: Terminate idle connections
        print("\n🔄 Step 1: Terminating IDLE connections...")
        result = conn.execute(text("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = :dbname
                AND state = 'idle'
                AND pid != pg_backend_pid();
        """), {"dbname": db_name})
        terminated_idle = result.rowcount
        print(f"   ✅ Terminated {terminated_idle} idle connections")
        
        # Commit the changes
        conn.commit()
        time.sleep(1)
        
        # Step 3: Terminate idle-in-transaction connections
        print("\n🔄 Step 2: Terminating IDLE-IN-TRANSACTION connections...")
        result = conn.execute(text("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = :dbname
                AND state = 'idle in transaction'
                AND pid != pg_backend_pid();
        """), {"dbname": db_name})
        terminated_idle_tx = result.rowcount
        print(f"   ✅ Terminated {terminated_idle_tx} idle-in-transaction connections")
        
        conn.commit()
        time.sleep(1)
        
        # Step 4: Show final status
        print("\n📋 Final PostgreSQL Connection Status:")
        print("=" * 60)
        
        result = conn.execute(text("""
            SELECT 
                state,
                COUNT(*) as count,
                MAX(EXTRACT(EPOCH FROM (NOW() - state_change))) as max_idle_seconds
            FROM pg_stat_activity
            WHERE datname = :dbname
            GROUP BY state
            ORDER BY state;
        """), {"dbname": db_name})
        
        total_after = 0
        for state, count, max_idle in result.fetchall():
            idle_text = f"({max_idle:.0f}s)" if max_idle else ""
            print(f"  {state:20} : {count:3} connections {idle_text}")
            total_after += count
        
        print(f"\n  Total Connections After Cleanup: {total_after}")
        
        # Step 5: PostgreSQL connection limits
        print("\n📊 PostgreSQL Connection Limits:")
        print("=" * 60)
        result = conn.execute(text("""
            SELECT 
                current_setting('max_connections')::int as max_connections,
                current_setting('superuser_reserved_connections')::int as reserved,
                (current_setting('max_connections')::int - 
                 current_setting('superuser_reserved_connections')::int) as user_available,
                (SELECT COUNT(*) FROM pg_stat_activity) as current_connections
        """))
        
        row = result.fetchone()
        max_conn, reserved, available, current = row
        utilization = (current / max_conn) * 100
        
        print(f"  Max Connections: {max_conn}")
        print(f"  Reserved for Superuser: {reserved}")
        print(f"  Available for Users: {available}")
        print(f"  Current Connections: {current}")
        print(f"  Utilization: {utilization:.1f}%")
        
        if utilization > 70:
            print(f"  ⚠️  WARNING: Utilization >70% ({utilization:.1f}%)")
        else:
            print(f"  ✅ OK: Connection pool healthy")
        
        print("\n" + "=" * 60)
        print("✨ Cleanup complete!")
        print("\n📝 You can now start the backend:")
        print("   python -m services.data_ingestion_pallavi.main")

except OperationalError as e:
    print(f"\n❌ ERROR: Cannot connect to PostgreSQL!")
    print(f"   {e}")
    print("\n🔧 Troubleshooting:")
    print("   1. Is PostgreSQL running? (Check: pg_isready)")
    print("   2. Is DATABASE_URL set correctly? (Check: echo $DATABASE_URL)")
    print("   3. Are credentials correct? (user/password)")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
