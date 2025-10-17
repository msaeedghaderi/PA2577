from __future__ import annotations
import os
import time
import threading
from datetime import datetime, timezone
from typing import List, Tuple

from sqlalchemy import text, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
import pandas as pd
import numpy as np

from .db import engine
from .analysis import linear_fit, exponential_fit, summarize_durations

TRACK_TABLES = [t.strip() for t in os.getenv('MT_TRACK_TABLES', 'files,chunks,candidates,clones,statusUpdates').split(',') if t.strip()]
STATUS_TABLE = os.getenv('MT_STATUS_UPDATES_TABLE', 'statusUpdates')
SAMPLE_INTERVAL = int(os.getenv('MT_SAMPLE_INTERVAL_SECONDS', '300'))
SQLITE_PATH = os.getenv('MT_SQLITE_PATH', '/data/monitor.sqlite')

import sqlite3
conn_sqlite = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
cur = conn_sqlite.cursor()
cur.execute('''
CREATE TABLE IF NOT EXISTS samples(
    ts_utc TEXT NOT NULL,
    table_name TEXT NOT NULL,
    total_count INTEGER NOT NULL,
    new_rows INTEGER NOT NULL,
    mean_ms REAL,
    p50_ms REAL,
    p95_ms REAL
);
''')
cur.execute('''
CREATE TABLE IF NOT EXISTS watermarks(
    table_name TEXT PRIMARY KEY,
    last_seen_id TEXT,
    last_seen_ts TEXT
);
''')
cur.execute('''
CREATE TABLE IF NOT EXISTS new_rows(
    ts_utc TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_id TEXT,
    duration_ms REAL
);
''')
cur.execute('''
CREATE TABLE IF NOT EXISTS fits(
    ts_utc TEXT NOT NULL,
    table_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    slope REAL,
    intercept REAL,
    r2 REAL,
    n INTEGER
);
''')
conn_sqlite.commit()

def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()

def detect_keys_and_times(pg_engine: Engine, table: str) -> dict:
    inspector = inspect(pg_engine)
    pk_cols = inspector.get_pk_constraint(table).get('constrained_columns') or []
    columns = [c['name'] for c in inspector.get_columns(table)]
    key = pk_cols[0] if pk_cols else ('id' if 'id' in columns else columns[0])
    return {
        'pk': key,
        'has_ms': next((c for c in ['processing_time_ms', 'duration_ms'] if c in columns), None),
        'has_secs': next((c for c in ['processing_time', 'duration'] if c in columns), None),
        'start_end': ('start_time' in columns and 'end_time' in columns),
        'queued_created': ('queued_at' in columns and ('created_at' in columns or 'inserted_at' in columns)),
        'created_col': 'created_at' if 'created_at' in columns else ('inserted_at' if 'inserted_at' in columns else None),
    }

def get_total_count(pg_engine: Engine, table: str) -> int:
    with pg_engine.connect() as c:
        return int(c.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar() or 0)

def get_new_rows_with_duration(pg_engine: Engine, table: str, strat: dict, last_seen_id: str | None) -> List[Tuple[str, float]]:
    pk = strat['pk']
    order_by = f'ORDER BY {pk} ASC'
    where = ''
    params = {}
    if last_seen_id is not None:
        where = f'WHERE {pk} > :last_id'
        params['last_id'] = last_seen_id

    if strat['has_ms']:
        sql = f'SELECT {pk} AS pk, {strat["has_ms"]} AS duration_ms FROM {table} {where} {order_by} LIMIT 10000'
        with pg_engine.connect() as c:
            rows = c.execute(text(sql), params).mappings().all()
        return [(str(r['pk']), float(r['duration_ms'])) for r in rows if r['duration_ms'] is not None]

    if strat['has_secs']:
        sql = f'SELECT {pk} AS pk, {strat["has_secs"]} AS duration_secs FROM {table} {where} {order_by} LIMIT 10000'
        with pg_engine.connect() as c:
            rows = c.execute(text(sql), params).mappings().all()
        return [(str(r['pk']), float(r['duration_secs']) * 1000.0) for r in rows if r['duration_secs'] is not None]

    if strat['start_end']:
        sql = f'''SELECT {pk} AS pk,
            EXTRACT(EPOCH FROM (end_time - start_time)) * 1000.0 AS duration_ms
            FROM {table} {where} {order_by} LIMIT 10000'''
        with pg_engine.connect() as c:
            rows = c.execute(text(sql), params).mappings().all()
        return [(str(r['pk']), float(r['duration_ms'])) for r in rows if r['duration_ms'] is not None]

    if strat['queued_created']:
        created = strat['created_col']
        sql = f'''SELECT {pk} AS pk,
            EXTRACT(EPOCH FROM ({created} - queued_at)) * 1000.0 AS duration_ms
            FROM {table} {where} {order_by} LIMIT 10000'''
        with pg_engine.connect() as c:
            rows = c.execute(text(sql), params).mappings().all()
        return [(str(r['pk']), float(r['duration_ms'])) for r in rows if r['duration_ms'] is not None]

    if where:
        sql = f'SELECT {pk} AS pk FROM {table} {where} {order_by} LIMIT 10000'
        with pg_engine.connect() as c:
            rows = c.execute(text(sql), params).mappings().all()
        return [(str(r['pk']), float('nan')) for r in rows]
    return []

