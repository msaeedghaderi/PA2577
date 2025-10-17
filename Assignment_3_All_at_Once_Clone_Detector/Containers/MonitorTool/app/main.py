from __future__ import annotations
import os, sqlite3
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

# Mongo sampler (we’re in a Mongo stack)
from .sampler_mongo import Sampler, SQLITE_PATH, TRACK_TABLES, SAMPLE_INTERVAL

app = FastAPI(title="MonitorTool")
templates = Jinja2Templates(directory="app/templates")

# serve static + exported artifacts
app.mount("/static", StaticFiles(directory="app/static"), name="static")
# exports live in /data/exports (mounted volume)
export_dir = os.getenv("MT_EXPORT_DIR", "/data/exports")
os.makedirs(export_dir, exist_ok=True)
app.mount("/exports", StaticFiles(directory=export_dir), name="exports")

# start background sampler
_sampler = Sampler()
_sampler.start()

def get_db():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    conn = get_db()
    samples = {}
    for t in TRACK_TABLES:
        rows = conn.execute(
            "SELECT * FROM samples WHERE table_name=? ORDER BY ts_utc DESC LIMIT 200", (t,)
        ).fetchall()
        samples[t] = [dict(r) for r in rows][::-1]
    fits = {}
    for t in TRACK_TABLES:
        rows = conn.execute(
            "SELECT kind, slope, intercept, r2, n, ts_utc FROM fits WHERE table_name=? "
            "ORDER BY ts_utc DESC LIMIT 2",
            (t,),
        ).fetchall()
        fits[t] = [dict(r) for r in rows]
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "samples": samples,
            "fits": fits,
            "tables": TRACK_TABLES,
            "sample_interval": SAMPLE_INTERVAL,  # handy for client-side auto-refresh
        },
    )

@app.get("/api/samples", response_class=JSONResponse)
def api_samples():
    conn = get_db()
    rows = conn.execute("SELECT * FROM samples ORDER BY ts_utc ASC").fetchall()
    return JSONResponse([dict(r) for r in rows])

@app.get("/api/fits", response_class=JSONResponse)
def api_fits():
    conn = get_db()
    rows = conn.execute("SELECT * FROM fits ORDER BY ts_utc DESC").fetchall()
    return JSONResponse([dict(r) for r in rows])

@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    conn = get_db()
    lines = []
    for t in TRACK_TABLES:
        row = conn.execute(
            "SELECT * FROM samples WHERE table_name=? ORDER BY ts_utc DESC LIMIT 1", (t,)
        ).fetchone()
        if row:
            lines.append(f'monitortool_total_count{{table="{t}"}} {row["total_count"]}')
            if row["mean_ms"] is not None:
                lines.append(f'monitortool_proc_mean_ms{{table="{t}"}} {row["mean_ms"]}')
                lines.append(f'monitortool_proc_p50_ms{{table="{t}"}} {row["p50_ms"]}')
                lines.append(f'monitortool_proc_p95_ms{{table="{t}"}} {row["p95_ms"]}')
    return PlainTextResponse("\n".join(lines) + "\n")

@app.get("/exports/", response_class=HTMLResponse)
def list_exports():
    root = Path(export_dir)
    items = []
    for p in sorted(root.iterdir()):
        if p.is_file():
            items.append(f'<li><a href="/exports/{p.name}">{p.name}</a></li>')
    return HTMLResponse("<h1>Exports</h1><ul>" + "\n".join(items) + "</ul>")