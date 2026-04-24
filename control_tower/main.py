import os, json, uuid, sqlite3
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List

DB_PATH = "/tmp/control_tower.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS containers (
            id TEXT PRIMARY KEY, reference TEXT, client_name TEXT,
            origin TEXT, destination TEXT, status TEXT, current_site TEXT,
            seal_number TEXT, created_at TEXT, updated_at TEXT,
            version INTEGER DEFAULT 1, site_id TEXT
        );
        CREATE TABLE IF NOT EXISTS trips (
            id TEXT PRIMARY KEY, container_id TEXT, driver_name TEXT,
            vehicle_reg TEXT, from_site TEXT, to_site TEXT, status TEXT,
            created_at TEXT, updated_at TEXT, site_id TEXT
        );
        CREATE TABLE IF NOT EXISTS milestones (
            id TEXT PRIMARY KEY, container_id TEXT, event_type TEXT,
            description TEXT, recorded_by TEXT, site_id TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, container_id TEXT, incident_type TEXT,
            description TEXT, site_id TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS processed_operations (
            operation_id TEXT PRIMARY KEY, site_id TEXT, processed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_log (
            id TEXT PRIMARY KEY, site_id TEXT, operations_count INTEGER,
            conflicts_detected INTEGER DEFAULT 0, received_at TEXT
        );
    """)
    conn.commit()
    conn.close()

def now(): return datetime.utcnow().isoformat()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="BorderFlow Control Tower", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class SyncOperation(BaseModel):
    id: str; operation_id: str; table_name: str; record_id: str
    action: str; payload: str; synced: int; created_at: str

class SyncBatch(BaseModel):
    site_id: str; operations: List[SyncOperation]

def resolve_conflict(conn, table, record_id, incoming):
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    if not row: return "INSERT"
    existing = dict(row)
    ev = existing.get("version", 0) or 0
    iv = incoming.get("version", 0) or 0
    if iv > ev: return "UPDATE"
    if iv == ev and incoming.get("updated_at","") > existing.get("updated_at",""): return "UPDATE"
    return "SKIP"

@app.post("/sync/receive")
def receive_sync(batch: SyncBatch):
    conn = get_db(); accepted = skipped = conflicts = 0
    for op in batch.operations:
        if conn.execute("SELECT 1 FROM processed_operations WHERE operation_id=?", (op.operation_id,)).fetchone():
            skipped += 1; continue
        p = json.loads(op.payload); t = op.table_name
        try:
            if t == "containers":
                d = resolve_conflict(conn, "containers", op.record_id, p)
                if d == "INSERT":
                    conn.execute("INSERT OR IGNORE INTO containers (id,reference,client_name,origin,destination,status,current_site,seal_number,created_at,updated_at,version,site_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (p.get("id"),p.get("reference"),p.get("client_name"),p.get("origin"),p.get("destination"),p.get("status"),p.get("current_site"),p.get("seal_number"),p.get("created_at"),p.get("updated_at"),p.get("version",1),p.get("site_id")))
                elif d == "UPDATE":
                    conflicts += 1
                    conn.execute("UPDATE containers SET status=?,current_site=?,updated_at=?,version=?,site_id=? WHERE id=?",
                        (p.get("status"),p.get("current_site"),p.get("updated_at"),p.get("version",1),p.get("site_id"),p.get("id")))
            elif t == "trips":
                conn.execute("INSERT OR IGNORE INTO trips (id,container_id,driver_name,vehicle_reg,from_site,to_site,status,created_at,updated_at,site_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (p.get("id"),p.get("container_id"),p.get("driver_name"),p.get("vehicle_reg"),p.get("from_site"),p.get("to_site"),p.get("status"),p.get("created_at"),p.get("updated_at"),p.get("site_id")))
            elif t == "milestones":
                conn.execute("INSERT OR IGNORE INTO milestones (id,container_id,event_type,description,recorded_by,site_id,created_at) VALUES (?,?,?,?,?,?,?)",
                    (p.get("id"),p.get("container_id"),p.get("event_type"),p.get("description"),p.get("recorded_by"),p.get("site_id"),p.get("created_at")))
            elif t == "incidents":
                conn.execute("INSERT OR IGNORE INTO incidents (id,container_id,incident_type,description,site_id,created_at) VALUES (?,?,?,?,?,?)",
                    (p.get("id"),p.get("container_id"),p.get("incident_type"),p.get("description"),p.get("site_id"),p.get("created_at")))
            conn.execute("INSERT OR IGNORE INTO processed_operations (operation_id,site_id,processed_at) VALUES (?,?,?)", (op.operation_id, batch.site_id, now()))
            accepted += 1
        except Exception as e: print(f"Error: {e}")
    conn.execute("INSERT INTO sync_log (id,site_id,operations_count,conflicts_detected,received_at) VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), batch.site_id, accepted, conflicts, now()))
    conn.commit(); conn.close()
    return {"accepted": accepted, "skipped_duplicates": skipped, "conflicts_resolved": conflicts}

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/containers")
def all_containers():
    conn = get_db()
    rows = conn.execute("SELECT * FROM containers ORDER BY updated_at DESC").fetchall()
    conn.close(); return [dict(r) for r in rows]

@app.get("/containers/{container_id}/timeline")
def timeline(container_id: str):
    conn = get_db()
    c = conn.execute("SELECT * FROM containers WHERE id=?", (container_id,)).fetchone()
    if not c: raise HTTPException(404, "Not found")
    m = conn.execute("SELECT * FROM milestones WHERE container_id=? ORDER BY created_at", (container_id,)).fetchall()
    t = conn.execute("SELECT * FROM trips WHERE container_id=? ORDER BY created_at", (container_id,)).fetchall()
    i = conn.execute("SELECT * FROM incidents WHERE container_id=? ORDER BY created_at", (container_id,)).fetchall()
    conn.close()
    return {"container": dict(c), "milestones": [dict(x) for x in m], "trips": [dict(x) for x in t], "incidents": [dict(x) for x in i]}

@app.get("/kpis")
def kpis():
    conn = get_db()
    r = {
        "total_containers": conn.execute("SELECT COUNT(*) FROM containers").fetchone()[0],
        "delivered": conn.execute("SELECT COUNT(*) FROM containers WHERE status='DELIVERED'").fetchone()[0],
        "in_transit": conn.execute("SELECT COUNT(*) FROM containers WHERE status='IN_TRANSIT'").fetchone()[0],
        "total_incidents": conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
        "total_syncs": conn.execute("SELECT COUNT(*) FROM sync_log").fetchone()[0],
        "containers_by_site": [dict(x) for x in conn.execute("SELECT current_site, COUNT(*) as count FROM containers GROUP BY current_site").fetchall()]
    }
    conn.close(); return r

@app.get("/sync/log")
def sync_log():
    conn = get_db()
    rows = conn.execute("SELECT * FROM sync_log ORDER BY received_at DESC LIMIT 50").fetchall()
    conn.close(); return [dict(r) for r in rows]

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "dashboard.html")
    with open(html_path) as f:
        return HTMLResponse(f.read())

@app.get("/")
def root(): return {"service": "BorderFlow Control Tower", "dashboard": "/dashboard", "docs": "/docs"}
