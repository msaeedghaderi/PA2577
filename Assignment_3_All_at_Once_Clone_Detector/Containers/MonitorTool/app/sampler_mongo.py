from __future__ import annotations
import os, time, threading, sqlite3
from datetime import datetime, timezone
from typing import List, Tuple

import numpy as np
import pandas as pd
from pymongo import MongoClient
from bson.objectid import ObjectId

# ---- env
TRACK_TABLES = [t.strip() for t in os.getenv("MT_TRACK_TABLES", "files,chunks,candidates,clones,statusUpdates").split(",") if t.strip()]
STATUS_TABLE = os.getenv("MT_STATUS_UPDATES_TABLE", "statusUpdates")  # optional
SAMPLE_INTERVAL = int(os.getenv("MT_SAMPLE_INTERVAL_SECONDS", "30"))
SQLITE_PATH = os.getenv("MT_SQLITE_PATH", "/data/monitor.sqlite")
MT_DB_URL = os.getenv("MT_DB_URL")

# exports
EXPORT_DIR = os.getenv("MT_EXPORT_DIR", "/data/exports")
RAW_LIMIT = int(os.getenv("MT_RAW_LIMIT", "100"))
EXPORT_EVERY_N_LOOPS = int(os.getenv("MT_EXPORT_EVERY_N_SAMPLES", "2"))

# ensure /data exists before SQLite open
os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

# ---- Mongo
if not MT_DB_URL or not (MT_DB_URL.startswith("mongodb://") or MT_DB_URL.startswith("mongodb+srv://")):
    raise RuntimeError("MT_DB_URL must be a Mongo URI and include a database, e.g. mongodb://dbstorage:27017/cloneDetector")

client = MongoClient(MT_DB_URL)
db = client.get_default_database()

