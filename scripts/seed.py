#!/usr/bin/env python3
"""
BorderFlow Seed Data Script
Runs the complete consignment workflow:
  Depot → Border → Port → Hub (with offline simulation)

Usage: python seed.py
Make sure all services are running first: docker compose up -d
"""

import httpx
import time
import json

SITES = {
    "depot":  "http://localhost:8001",
    "border": "http://localhost:8002",
    "port":   "http://localhost:8003",
    "hub":    "http://localhost:8004",
}
CONTROL_TOWER = "http://localhost:8000"

def wait_for_services():
    print("⏳ Waiting for services to be ready...")
    for name, url in {**SITES, "control-tower": CONTROL_TOWER}.items():
        for attempt in range(20):
            try:
                r = httpx.get(f"{url}/health", timeout=3)
                if r.status_code == 200:
                    print(f"  ✅ {name} is ready")
                    break
            except:
                pass
            time.sleep(2)
        else:
            print(f"  ❌ {name} not responding — is it running?")

def post(url, path, data):
    r = httpx.post(f"{url}{path}", json=data, timeout=10)
    r.raise_for_status()
    return r.json()

def get(url, path):
    r = httpx.get(f"{url}{path}", timeout=10)
    r.raise_for_status()
    return r.json()

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

def main():
    wait_for_services()

    # ── STEP 1: Register containers at Depot ─────────────────────────────────
    section("STEP 1: Depot registers containers")

    containers = []
    consignments = [
        {"reference": "BF-2026-001", "client_name": "Lesotho Milling Co.", "origin": "Maseru", "destination": "Johannesburg", "seal_number": "SEAL-7721"},
        {"reference": "BF-2026-002", "client_name": "Vodacom Lesotho", "origin": "Maseru", "destination": "Durban Port", "seal_number": "SEAL-7722"},
        {"reference": "BF-2026-003", "client_name": "Standard Lesotho Bank", "origin": "Maseru", "destination": "Cape Town", "seal_number": "SEAL-7723"},
    ]

    for c in consignments:
        resp = post(SITES["depot"], "/containers", c)
        cid = resp["container"]["id"]
        containers.append(cid)
        print(f"  📦 Registered {c['reference']} → ID: {cid[:8]}...")

    # ── STEP 2: Assign trips at Depot ────────────────────────────────────────
    section("STEP 2: Depot assigns trips to drivers")

    drivers = [
        {"driver_name": "Thabo Mokoena", "vehicle_reg": "LSO-1234-AA", "to_site": "border"},
        {"driver_name": "Palesa Ntsane",  "vehicle_reg": "LSO-5678-BB", "to_site": "border"},
        {"driver_name": "Lerato Sello",   "vehicle_reg": "LSO-9012-CC", "to_site": "border"},
    ]

    for cid, driver in zip(containers, drivers):
        resp = post(SITES["depot"], "/trips", {"container_id": cid, **driver})
        print(f"  🚛 Assigned {driver['driver_name']} to container {cid[:8]}...")

    # ── STEP 3: Update status — containers dispatched ────────────────────────
    section("STEP 3: Containers dispatched from Depot")

    for cid in containers:
        post(SITES["depot"], "/containers/status", {
            "container_id": cid, "new_status": "IN_TRANSIT", "recorded_by": "depot_clerk"
        })
    print(f"  ✅ All containers marked IN_TRANSIT")

    # ── STEP 4: Simulate Border offline — records locally ────────────────────
    section("STEP 4: Simulating OFFLINE scenario at Border Post")
    print("  📡 Border going offline (network partition)...")

    # Create containers at border (simulating offline sync — they record locally)
    border_containers = []
    for ref_suffix, cid in zip(["001", "002"], containers[:2]):
        resp = post(SITES["border"], "/containers", {
            "reference": f"BF-BORDER-{ref_suffix}",
            "client_name": f"Offline Client {ref_suffix}",
            "origin": "Maseru Bridge",
            "destination": "Johannesburg",
            "seal_number": f"SEAL-OFFLN-{ref_suffix}"
        })
        bcid = resp["container"]["id"]
        border_containers.append(bcid)
        # Add clearance milestone (offline)
        post(SITES["border"], "/milestones", {
            "container_id": bcid,
            "event_type": "CUSTOMS_CLEARED",
            "description": "Customs clearance completed offline",
            "recorded_by": "border_agent_1"
        })
        post(SITES["border"], "/containers/status", {
            "container_id": bcid, "new_status": "CLEARED", "recorded_by": "border_agent_1"
        })

    print(f"  📝 Border recorded {len(border_containers)} containers offline (not yet synced)")

    # Check sync queue
    sync_status = get(SITES["border"], "/sync/status")
    print(f"  ⏳ Border sync queue: {sync_status['pending_sync']} operations pending")

    # Wait for sync to happen
    print(f"\n  ⏸  Waiting 15s for auto-sync to fire...")
    time.sleep(15)

    sync_status = get(SITES["border"], "/sync/status")
    print(f"  ✅ Border sync queue after wait: {sync_status['pending_sync']} pending")

    # ── STEP 5: Port records vessel loading ──────────────────────────────────
    section("STEP 5: Port records vessel loading milestones")

    resp = post(SITES["port"], "/containers", {
        "reference": "BF-2026-PORT-01",
        "client_name": "Vodacom Lesotho",
        "origin": "Durban Port",
        "destination": "Cape Town",
        "seal_number": "SEAL-PORT-001"
    })
    port_cid = resp["container"]["id"]

    milestones = [
        ("VESSEL_ARRIVED", "MV Cape Pioneer arrived at berth 7"),
        ("LOADING_STARTED", "Container loading commenced"),
        ("LOADING_COMPLETE", "All containers loaded, vessel cleared for departure"),
    ]
    for event, desc in milestones:
        post(SITES["port"], "/milestones", {
            "container_id": port_cid, "event_type": event,
            "description": desc, "recorded_by": "port_agent"
        })
        print(f"  ⚓ Milestone: {event}")

    # ── STEP 6: Report an incident ────────────────────────────────────────────
    section("STEP 6: Border reports an incident")

    post(SITES["border"], "/incidents", {
        "container_id": border_containers[0],
        "incident_type": "DOCUMENT_PROBLEM",
        "description": "Missing customs declaration form — driver sent back to depot"
    })
    print("  🚨 Incident reported: DOCUMENT_PROBLEM")

    # ── STEP 7: Hub confirms delivery ─────────────────────────────────────────
    section("STEP 7: Destination Hub confirms delivery")

    resp = post(SITES["hub"], "/containers", {
        "reference": "BF-2026-HUB-FINAL",
        "client_name": "Lesotho Milling Co.",
        "origin": "Maseru",
        "destination": "Johannesburg",
        "seal_number": "SEAL-FINAL"
    })
    hub_cid = resp["container"]["id"]
    post(SITES["hub"], "/containers/status", {
        "container_id": hub_cid, "new_status": "DELIVERED", "recorded_by": "hub_clerk"
    })
    post(SITES["hub"], "/milestones", {
        "container_id": hub_cid, "event_type": "DELIVERY_CONFIRMED",
        "description": "Consignment received in good condition, seal intact",
        "recorded_by": "hub_clerk"
    })
    print(f"  ✅ Container {hub_cid[:8]} marked DELIVERED")

    # ── Final wait + summary ──────────────────────────────────────────────────
    print(f"\n  ⏸  Waiting 15s for all sites to sync to Control Tower...")
    time.sleep(15)

    section("✅ SEED COMPLETE — Control Tower Summary")
    try:
        kpis = get(CONTROL_TOWER, "/kpis")
        print(f"  📊 Total containers in global view : {kpis['total_containers']}")
        print(f"  🚛 In transit                      : {kpis['in_transit']}")
        print(f"  ✅ Delivered                       : {kpis['delivered']}")
        print(f"  🚨 Incidents                       : {kpis['total_incidents']}")
        print(f"  🔄 Sync events logged              : {kpis['total_syncs']}")
        print(f"\n  🌐 Dashboard → http://localhost:8000/dashboard")
        print(f"  📖 API docs  → http://localhost:8000/docs")
    except Exception as e:
        print(f"  ⚠️  Could not fetch KPIs: {e}")

if __name__ == "__main__":
    main()
