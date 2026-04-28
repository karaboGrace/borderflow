"""
BorderFlow - Site Service
Runs as: Depot, Border Post, Port, or Destination Hub
Each site has its own SQLite DB and sync queue.
"""

import os
import uuid
import sqlite3
import httpx
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

SITE_ID = os.environ.get("SITE_ID", "depot")
SITE_NAME = os.environ.get("SITE_NAME", "Origin Depot")
CONTROL_TOWER_URL = os.environ.get("CONTROL_TOWER_URL", "http://control-tower:8000")
DATA_DIR = os.environ.get("DATA_DIR", f"/tmp/borderflow_{SITE_ID}")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, f"{SITE_ID}.db")

# ─── Database Setup ────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS containers (
            id TEXT PRIMARY KEY,
            reference TEXT UNIQUE NOT NULL,
            client_name TEXT NOT NULL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'REGISTERED',
            current_site TEXT,
            seal_number TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            site_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trips (
            id TEXT PRIMARY KEY,
            container_id TEXT NOT NULL,
            driver_name TEXT NOT NULL,
            vehicle_reg TEXT NOT NULL,
            from_site TEXT NOT NULL,
            to_site TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ASSIGNED',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            site_id TEXT NOT NULL,
            FOREIGN KEY (container_id) REFERENCES containers(id)
        );

        CREATE TABLE IF NOT EXISTS milestones (
            id TEXT PRIMARY KEY,
            container_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            description TEXT,
            recorded_by TEXT NOT NULL,
            site_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            container_id TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            description TEXT NOT NULL,
            site_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_queue (
            id TEXT PRIMARY KEY,
            operation_id TEXT UNIQUE NOT NULL,
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
            action TEXT NOT NULL,
            payload TEXT NOT NULL,
            synced INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS processed_ops (
            operation_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

def queue_sync(table_name: str, record_id: str, action: str, payload: dict):
    """Add a change to the sync queue to be pushed to Control Tower."""
    import json
    conn = get_db()
    op_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO sync_queue (id, operation_id, table_name, record_id, action, payload, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), op_id, table_name, record_id, action, json.dumps(payload), now()))
    conn.commit()
    conn.close()
    return op_id

def now():
    return datetime.utcnow().isoformat()

# ─── Background Sync Task ──────────────────────────────────────────────────────

async def sync_worker():
    """Periodically push pending changes to Control Tower."""
    import json
    while True:
        await asyncio.sleep(10)
        if not _online:
            print(f"[{SITE_ID}] OFFLINE - sync paused")
            continue
        try:
            conn = get_db()
            pending = conn.execute(
                "SELECT * FROM sync_queue WHERE synced = 0 ORDER BY created_at LIMIT 50"
            ).fetchall()
            conn.close()

            if not pending:
                continue

            batch = [dict(row) for row in pending]
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{CONTROL_TOWER_URL}/sync/receive",
                    json={"site_id": SITE_ID, "operations": batch}
                )
                if resp.status_code == 200:
                    conn = get_db()
                    for op in batch:
                        conn.execute(
                            "UPDATE sync_queue SET synced = 1 WHERE operation_id = ?",
                            (op["operation_id"],)
                        )
                    conn.commit()
                    conn.close()
                    print(f"[{SITE_ID}] Synced {len(batch)} operations")
        except Exception as e:
            print(f"[{SITE_ID}] Sync failed (offline?): {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(sync_worker())
    yield

# ─── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title=f"BorderFlow - {SITE_NAME}", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Models ────────────────────────────────────────────────────────────────────

class ContainerCreate(BaseModel):
    reference: str
    client_name: str
    origin: str
    destination: str
    seal_number: Optional[str] = None

class TripCreate(BaseModel):
    container_id: str
    driver_name: str
    vehicle_reg: str
    to_site: str

class MilestoneCreate(BaseModel):
    container_id: str
    event_type: str
    description: Optional[str] = None
    recorded_by: str

class StatusUpdate(BaseModel):
    container_id: str
    new_status: str
    recorded_by: str

class IncidentCreate(BaseModel):
    container_id: str
    incident_type: str
    description: str

# ─── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def info():
    return {"site_id": SITE_ID, "site_name": SITE_NAME, "status": "online"}

@app.get("/health")
def health():
    return {"status": "ok", "site": SITE_ID}

# Containers
@app.post("/containers")
def create_container(data: ContainerCreate):
    conn = get_db()
    cid = str(uuid.uuid4())
    ts = now()
    payload = {
        "id": cid, "reference": data.reference, "client_name": data.client_name,
        "origin": data.origin, "destination": data.destination, "status": "REGISTERED",
        "current_site": SITE_ID, "seal_number": data.seal_number,
        "created_at": ts, "updated_at": ts, "version": 1, "site_id": SITE_ID
    }
    try:
        conn.execute("""
            INSERT INTO containers (id,reference,client_name,origin,destination,status,
            current_site,seal_number,created_at,updated_at,version,site_id)
            VALUES (:id,:reference,:client_name,:origin,:destination,:status,
            :current_site,:seal_number,:created_at,:updated_at,:version,:site_id)
        """, payload)
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Container reference already exists")
    finally:
        conn.close()
    queue_sync("containers", cid, "INSERT", payload)
    return {"message": "Container registered", "container": payload}

@app.get("/containers")
def list_containers():
    conn = get_db()
    rows = conn.execute("SELECT * FROM containers ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/containers/{container_id}")
def get_container(container_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM containers WHERE id = ?", (container_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Container not found")
    return dict(row)

@app.post("/containers/status")
def update_status(data: StatusUpdate):
    conn = get_db()
    row = conn.execute("SELECT * FROM containers WHERE id = ?", (data.container_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Container not found")
    ts = now()
    new_version = row["version"] + 1
    conn.execute("""
        UPDATE containers SET status=?, updated_at=?, version=?, current_site=?
        WHERE id=?
    """, (data.new_status, ts, new_version, SITE_ID, data.container_id))
    conn.commit()
    conn.close()
    payload = {"id": data.container_id, "status": data.new_status,
               "updated_at": ts, "version": new_version, "site_id": SITE_ID}
    queue_sync("containers", data.container_id, "UPDATE", payload)
    # Auto-add milestone
    _add_milestone(data.container_id, f"STATUS_{data.new_status}", data.new_status, data.recorded_by)
    return {"message": "Status updated", "new_status": data.new_status}

# Trips
@app.post("/trips")
def create_trip(data: TripCreate):
    conn = get_db()
    # Check container exists
    row = conn.execute("SELECT * FROM containers WHERE id = ?", (data.container_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Container not found")
    # Check no active trip
    active = conn.execute(
        "SELECT id FROM trips WHERE container_id=? AND status='ASSIGNED'", (data.container_id,)
    ).fetchone()
    if active:
        conn.close()
        raise HTTPException(400, "Container already has an active trip assignment")
    tid = str(uuid.uuid4())
    ts = now()
    payload = {
        "id": tid, "container_id": data.container_id, "driver_name": data.driver_name,
        "vehicle_reg": data.vehicle_reg, "from_site": SITE_ID, "to_site": data.to_site,
        "status": "ASSIGNED", "created_at": ts, "updated_at": ts, "site_id": SITE_ID
    }
    conn.execute("""
        INSERT INTO trips (id,container_id,driver_name,vehicle_reg,from_site,to_site,
        status,created_at,updated_at,site_id)
        VALUES (:id,:container_id,:driver_name,:vehicle_reg,:from_site,:to_site,
        :status,:created_at,:updated_at,:site_id)
    """, payload)
    conn.commit()
    conn.close()
    queue_sync("trips", tid, "INSERT", payload)
    return {"message": "Trip assigned", "trip": payload}

@app.get("/trips")
def list_trips():
    conn = get_db()
    rows = conn.execute("SELECT * FROM trips ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Milestones
def _add_milestone(container_id, event_type, description, recorded_by):
    conn = get_db()
    mid = str(uuid.uuid4())
    ts = now()
    payload = {
        "id": mid, "container_id": container_id, "event_type": event_type,
        "description": description, "recorded_by": recorded_by,
        "site_id": SITE_ID, "created_at": ts
    }
    conn.execute("""
        INSERT INTO milestones (id,container_id,event_type,description,recorded_by,site_id,created_at)
        VALUES (:id,:container_id,:event_type,:description,:recorded_by,:site_id,:created_at)
    """, payload)
    conn.commit()
    conn.close()
    queue_sync("milestones", mid, "INSERT", payload)
    return payload

@app.post("/milestones")
def add_milestone(data: MilestoneCreate):
    m = _add_milestone(data.container_id, data.event_type, data.description, data.recorded_by)
    return {"message": "Milestone recorded", "milestone": m}

@app.get("/milestones/{container_id}")
def get_milestones(container_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM milestones WHERE container_id=? ORDER BY created_at", (container_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Incidents
@app.post("/incidents")
def report_incident(data: IncidentCreate):
    conn = get_db()
    iid = str(uuid.uuid4())
    ts = now()
    payload = {
        "id": iid, "container_id": data.container_id, "incident_type": data.incident_type,
        "description": data.description, "site_id": SITE_ID, "created_at": ts
    }
    conn.execute("""
        INSERT INTO incidents (id,container_id,incident_type,description,site_id,created_at)
        VALUES (:id,:container_id,:incident_type,:description,:site_id,:created_at)
    """, payload)
    conn.commit()
    conn.close()
    queue_sync("incidents", iid, "INSERT", payload)
    return {"message": "Incident reported", "incident": payload}

# Sync queue status
@app.get("/sync/status")
def sync_status():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM sync_queue WHERE synced=0").fetchone()[0]
    conn.close()
    return {"site_id": SITE_ID, "total_queued": total, "pending_sync": pending}

# Simulate going offline/online (for demo)
_online = True

@app.post("/demo/go-offline")
def go_offline():
    global _online
    _online = False
    return {"message": f"{SITE_NAME} is now OFFLINE (sync paused)"}

@app.post("/demo/go-online")
def go_online():
    global _online
    _online = True
    return {"message": f"{SITE_NAME} is now ONLINE (sync will resume)"}