# ---- SQLite (local time-series store)
conn_sqlite = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
cur = conn_sqlite.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS samples(
  ts_utc TEXT NOT NULL,
  table_name TEXT NOT NULL,
  total_count INTEGER NOT NULL,
  new_rows INTEGER NOT NULL,
  mean_ms REAL,
  p50_ms REAL,
  p95_ms REAL
);""")
cur.execute("""
CREATE TABLE IF NOT EXISTS watermarks(
  table_name TEXT PRIMARY KEY,
  last_seen_id TEXT,
  last_seen_ts TEXT
);""")
cur.execute("""
CREATE TABLE IF NOT EXISTS new_rows(
  ts_utc TEXT NOT NULL,
  table_name TEXT NOT NULL,
  row_id TEXT,
  duration_ms REAL
);""")
cur.execute("""
CREATE TABLE IF NOT EXISTS fits(
  ts_utc TEXT NOT NULL,
  table_name TEXT NOT NULL,
  kind TEXT NOT NULL,
  slope REAL,
  intercept REAL,
  r2 REAL,
  n INTEGER
);""")
conn_sqlite.commit()

# ---- analysis helpers
from .analysis import linear_fit, exponential_fit, summarize_durations

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

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
    try:
        return db[table].estimated_document_count()
    except Exception:
        return 0

def _duration_from_doc(doc: dict) -> float | None:
    # Prefer explicit ms -> sec -> derived timestamps
    if "processing_time_ms" in doc and doc["processing_time_ms"] is not None:
        return float(doc["processing_time_ms"])
    if "duration_ms" in doc and doc["duration_ms"] is not None:
        return float(doc["duration_ms"])
    if "processing_time" in doc and doc["processing_time"] is not None:
        return float(doc["processing_time"]) * 1000.0
    if "duration" in doc and doc["duration"] is not None:
        return float(doc["duration"]) * 1000.0

    st, et = doc.get("start_time"), doc.get("end_time")
    if st and et:
        try:
            return (et - st).total_seconds() * 1000.0
        except Exception:
            pass

    qa = doc.get("queued_at")
    ca = doc.get("created_at") or doc.get("inserted_at")
    if qa and ca:
        try:
            return (ca - qa).total_seconds() * 1000.0
        except Exception:
            pass

    return None

def get_new_docs_with_durations(table: str, last_seen: str | None) -> List[Tuple[str, float]]:
    col = db[table]
    q = {"_id": {"$gt": ObjectId(last_seen)}} if (last_seen and len(last_seen) == 24) else {}
    proj = {
        "_id": 1,
        "processing_time_ms": 1, "duration_ms": 1,
        "processing_time": 1, "duration": 1,
        "start_time": 1, "end_time": 1,
        "queued_at": 1, "created_at": 1, "inserted_at": 1
    }
    out: List[Tuple[str, float]] = []
    for d in col.find(q, projection=proj).sort("_id", 1).limit(10000):
        rid = str(d["_id"])
        dur = _duration_from_doc(d)
        out.append((rid, float(dur) if dur is not None else float("nan")))
    return out

def record_sample(table: str, total_count: int, new_rows: List[tuple[str, float]]):
    durations = [d for _, d in new_rows if not np.isnan(d)]
    stats = summarize_durations(durations)
    conn_sqlite.execute(
        "INSERT INTO samples(ts_utc, table_name, total_count, new_rows, mean_ms, p50_ms, p95_ms) VALUES(?,?,?,?,?,?,?)",
        (now_utc_iso(), table, total_count, len(new_rows), stats["mean_ms"], stats["p50_ms"], stats["p95_ms"])
    )
    if new_rows:
        conn_sqlite.executemany(
            "INSERT INTO new_rows(ts_utc, table_name, row_id, duration_ms) VALUES(?,?,?,?)",
            [(now_utc_iso(), table, rid, (None if np.isnan(d) else float(d))) for rid, d in new_rows]
        )
    conn_sqlite.commit()

def compute_and_store_fits(table: str):
    df = pd.read_sql_query(
        "SELECT row_id, duration_ms FROM new_rows WHERE table_name=? AND duration_ms IS NOT NULL ORDER BY ROWID ASC",
        conn_sqlite, params=(table,))
    if df.empty:
        return
    df = df.reset_index(drop=True)
    df["n_processed"] = np.arange(1, len(df) + 1)
    x = df["n_processed"].to_numpy(dtype=float)
    y = df["duration_ms"].to_numpy(dtype=float)

    lin = linear_fit(x, y)
    exp = exponential_fit(x, y)

    if lin:
        conn_sqlite.execute(
            "INSERT INTO fits(ts_utc, table_name, kind, slope, intercept, r2, n) VALUES(?,?,?,?,?,?,?)",
            (now_utc_iso(), table, lin.kind, lin.slope, lin.intercept, lin.r2, lin.n)
        )
    if exp:
        conn_sqlite.execute(
            "INSERT INTO fits(ts_utc, table_name, kind, slope, intercept, r2, n) VALUES(?,?,?,?,?,?,?)",
            (now_utc_iso(), table, exp.kind, exp.slope, exp.intercept, exp.r2, exp.n)
        )
    conn_sqlite.commit()

def poll_status_updates(table: str) -> int:
    try:
        return db[table].estimated_document_count()
    except Exception:
        return 0

# ---- exporter (optional but handy)
from bson import json_util
def _dump_collection_head(coll_name: str, limit: int = RAW_LIMIT):
    try:
        docs = list(db[coll_name].find({}, {}).sort("_id", 1).limit(limit))
        path = os.path.join(EXPORT_DIR, f"{coll_name}_head.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json_util.dumps(docs, json_options=json_util.RELAXED_JSON_OPTIONS))
    except Exception:
        pass

def _export_sqlite_heads():
    try:
        import pandas as pd
        # first 100 samples
        path = os.path.join(EXPORT_DIR, "samples_head.csv")
        df = pd.read_sql_query(
            "SELECT * FROM samples ORDER BY ts_utc ASC LIMIT ?",
            conn_sqlite, params=(RAW_LIMIT,))
        df.to_csv(path, index=False)
        # all fits
        path2 = os.path.join(EXPORT_DIR, "fits.csv")
        df2 = pd.read_sql_query(
            "SELECT * FROM fits ORDER BY table_name, ts_utc DESC",
            conn_sqlite)
        df2.to_csv(path2, index=False)
    except Exception:
        pass

def _write_report_md():
    try:
        # latest fits per table
        rows = conn_sqlite.execute("""
          SELECT f.* FROM fits f
          JOIN (SELECT table_name, kind, MAX(ts_utc) ts
                FROM fits GROUP BY table_name, kind) g
            ON g.table_name=f.table_name AND g.kind=f.kind AND g.ts=f.ts_utc
        """).fetchall()
        fits = {}
        for r in rows:
            d = {k: r[idx] for idx, k in enumerate([c[0] for c in conn_sqlite.execute("PRAGMA table_info(fits)").fetchall()])}
        # Simpler: build dict from row
        fits = {}
        for r in rows:
            d = dict(zip([col[1] for col in conn_sqlite.execute("PRAGMA table_info(fits)").fetchall()], r))
            fits.setdefault(d["table_name"], {})[d["kind"]] = d
        # classify
        def classify(dk):
            L, E = dk.get("linear"), dk.get("exponential")
            if not L and not E: return "insufficient data"
            if L and (not E or float(L["r2"]) >= float(E["r2"]) - 0.02):
                if abs(float(L["slope"])) < 1e-6: return "roughly constant"
                return "linear ↑" if float(L["slope"]) > 0 else "linear ↓"
            if E: return "exponential ↑" if float(E["slope"]) > 0 else "exponential ↓"
            return "insufficient data"

        lines = [f"# Clone Detection – Monitoring Report\n", f"Generated: {now_utc_iso()}\n", "## Summary (latest fits)\n"]
        for t in TRACK_TABLES:
            dk = fits.get(t, {})
            if not dk:
                lines.append(f"- **{t}**: no fit yet")
                continue
            parts = []
            for kind in ("linear", "exponential"):
                r = dk.get(kind)
                if r:
                    parts.append(f"{kind}: slope={float(r['slope']):.4g}, r²={float(r['r2']):.3f}, n={int(r['n'])}")
            lines.append(f"- **{t}**: {classify(dk)} ({'; '.join(parts)})")
        lines.append("\n## Raw samples\n- `samples_head.csv`\n- `fits.csv`\n" + "".join([f"- `{t}_head.json`\n" for t in TRACK_TABLES]))
        with open(os.path.join(EXPORT_DIR, "report.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass

def run_export_all():
    _export_sqlite_heads()
    for t in TRACK_TABLES:
        _dump_collection_head(t, RAW_LIMIT)
    _write_report_md()

# ---- sampler thread
class Sampler(threading.Thread):
    daemon = True
    def run(self):
        loops = 0
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
                    try:
                        total = get_total_count(t)
                    except Exception:
                        total = 0
                    record_sample(t, total, [])
            if STATUS_TABLE:
                poll_status_updates(STATUS_TABLE)

            loops += 1
            if EXPORT_EVERY_N_LOOPS > 0 and loops % EXPORT_EVERY_N_LOOPS == 0:
                try:
                    run_export_all()
                except Exception:
                    pass

            time.sleep(SAMPLE_INTERVAL)
