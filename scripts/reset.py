#!/usr/bin/env python3
"""
BorderFlow - Reset Script
Clears ALL data from all sites and control tower.
Run this to start completely fresh.
"""
import os, glob

print("Resetting BorderFlow data...")

# Clear SQLite databases
paths = [
    '/tmp/borderflow_depot/depot.db',
    '/tmp/borderflow_border/border.db',
    '/tmp/borderflow_port/port.db',
    '/tmp/borderflow_hub/hub.db',
    '/tmp/control_tower.db',
]

for p in paths:
    if os.path.exists(p):
        os.remove(p)
        print(f"  ✅ Cleared {p}")
    else:
        print(f"  — Not found: {p}")

print("\nDone! Restart the services: python run_all.py")
print("Your data is now clean.")
