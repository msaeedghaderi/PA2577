import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient, ASCENDING
from bson.objectid import ObjectId
import numpy as np
import uvicorn

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "test-db")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL_SEC", "5"))

TRACKED = ["files", "chunks", "candidates", "clones"]
SAMPLES_COLL = "monitorSamples"
STATUS_MIRROR_COLL = "monitorStatusMirror"
STATUS_COLL_CANDIDATES = ["statusUpdates", "statusupdates"]

client = MongoClient(MONGODB_URI)
db = client[MONGODB_DB]

app = FastAPI(title="MonitorTool")
app.mount("/static", StaticFiles(directory="static"), name="static")

# serve the UI
@app.get("/")
def index():
    return FileResponse("static/index.html")

db[SAMPLES_COLL].create_index([("ts", ASCENDING)], background=True)
db[STATUS_MIRROR_COLL].create_index([("ts", ASCENDING)], background=True)

existing = set(db.list_collection_names())
STATUS_COLL = next((c for c in STATUS_COLL_CANDIDATES if c in existing), STATUS_COLL_CANDIDATES[0])

state = {
    "last_counts": {k: 0 for k in TRACKED},
    "last_status_ts": None,
    "last_status_id": None,
    "running": True
}

def _parse_dt_maybe(v: Any) -> Optional[datetime]:
    from datetime import datetime as dt
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc)
    if isinstance(v, str):
        try:
            d = dt.fromisoformat(v.replace("Z", ""))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None

def sample_once():
    now = datetime.now(timezone.utc)
    counts = {name: db[name].count_documents({}) for name in TRACKED}

    new_updates = []
    coll = db[STATUS_COLL]
    q = {}
    if state["last_status_ts"] is not None:
        q = {"$or": [
            {"timestamp": {"$gt": state["last_status_ts"]}},
            {"timestamp": {"$gt": state["last_status_ts"].isoformat()}}
        ]}
    elif state["last_status_id"] is not None:
        q = {"_id": {"$gt": state["last_status_id"]}}

    try:
        cursor = coll.find(q).sort([("_id", ASCENDING)])
        last_seen_ts = state["last_status_ts"]
        last_seen_id = state["last_status_id"]
        for doc in cursor:
            ts_val = _parse_dt_maybe(doc.get("timestamp")) or _parse_dt_maybe(doc.get("createdAt")) or now
            new_updates.append({"ts": ts_val, "message": doc.get("message"), "raw_id": str(doc.get("_id"))})
            if last_seen_ts is None or ts_val > last_seen_ts:
                last_seen_ts = ts_val
            oid = ObjectId(doc["_id"])
            if last_seen_id is None or oid > last_seen_id:
                last_seen_id = oid
        state["last_status_ts"] = last_seen_ts
        state["last_status_id"] = last_seen_id
    except Exception:
        pass

    prev = state["last_counts"]
    deltas = {k: counts[k] - prev.get(k, 0) for k in TRACKED}
    interval = POLL_INTERVAL
    stats = {
        k: {
            "new_units": max(0, deltas[k]),
            "rate": (max(0, deltas[k]) / interval) if interval > 0 else 0.0,
            "sec_per_unit": (interval / max(1, deltas[k])) if deltas[k] > 0 else None
        } for k in TRACKED
    }

    db[SAMPLES_COLL].insert_one({
        "ts": now, "counts": counts, "deltas": deltas,
        "interval_sec": interval, "stats": stats
    })

    if new_updates:
        db[STATUS_MIRROR_COLL].insert_many(
            [{"ts": u["ts"], "message": u["message"], "status_id": u["raw_id"]} for u in new_updates]
        )

    state["last_counts"] = counts

def sampler_loop():
    try:
        state["last_counts"] = {k: db[k].count_documents({}) for k in TRACKED}
    except Exception:
        pass
    while state["running"]:
        t0 = time.time()
        sample_once()
        time.sleep(max(0.0, POLL_INTERVAL - (time.time() - t0)))

threading.Thread(target=sampler_loop, daemon=True).start()

def _fetch_series(limit: int = 600):
    docs = list(db[SAMPLES_COLL].find({}).sort([("ts", ASCENDING)]).limit(limit))
    ts = [d["ts"].isoformat() for d in docs]
    counts = {k: [d["counts"].get(k, 0) for d in docs] for k in TRACKED}
    rates = {k: [d["stats"][k]["rate"] for d in docs] for k in TRACKED}
    sec_per = {k: [d["stats"][k]["sec_per_unit"] for d in docs] for k in TRACKED}
    return ts, counts, rates, sec_per

def _trend_fits(xs: List[int], ys: List[Optional[float]]) -> Dict[str, Any]:
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if len(pts) < 3:
        return {"linear": None, "exponential": None}
    import numpy as np
    X = np.array([p[0] for p in pts], dtype=float)
    Y = np.array([p[1] for p in pts], dtype=float)

    a_lin, b_lin = np.polyfit(X, Y, 1)
    yhat_lin = a_lin * X + b_lin
    r2_lin = 1.0 - (np.sum((Y - yhat_lin) ** 2) / np.sum((Y - np.mean(Y)) ** 2))

    exp = None
    mask = Y > 0
    if np.sum(mask) >= 3:
        Xp, L = X[mask], np.log(Y[mask])
        a_exp, b_exp = np.polyfit(Xp, L, 1)
        yhat_exp = np.exp(a_exp * Xp + b_exp)
        r2_exp = 1.0 - (np.sum((Y[mask] - yhat_exp) ** 2) / np.sum((Y[mask] - np.mean(Y[mask])) ** 2))
        exp = {"a": float(a_exp), "b": float(b_exp), "r2": float(r2_exp)}

    return {"linear": {"a": float(a_lin), "b": float(b_lin), "r2": float(r2_lin)}, "exponential": exp}

@app.get("/api/stats")
def api_stats(limit: int = 600):
    ts, counts, rates, sec_per = _fetch_series(limit)
    x_idx = list(range(len(ts)))
    fits = {k: _trend_fits(x_idx, sec_per[k]) for k in TRACKED}
    return JSONResponse({
        "timestamps": ts,
        "counts": counts,
        "rates": rates,
        "sec_per_unit": sec_per,
        "fits": fits,
        "poll_interval_sec": POLL_INTERVAL,
        "status_collection": STATUS_COLL,
        "tracked": TRACKED
    })

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
