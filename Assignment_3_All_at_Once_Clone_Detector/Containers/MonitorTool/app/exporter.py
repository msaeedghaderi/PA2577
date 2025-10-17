from __future__ import annotations
import os, io, csv, sqlite3, datetime as dt
from typing import List, Dict, Any
import pandas as pd
from bson import json_util

# These are provided by sampler_mongo at import-time:
# - client (MongoClient), db (Database), SQLITE_PATH (str), TRACK_TABLES (list[str])

EXPORT_DIR = os.getenv("MT_EXPORT_DIR", "/data/exports")
RAW_LIMIT = int(os.getenv("MT_RAW_LIMIT", "100"))

def _ensure_dir():
    os.makedirs(EXPORT_DIR, exist_ok=True)

def dump_collection_head(db, coll_name: str, limit: int = RAW_LIMIT) -> str:
    """Dump first N docs of a Mongo collection to JSON (relaxed EJSON) and return path."""
    _ensure_dir()
    path = os.path.join(EXPORT_DIR, f"{coll_name}_head.json")
    cur = db[coll_name].find({}, {}).sort("_id", 1).limit(limit)
    docs = list(cur)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json_util.dumps(docs, json_options=json_util.RELAXED_JSON_OPTIONS))
    return path

def export_sqlite_samples_head(sqlite_path: str, limit: int = RAW_LIMIT) -> str:
    _ensure_dir()
    path = os.path.join(EXPORT_DIR, "samples_head.csv")
    conn = sqlite3.connect(sqlite_path)
    df = pd.read_sql_query(
        "SELECT * FROM samples ORDER BY ts_utc ASC LIMIT ?",
        conn, params=(limit,)
    )
    df.to_csv(path, index=False)
    conn.close()
    return path

def export_sqlite_fits(sqlite_path: str) -> str:
    _ensure_dir()
    path = os.path.join(EXPORT_DIR, "fits.csv")
    conn = sqlite3.connect(sqlite_path)
    df = pd.read_sql_query(
        "SELECT * FROM fits ORDER BY table_name, ts_utc DESC", conn
    )
    df.to_csv(path, index=False)
    conn.close()
    return path

def compute_avg_clone_size(db) -> float | None:
    # Try common shapes; return first successful average
    try:
        # clones with array of chunkIds
        res = list(db.clones.aggregate([
            {"$project": {"size": {"$size": "$chunkIds"}}},
            {"$group": {"_id": None, "avg": {"$avg": "$size"}}}
        ], allowDiskUse=True))
        if res: return float(res[0]["avg"])
    except Exception:
        pass
    try:
        # clones with numeric length/size field
        res = list(db.clones.aggregate([
            {"$group": {"_id": None, "avg": {"$avg": "$length"}}}
        ], allowDiskUse=True))
        if res: return float(res[0]["avg"])
    except Exception:
        pass
    try:
        # membership stored in candidates via cloneId
        res = list(db.candidates.aggregate([
            {"$group": {"_id": "$cloneId", "n": {"$sum": 1}}},
            {"$group": {"_id": None, "avg": {"$avg": "$n"}}}
        ], allowDiskUse=True))
        if res: return float(res[0]["avg"])
    except Exception:
        pass
    return None

def compute_avg_chunks_per_file(db) -> float | None:
    try:
        # chunks reference fileId
        res = list(db.chunks.aggregate([
            {"$group": {"_id": "$fileId", "n": {"$sum": 1}}},
            {"$group": {"_id": None, "avg": {"$avg": "$n"}}}
        ], allowDiskUse=True))
        if res: return float(res[0]["avg"])
    except Exception:
        pass
    try:
        # files store nChunks
        res = list(db.files.aggregate([
            {"$group": {"_id": None, "avg": {"$avg": "$nChunks"}}}
        ], allowDiskUse=True))
        if res: return float(res[0]["avg"])
    except Exception:
        pass
    return None

def latest_fits(sqlite_path: str) -> Dict[str, Dict[str, Any]]:
    """Return {table: {kind: row}} for the most recent fits."""
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
      SELECT f.* FROM fits f
      JOIN (SELECT table_name, kind, MAX(ts_utc) ts
            FROM fits GROUP BY table_name, kind) g
        ON g.table_name=f.table_name AND g.kind=f.kind AND g.ts=f.ts_utc
    """).fetchall()
    conn.close()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        out.setdefault(d["table_name"], {})[d["kind"]] = d
    return out

def _classify(dkinds: Dict[str, Any]) -> str:
    L = dkinds.get("linear"); E = dkinds.get("exponential")
    if not L and not E: return "insufficient data"
    if L and (not E or L["r2"] >= E["r2"] - 0.02):
        if abs(L["slope"]) < 1e-6: return "roughly constant"
        return "linear ↑" if L["slope"] > 0 else "linear ↓"
    if E: return "exponential ↑" if E["slope"] > 0 else "exponential ↓"
    return "insufficient data"

def write_report_markdown(sqlite_path: str, db, track_tables: List[str]) -> str:
    _ensure_dir()
    path = os.path.join(EXPORT_DIR, "report.md")
    fits = latest_fits(sqlite_path)
    avg_clone = compute_avg_clone_size(db)
    avg_chunks_file = compute_avg_chunks_per_file(db)

    now = dt.datetime.utcnow().isoformat() + "Z"
    lines = []
    lines.append(f"# Clone Detection – Monitoring Report\n\nGenerated: {now}\n")
    lines.append("## Summary (latest fits)\n")
    for t in track_tables:
        dk = fits.get(t, {})
        if not dk:
            lines.append(f"- **{t}**: no fit yet")
            continue
        parts = []
        for kind in ("linear","exponential"):
            r = dk.get(kind)
            if r:
                parts.append(f"{kind}: slope={r['slope']:.4g}, r²={r['r2']:.3f}, n={r['n']}")
        lines.append(f"- **{t}**: {_classify(dk)} ({'; '.join(parts)})")
    lines.append("\n## Averages\n")
    lines.append(f"- Average clone size: **{avg_clone if avg_clone is not None else 'n/a'}**")
    lines.append(f"- Average chunks per file: **{avg_chunks_file if avg_chunks_file is not None else 'n/a'}**")
    lines.append("\n## Raw samples\n")
    lines.append("- `samples_head.csv` (first 100 samples)")
    for t in track_tables:
        lines.append(f"- `{t}_head.json` (first 100 docs)")
    lines.append("- `fits.csv` (all fits)\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path

def export_all(sqlite_path: str, db, track_tables: List[str]) -> Dict[str, str]:
    """Run full export once; return map of artifact paths."""
    _ensure_dir()
    out = {}
    # SQLite exports
    out["samples_head.csv"] = export_sqlite_samples_head(sqlite_path)
    out["fits.csv"] = export_sqlite_fits(sqlite_path)
    # Mongo raw heads
    for t in track_tables:
        try:
            out[f"{t}_head.json"] = dump_collection_head(db, t, RAW_LIMIT)
        except Exception:
            # keep going even if one coll is missing
            pass
    # Markdown report
    out["report.md"] = write_report_markdown(sqlite_path, db, track_tables)
    return out
