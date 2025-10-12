import os
import time
import logging
from typing import Optional

from fastapi import FastAPI
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ServerSelectionTimeoutError

# ----------------------------
# Config via environment
# ----------------------------
DEFAULT_DBHOST = os.environ.get("DBHOST", "dbstorage")  # compose service name
DEFAULT_DBNAME = os.environ.get("DBNAME", "monitor")
DEFAULT_COLL   = os.environ.get("SAMPLES_COLL", "samples")

MONGODB_URI = os.environ.get(
    "MONGODB_URI",
    f"mongodb://{DEFAULT_DBHOST}:27017/{DEFAULT_DBNAME}"
)

SERVER_SELECTION_TIMEOUT_MS = int(os.environ.get("MONGO_CONNECT_TIMEOUT_MS", "20000"))
CONNECT_RETRIES = int(os.environ.get("MONGO_CONNECT_RETRIES", "60"))   # ~60 * 1s = 1 minute
CONNECT_SLEEP_S = float(os.environ.get("MONGO_CONNECT_RETRY_DELAY_S", "1.0"))

# ----------------------------
# App + logging
# ----------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("monitor-tool")

app = FastAPI(title="Monitor Tool")

_mongo_client: Optional[MongoClient] = None
_db = None
_samples = None


def _connect_to_mongo() -> None:
    """Connect to MongoDB with retries and create indexes."""
    global _mongo_client, _db, _samples

    log.info("Connecting to MongoDB URI: %s", MONGODB_URI)

    last_err = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
            # Force connection check
            client.admin.command("ping")

            # Determine DB name: prefer URI's default DB; fallback to env DBNAME
            db = client.get_default_database()
            if db is None or db.name in (None, "", "admin"):
                db = client[DEFAULT_DBNAME]

            samples_coll_name = DEFAULT_COLL
            samples = db[samples_coll_name]

            # Ensure indexes (background is ignored on modern Mongo but harmless)
            samples.create_index([("ts", ASCENDING)], name="ts_asc", background=True)

            # Promote to globals only on success
            _mongo_client = client
            _db = db
            _samples = samples

            log.info("Connected to MongoDB on attempt %d; using db=%s, coll=%s",
                     attempt, _db.name, samples_coll_name)
            return
        except ServerSelectionTimeoutError as e:
            last_err = e
            log.warning("Mongo not ready (attempt %d/%d): %s", attempt, CONNECT_RETRIES, str(e))
            time.sleep(CONNECT_SLEEP_S)

    # If we exit loop, we failed to connect
    raise RuntimeError(f"Could not connect to MongoDB after {CONNECT_RETRIES} attempts: {last_err}")


@app.on_event("startup")
def on_startup():
    _connect_to_mongo()


@app.on_event("shutdown")
def on_shutdown():
    global _mongo_client
    if _mongo_client is not None:
        _mongo_client.close()
        _mongo_client = None
        log.info("Closed MongoDB connection")


@app.get("/healthz")
def healthz():
    """Lightweight health check endpoint."""
    try:
        if _mongo_client is None:
            return {"status": "starting", "mongo": "disconnected"}
        _mongo_client.admin.command("ping")
        return {
            "status": "ok",
            "mongo": "connected",
            "db": getattr(_db, "name", None),
            "collection": getattr(_samples, "name", None),
        }
    except Exception as e:
        return {"status": "degraded", "mongo": "error", "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "80")),
        reload=bool(int(os.environ.get("UVICORN_RELOAD", "0")))
    )
