#!/usr/bin/env python3
"""
PostgreSQL Connection Health Diagnostic Script

Run this before and after applying fixes to:
1. Check connection pool configuration
2. Monitor connection usage during ingestion
3. Detect connection exhaustion early
4. Benchmark improvements
"""

import sys
import time
from datetime import datetime
import logging

# Add project to path
sys.path.insert(0, str(__file__).rsplit("\\", 2)[0])

from services.data_ingestion.app.db import engine, SessionLocal
from services.data_ingestion.app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def check_pool_configuration():
    """Show SQLAlchemy connection pool settings"""
    print("\n" + "="*80)
    print("CONNECTION POOL CONFIGURATION")
    print("="*80)
    
    pool = engine.pool
    print(f"Pool Type: {type(pool).__name__}")
    
    if hasattr(pool, "pool_size"):
        print(f"Pool Size (min connections kept open): {pool.pool_size}")
    if hasattr(pool, "max_overflow"):
        print(f"Max Overflow (additional connections): {pool.max_overflow}")
    if hasattr(pool, "timeout"):
        print(f"Connection Timeout: {pool.timeout} seconds")
    
    total_connections = pool.size() if hasattr(pool, "size") else "N/A"
    print(f"Current Pool Size: {total_connections}")


def check_postgresql_limits():
    """Query PostgreSQL for max_connections setting"""
    print("\n" + "="*80)
    print("POSTGRESQL CONNECTION LIMITS")
    print("="*80)
    
    try:
        session = SessionLocal()
        result = session.execute("SHOW max_connections;")
        max_conn = result.scalar()
        print(f"max_connections: {max_conn}")
        
        result = session.execute("SHOW superuser_reserved_connections;")
        reserved = result.scalar()
        print(f"superuser_reserved_connections: {reserved}")
        
        available = int(max_conn) - int(reserved)
        print(f"Available for users: {available}")
        
        session.close()
    except Exception as e:
        logger.error(f"Failed to query PostgreSQL: {e}")


def check_current_connections():
    """Check current active connections"""
    print("\n" + "="*80)
    print("CURRENT CONNECTION STATUS")
    print("="*80)
    
    try:
        session = SessionLocal()
        
        # Total connections
        result = session.execute("""
            SELECT COUNT(*) as total_connections
            FROM pg_stat_activity;
        """)
        total = result.scalar()
        print(f"Total Active Connections: {total}")
        
        # By state
        result = session.execute("""
            SELECT 
                state,
                COUNT(*) as count
            FROM pg_stat_activity
            WHERE datname IS NOT NULL
            GROUP BY state
            ORDER BY count DESC;
        """)
        print("\nConnections by State:")
        for row in result:
            print(f"  {row[0]:20s}: {row[1]}")
        
        # Idle in transaction (DANGEROUS)
        result = session.execute("""
            SELECT COUNT(*) as idle_in_transaction
            FROM pg_stat_activity
            WHERE state = 'idle in transaction';
        """)
        idle_in_txn = result.scalar()
        if idle_in_txn > 0:
            print(f"\n⚠️  WARNING: {idle_in_txn} connections idle in transaction (holding locks!)")
        
        session.close()
    except Exception as e:
        logger.error(f"Failed to check connections: {e}")


def check_utilization_percentage():
    """Show connection pool utilization"""
    print("\n" + "="*80)
    print("CONNECTION POOL UTILIZATION")
    print("="*80)
    
    try:
        session = SessionLocal()
        
        result = session.execute("""
            SELECT 
                (SELECT COUNT(*) FROM pg_stat_activity) as current_connections,
                current_setting('max_connections')::int as max_connections,
                current_setting('superuser_reserved_connections')::int as reserved,
                (current_setting('max_connections')::int - 
                 current_setting('superuser_reserved_connections')::int) as available_for_users,
                ROUND(100.0 * (SELECT COUNT(*) FROM pg_stat_activity) / 
                      (current_setting('max_connections')::int - 
                       current_setting('superuser_reserved_connections')::int), 1) as percent_of_limit;
        """)
        
        row = result.fetchone()
        current, max_conn, reserved, available, pct = row
        
        print(f"Current Connections: {current}")
        print(f"Max Allowed: {max_conn}")
        print(f"Reserved for Superuser: {reserved}")
        print(f"Available for Users: {available}")
        print(f"Utilization: {pct}%")
        
        if pct > 90:
            print("🔴 CRITICAL: Connection limit >90% utilized")
        elif pct > 70:
            print("🟡 WARNING: Connection limit >70% utilized")
        else:
            print("🟢 OK: Connection utilization healthy")
        
        session.close()
    except Exception as e:
        logger.error(f"Failed to check utilization: {e}")


