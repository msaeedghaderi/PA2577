from __future__ import annotations
import os, time, threading, sqlite3
from datetime import datetime, timezone
from typing import List, Tuple
from pymongo import MongoClient
from bson.objectid import ObjectId
import numpy as np
import pandas as pd

from .analysis import linear_fit, exponential_fit, summarize_durations

TRACK_TABLES = [t.strip() for t in os.getenv("MT_TRACK_TABLES","files,chunks,candidates,clones").split(",") if t.strip()]
STATUS_TABLE = os.getenv("MT_STATUS_UPDATES_TABLE", "status_updates")
SAMPLE_INTERVAL = int(os.getenv("MT_SAMPLE_INTERVAL_SECONDS", "30"))
SQLITE_PATH = os.getenv("MT_SQLITE_PATH", "/data/monitor.sqlite")
MT_DB_URL = os.getenv("MT_DB_URL")

client = MongoClient(MT_DB_URL)
# use the db encoded in the URI
db = client.get_default_database()

conn_sqlite = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
cur = conn_sqlite.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS samples(
    ts_utc TEXT NOT NULL, table_name TEXT NOT NULL, total_count INTEGER NOT NULL,
    new_rows INTEGER NOT NULL, mean_ms REAL, p50_ms REAL, p95_ms REAL );""")
cur.execute("""CREATE TABLE IF NOT EXISTS watermarks(
    table_name TEXT PRIMARY KEY, last_seen_id TEXT, last_seen_ts TEXT );""")
cur.execute("""CREATE TABLE IF NOT EXISTS new_rows(
    ts_utc TEXT NOT NULL, table_name TEXT NOT NULL, row_id TEXT, duration_ms REAL );""")
cur.execute("""CREATE TABLE IF NOT EXISTS fits(
    ts_utc TEXT NOT NULL, table_name TEXT NOT NULL, kind TEXT NOT NULL,
    slope REAL, intercept REAL, r2 REAL, n INTEGER );""")
conn_sqlite.commit()

def now_utc_iso(): return datetime.now(timezone.utc).isoformat()

def get_watermark(table: str) -> str | None:
    row = conn_sqlite.execute("SELECT last_seen_id FROM watermarks WHERE table_name=?", (table,)).fetchone()
    return row[0] if row and row[0] else None

def upsert_watermark(table: str, last_id: str | None):
    conn_sqlite.execute(
        "INSERT INTO watermarks(table_name,last_seen_id,last_seen_ts) VALUES(?,?,?) "
        "ON CONFLICT(table_name) DO UPDATE SET last_seen_id=excluded.last_seen_id,last_seen_ts=excluded.last_seen_ts",
        (table, last_id, now_utc_iso()))
    conn_sqlite.commit()

def get_total_count(table: str) -> int:
    return db[table].estimated_document_count()

def durations_from_doc(doc: dict) -> float | None:
    # priority: processing_time_ms / duration_ms (ms), processing_time/duration (seconds),
    # start_time & end_time (datetime), queued_at & created/inserted (datetime)
    if "processing_time_ms" in doc: return float(doc["processing_time_ms"])
    if "duration_ms" in doc: return float(doc["duration_ms"])
    if "processing_time" in doc: return float(doc["processing_time"]) * 1000.0
    if "duration" in doc: return float(doc["duration"]) * 1000.0
    st, et = doc.get("start_time"), doc.get("end_time")
    if st and et:
        try: return (et - st).total_seconds() * 1000.0
        except Exception: pass
    qa = doc.get("queued_at")
    ca = doc.get("created_at") or doc.get("inserted_at")
    if qa and ca:
        try: return (ca - qa).total_seconds() * 1000.0
        except Exception: pass
    return None

def get_new_docs_with_durations(table: str, last_seen: str | None) -> List[Tuple[str, float]]:
    col = db[table]
    q = {"_id": {"$gt": ObjectId(last_seen)}} if last_seen else {}
    cursor = col.find(q, projection={"_id": 1, "processing_time_ms": 1, "duration_ms": 1,
                                     "processing_time": 1, "duration": 1,
                                     "start_time": 1, "end_time": 1,
                                     "queued_at": 1, "created_at": 1, "inserted_at": 1}).sort("_id", 1).limit(10000)
    out = []
    for d in cursor:
        dur = durations_from_doc(d)
        out.append((str(d["_id"]), float(dur) if dur is not None else float("nan")))
    return out

def record_sample(table: str, total: int, new_rows: List[tuple[str, float]]):
    durations = [d for _, d in new_rows if not np.isnan(d)]
    stats = summarize_durations(durations)
    conn_sqlite.execute(
        "INSERT INTO samples(ts_utc,table_name,total_count,new_rows,mean_ms,p50_ms,p95_ms) VALUES(?,?,?,?,?,?,?)",
        (now_utc_iso(), table, total, len(new_rows), stats["mean_ms"], stats["p50_ms"], stats["p95_ms"]))
    if new_rows:
        conn_sqlite.executemany(
            "INSERT INTO new_rows(ts_utc,table_name,row_id,duration_ms) VALUES(?,?,?,?)",
            [(now_utc_iso(), table, rid, (None if np.isnan(d) else float(d))) for rid, d in new_rows])
    conn_sqlite.commit()

def compute_and_store_fits(table: str):
    df = pd.read_sql_query(
        "SELECT row_id, duration_ms FROM new_rows WHERE table_name=? AND duration_ms IS NOT NULL ORDER BY ROWID ASC",
        conn_sqlite, params=(table,))
    if df.empty: return
    df = df.reset_index(drop=True)
    df["n_processed"] = np.arange(1, len(df)+1)
    x, y = df["n_processed"].to_numpy(float), df["duration_ms"].to_numpy(float)
    lin, exp = linear_fit(x, y), exponential_fit(x, y)
    if lin:
        conn_sqlite.execute("INSERT INTO fits(ts_utc,table_name,kind,slope,intercept,r2,n) VALUES(?,?,?,?,?,?,?)",
                            (now_utc_iso(), table, lin.kind, lin.slope, lin.intercept, lin.r2, lin.n))
    if exp:
        conn_sqlite.execute("INSERT INTO fits(ts_utc,table_name,kind,slope,intercept,r2,n) VALUES(?,?,?,?,?,?,?)",
                            (now_utc_iso(), table, exp.kind, exp.slope, exp.intercept, exp.r2, exp.n))
    conn_sqlite.commit()

def poll_status_updates(table: str) -> int:
    try: return db[table].estimated_document_count()
    except Exception: return 0

class Sampler(threading.Thread):
    daemon = True
    def run(self):
        while True:
            for t in TRACK_TABLES:
                try:
                    last_id = get_watermark(t)
                    new_rows = get_new_docs_with_durations(t, last_id)
                    if new_rows:
                        upsert_watermark(t, new_rows[-1][0])
                    total = get_total_count(t)
                    record_sample(t, total, new_rows)
                    compute_and_store_fits(t)
                except Exception:
                    # on error, at least record count
                    try: total = get_total_count(t)
                    except Exception: total = 0
                    record_sample(t, total, [])
            if STATUS_TABLE:
                poll_status_updates(STATUS_TABLE)
            time.sleep(SAMPLE_INTERVAL)
