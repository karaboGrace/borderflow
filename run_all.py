#!/usr/bin/env python3
"""
BorderFlow - Start all services without Docker
Run this from the borderflow/ folder:
    python3 run_all.py
"""
import subprocess, sys, os, time, signal

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

services = [
    {
        "name": "Control Tower",
        "cmd": [PYTHON, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
        "cwd": os.path.join(BASE, "control_tower"),
        "env": {}
    },
    {
        "name": "Depot (Maseru)",
        "cmd": [PYTHON, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"],
        "cwd": os.path.join(BASE, "site_service"),
        "env": {"SITE_ID": "depot", "SITE_NAME": "Origin Depot (Maseru)", "CONTROL_TOWER_URL": "http://localhost:8000"}
    },
    {
        "name": "Border Post",
        "cmd": [PYTHON, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"],
        "cwd": os.path.join(BASE, "site_service"),
        "env": {"SITE_ID": "border", "SITE_NAME": "Border Post (Maseru Bridge)", "CONTROL_TOWER_URL": "http://localhost:8000"}
    },
    {
        "name": "Port (Durban)",
        "cmd": [PYTHON, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"],
        "cwd": os.path.join(BASE, "site_service"),
        "env": {"SITE_ID": "port", "SITE_NAME": "Port Site (Durban)", "CONTROL_TOWER_URL": "http://localhost:8000"}
    },
    {
        "name": "Hub (Johannesburg)",
        "cmd": [PYTHON, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004"],
        "cwd": os.path.join(BASE, "site_service"),
        "env": {"SITE_ID": "hub", "SITE_NAME": "Destination Hub (Johannesburg)", "CONTROL_TOWER_URL": "http://localhost:8000"}
    },
]

procs = []

def stop_all(sig=None, frame=None):
    print("\n\nStopping all services...")
    for p in procs:
        p.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, stop_all)

print("=" * 55)
print("  BorderFlow — Starting all services")
print("=" * 55)

for svc in services:
    env = {**os.environ, **svc["env"]}
    # Each site needs its own data directory
    if svc["env"].get("SITE_ID"):
        data_dir = f"/tmp/borderflow_{svc['env']['SITE_ID']}"
        os.makedirs(data_dir, exist_ok=True)
        env["DATA_DIR"] = data_dir

    p = subprocess.Popen(
        svc["cmd"],
        cwd=svc["cwd"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    procs.append(p)
    print(f"  ✅ Started {svc['name']} (PID {p.pid})")
    time.sleep(1.5)  # stagger startup

print()
print("=" * 55)
print("  All services running!")
print()
print("  Dashboard  →  http://localhost:8000/dashboard")
print("  Depot API  →  http://localhost:8001/docs")
print("  Border API →  http://localhost:8002/docs")
print("  Port API   →  http://localhost:8003/docs")
print("  Hub API    →  http://localhost:8004/docs")
print()
print("  Press Ctrl+C to stop all services")
print("=" * 55)

# Stream logs from all processes
import threading

def stream(proc, name):
    for line in proc.stdout:
        text = line.decode("utf-8", errors="replace").strip()
        if text and "INFO" not in text:
            print(f"  [{name}] {text}")

for p, svc in zip(procs, services):
    t = threading.Thread(target=stream, args=(p, svc["name"]), daemon=True)
    t.start()

# Wait for all
for p in procs:
    p.wait()