def check_long_running_queries():
    """Check for queries running >60 seconds"""
    print("\n" + "="*80)
    print("LONG-RUNNING QUERIES (>60 seconds)")
    print("="*80)
    
    try:
        session = SessionLocal()
        
        result = session.execute("""
            SELECT 
                pid,
                usename,
                state,
                query_start,
                EXTRACT(EPOCH FROM (NOW() - query_start)) as duration_seconds,
                SUBSTRING(query, 1, 80) as query_preview
            FROM pg_stat_activity
            WHERE datname = 'hedis_db'
                AND state != 'idle'
                AND query NOT ILIKE '%pg_stat%'
                AND EXTRACT(EPOCH FROM (NOW() - query_start)) > 60
            ORDER BY query_start ASC;
        """)
        
        rows = result.fetchall()
        if not rows:
            print("✓ No long-running queries detected")
        else:
            print(f"Found {len(rows)} long-running query(ies):")
            for row in rows:
                pid, user, state, start, duration, query = row
                print(f"\n  PID: {pid}, User: {user}, State: {state}")
                print(f"  Duration: {duration:.1f}s")
                print(f"  Query: {query}...")
        
        session.close()
    except Exception as e:
        logger.error(f"Failed to check long-running queries: {e}")


def check_connection_per_user():
    """Check connections grouped by user/app"""
    print("\n" + "="*80)
    print("CONNECTIONS BY USER & APPLICATION")
    print("="*80)
    
    try:
        session = SessionLocal()
        
        result = session.execute("""
            SELECT 
                datname,
                usename,
                application_name,
                COUNT(*) as connection_count
            FROM pg_stat_activity
            WHERE datname IS NOT NULL
            GROUP BY datname, usename, application_name
            ORDER BY connection_count DESC;
        """)
        
        print(f"{'Database':<15} {'User':<15} {'Application':<30} {'Count':<8}")
        print("-" * 70)
        for row in result:
            db, user, app, count = row
            app_display = (app or "unknown")[:30]
            print(f"{db:<15} {user:<15} {app_display:<30} {count:<8}")
        
        session.close()
    except Exception as e:
        logger.error(f"Failed to check connections by user: {e}")


def test_connection_pool_throughput():
    """Quick test: how many connections can we acquire sequentially?"""
    print("\n" + "="*80)
    print("CONNECTION POOL STRESS TEST (Sequential)")
    print("="*80)
    
    connections_created = 0
    start_time = time.perf_counter()
    
    try:
        for i in range(30):
            session = SessionLocal()
            connections_created += 1
            session.execute("SELECT 1;")
            session.close()
    except Exception as e:
        logger.warning(f"Test stopped after {connections_created} connections: {e}")
    finally:
        elapsed = time.perf_counter() - start_time
        print(f"Created {connections_created} connections in {elapsed:.2f}s")
        print(f"Rate: {connections_created/elapsed:.1f} conn/sec")


def main():
    print("\n\n")
    print("╔" + "="*78 + "╗")
    print("║" + " "*78 + "║")
    print("║" + " POSTGRESQL CONNECTION HEALTH DIAGNOSTIC ".center(78) + "║")
    print("║" + " "*78 + "║")
    print("╚" + "="*78 + "╝")
    print(f"\nTimestamp: {datetime.now().isoformat()}")
    print(f"Database URL: {settings.database_url.replace('password', 'REDACTED')}")
    
    check_pool_configuration()
    check_postgresql_limits()
    check_current_connections()
    check_utilization_percentage()
    check_long_running_queries()
    check_connection_per_user()
    test_connection_pool_throughput()
    
    print("\n" + "="*80)
    print("RECOMMENDATIONS")
    print("="*80)
    print("""
If you see "too many clients" errors:
1. Check 'POSTGRESQL CONNECTION LIMITS' above
2. Run this script again during error to see utilization %
3. If >90%: Apply connection pool fixes from CONNECTION_ANALYSIS.md
4. If still failing: May need to increase PostgreSQL max_connections

If you see long-running queries:
1. Check 'LONG-RUNNING QUERIES' section above
2. These tie up connections and block other requests
3. Consider optimization or timeout settings

Connection pool size recommendation:
- pool_size = (expected concurrent ingestions × 2) + safety margin
- Current: pool_size=20, max_overflow=10 (total 30 connections max)
- For 3 concurrent ingestions: pool_size=20 is sufficient
    """)
    
    print("\n" + "="*80)
    print("END OF REPORT")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