def upsert_watermark(table: str, last_id: str | None):
    cur = conn_sqlite.cursor()
    cur.execute(
        'INSERT INTO watermarks(table_name, last_seen_id, last_seen_ts) VALUES(?,?,?) '
        'ON CONFLICT(table_name) DO UPDATE SET last_seen_id=excluded.last_seen_id, last_seen_ts=excluded.last_seen_ts',
        (table, last_id, now_utc_iso())
    )
    conn_sqlite.commit()

def get_watermark(table: str) -> str | None:
    cur = conn_sqlite.cursor()
    cur.execute('SELECT last_seen_id FROM watermarks WHERE table_name=?', (table,))
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None

def record_sample(table: str, total_count: int, new_rows: List[tuple[str, float]]):
    durations = [d for _, d in new_rows if isinstance(d, (int, float)) and not np.isnan(d)]
    stats = summarize_durations(durations)
    cur = conn_sqlite.cursor()
    cur.execute(
        'INSERT INTO samples(ts_utc, table_name, total_count, new_rows, mean_ms, p50_ms, p95_ms) VALUES(?,?,?,?,?,?,?)',
        (now_utc_iso(), table, total_count, len(new_rows), stats['mean_ms'], stats['p50_ms'], stats['p95_ms'])
    )
    if new_rows:
        cur.executemany(
            'INSERT INTO new_rows(ts_utc, table_name, row_id, duration_ms) VALUES(?,?,?,?)',
            [(now_utc_iso(), table, rid, float(d) if not np.isnan(d) else None) for rid, d in new_rows]
        )
    conn_sqlite.commit()

def compute_and_store_fits(table: str):
    df = pd.read_sql_query(
        'SELECT row_id, duration_ms FROM new_rows WHERE table_name=? AND duration_ms IS NOT NULL ORDER BY ROWID ASC',
        conn_sqlite,
        params=(table,)
    )
    if df.empty:
        return
    df = df.reset_index(drop=True)
    df['n_processed'] = np.arange(1, len(df) + 1)
    x = df['n_processed'].to_numpy(dtype=float)
    y = df['duration_ms'].to_numpy(dtype=float)

    lin = linear_fit(x, y)
    exp = exponential_fit(x, y)

    cur = conn_sqlite.cursor()
    if lin:
        cur.execute(
            'INSERT INTO fits(ts_utc, table_name, kind, slope, intercept, r2, n) VALUES(?,?,?,?,?,?,?)',
            (now_utc_iso(), table, lin.kind, lin.slope, lin.intercept, lin.r2, lin.n)
        )
    if exp:
        cur.execute(
            'INSERT INTO fits(ts_utc, table_name, kind, slope, intercept, r2, n) VALUES(?,?,?,?,?,?,?)',
            (now_utc_iso(), table, exp.kind, exp.slope, exp.intercept, exp.r2, exp.n)
        )
    conn_sqlite.commit()

def poll_status_updates(pg_engine: Engine, table: str) -> int:
    try:
        with pg_engine.connect() as c:
            cnt = c.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar()
            return int(cnt or 0)
    except Exception:
        return 0

class Sampler(threading.Thread):
    daemon = True

    def run(self):
        strategies = {}
        for t in TRACK_TABLES:
            try:
                strategies[t] = detect_keys_and_times(engine, t)
            except Exception:
                strategies[t] = None

        while True:
            for t in TRACK_TABLES:
                try:
                    strat = strategies.get(t) or detect_keys_and_times(engine, t)
                    last_id = get_watermark(t)
                    new_rows = get_new_rows_with_duration(engine, t, strat, last_id)
                    if new_rows:
                        upsert_watermark(t, new_rows[-1][0])
                    total = get_total_count(engine, t)
                    record_sample(t, total, new_rows)
                    compute_and_store_fits(t)
                except SQLAlchemyError:
                    try:
                        total = get_total_count(engine, t)
                    except Exception:
                        total = 0
                    record_sample(t, total_count=total, new_rows=[])
                except Exception:
                    pass

            if STATUS_TABLE:
                poll_status_updates(engine, STATUS_TABLE)

            time.sleep(SAMPLE_INTERVAL)
