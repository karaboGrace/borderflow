# BorderFlow — CS4430 Distributed Database Systems

Cross-border container logistics tracking system with offline-first distributed design.

## Team: DDB4by4-2026 | Coordinator: Mr. Khobatha Setetemela

---

## Architecture

```
┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐  ┌──────────────────┐
│  Origin Depot   │  │   Border Post    │  │   Port Site    │  │ Destination Hub  │
│  (Maseru)       │  │ (Maseru Bridge)  │  │   (Durban)     │  │ (Johannesburg)   │
│  :8001          │  │   :8002          │  │   :8003        │  │   :8004          │
│  SQLite (local) │  │  SQLite (local)  │  │ SQLite (local) │  │  SQLite (local)  │
└────────┬────────┘  └────────┬─────────┘  └───────┬────────┘  └────────┬─────────┘
         │                    │                     │                    │
         └──────── push sync (when online) ─────────┴────────────────────┘
                                     │
                          ┌──────────▼───────────┐
                          │   Control Tower       │
                          │   :8000               │
                          │   PostgreSQL (global) │
                          │   Conflict Resolution │
                          │   Dashboard UI        │
                          └───────────────────────┘
```

**Key design decisions:**
- Multi-master replication (all sites accept writes independently) — AP from CAP theorem
- SQLite at edge sites (zero-config, survives restarts)
- PostgreSQL at Control Tower (ACID, conflict resolution)
- Push-based sync (sites push when connectivity restored)
- Last Write Wins conflict resolution (version + timestamp)
- Idempotency keys prevent duplicate processing

---

## Prerequisites

- Docker Desktop installed and running
- For Kubernetes: enable Kubernetes in Docker Desktop settings

---

## Option A: Run with Docker Compose (Quick Start)

### Step 1 — Build and start everything

```bash
cd borderflow
docker compose up --build
```

Wait until you see all services say "Application startup complete".

### Step 2 — Load seed data

Open a new terminal:
```bash
pip install httpx
python scripts/seed.py
```

### Step 3 — Open the dashboard

http://localhost:8000/dashboard

### API endpoints (for testing):
| Service       | URL                    |
|---------------|------------------------|
| Control Tower | http://localhost:8000  |
| Depot         | http://localhost:8001  |
| Border Post   | http://localhost:8002  |
| Port          | http://localhost:8003  |
| Hub           | http://localhost:8004  |

Each site has interactive API docs at `/docs` (e.g. http://localhost:8001/docs)

---

## Option B: Deploy on Kubernetes

### Step 1 — Enable Kubernetes in Docker Desktop

Docker Desktop → Settings → Kubernetes → Enable Kubernetes → Apply

### Step 2 — Build images into Minikube/local registry

```bash
cd borderflow

# Build the site service image
docker build -t borderflow-site:latest ./site_service

# Build the control tower image
docker build -t borderflow-control-tower:latest ./control_tower
```

### Step 3 — Deploy to Kubernetes

```bash
kubectl apply -f k8s/postgres.yaml
# Wait for postgres to be ready
kubectl wait --for=condition=ready pod -l app=postgres --timeout=60s

kubectl apply -f k8s/control-tower.yaml
kubectl apply -f k8s/sites.yaml
```

### Step 4 — Check everything is running

```bash
kubectl get pods
kubectl get services
```

All pods should show STATUS=Running.

### Step 5 — Load seed data

```bash
python scripts/seed.py
```

### Access (NodePort):
| Service       | URL                       |
|---------------|---------------------------|
| Control Tower | http://localhost:30000    |
| Depot         | http://localhost:30001    |
| Border Post   | http://localhost:30002    |
| Port          | http://localhost:30003    |
| Hub           | http://localhost:30004    |

---

## Demo: Failure + Recovery (Required for submission)

### Simulate network partition (border goes offline)

**Docker Compose:**
```bash
# Disconnect border from network
docker network disconnect borderflow_default borderflow-border-1

# Border now works offline — make some updates
curl -X POST http://localhost:8002/containers \
  -H "Content-Type: application/json" \
  -d '{"reference":"OFFLINE-TEST","client_name":"Test","origin":"Border","destination":"JHB","seal_number":"X123"}'

# Check sync queue shows pending items
curl http://localhost:8002/sync/status

# Restore connectivity
docker network connect borderflow_default borderflow-border-1

# Wait ~15 seconds, check sync clears
curl http://localhost:8002/sync/status

# Verify it appeared in Control Tower
curl http://localhost:8000/containers
```

**Kubernetes:**
```bash
# Apply network partition policy (blocks border → control-tower)
kubectl apply -f k8s/network-partition.yaml

# Make updates at border while offline
kubectl exec deployment/border -- curl -X POST http://localhost:8000/containers \
  -H "Content-Type: application/json" \
  -d '{"reference":"K8S-OFFLINE","client_name":"Test","origin":"Border","destination":"JHB"}'

# Restore connectivity
kubectl delete -f k8s/network-partition.yaml

# Watch sync happen in logs
kubectl logs deployment/border --follow
```

---

## Testing Conflict Resolution

```bash
# Same container updated at depot AND border while offline
# Then both sync — Control Tower applies Last Write Wins

# 1. Create container at depot
curl -X POST http://localhost:8001/containers \
  -H "Content-Type: application/json" \
  -d '{"reference":"CONFLICT-TEST","client_name":"Test","origin":"Maseru","destination":"JHB"}'

# Note the container ID from the response, then:
# 2. Update status at depot
curl -X POST http://localhost:8001/containers/status \
  -H "Content-Type: application/json" \
  -d '{"container_id":"<ID>","new_status":"IN_TRANSIT","recorded_by":"depot"}'

# 3. Disconnect border, update there too, reconnect
# Control Tower will resolve with higher version/timestamp winning
```

---

## Project Structure

```
borderflow/
├── site_service/          # Shared service for all 4 edge sites
│   ├── main.py            # FastAPI app with SQLite + sync queue
│   ├── requirements.txt
│   └── Dockerfile
├── control_tower/         # Central sync hub + dashboard
│   ├── main.py            # FastAPI + PostgreSQL + conflict resolution
│   ├── requirements.txt
│   ├── Dockerfile
│   └── templates/
│       └── dashboard.html # Real-time dashboard UI
├── k8s/                   # Kubernetes manifests
│   ├── postgres.yaml
│   ├── control-tower.yaml
│   ├── sites.yaml
│   └── network-partition.yaml  # For failure demo
├── scripts/
│   └── seed.py            # Demo data + workflow script
├── docker-compose.yml
└── README.md
```
