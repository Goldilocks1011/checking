# debug_resolve.py
import sys
import os
sys.path.insert(0, os.getcwd())  # adjust if needed
from backend.services.symbol_resolver import get_canonical, debug_resolve
from backend.services.scrip_master_db import _normalize_broker_name

print("\n=== Testing 5paisa symbols ===")
sym_list = [
    "MAHA. SCOOTERS",
    "MAHA.SCOOTERS",   # <-- this is likely the cause of failure
    "BAJAJ HLDG. & INV.",
    "BAJAJHLDG&INV",   # <-- 5paisa sometimes removes spaces entirely
    "PUNJ. NATIONLBAK",
    "PUNJ.NATIONLBAK",
]

for sym in sym_list:
    print(f"\n--- RESOLVING: {sym} ---")
    # 1. Show what the normalizer does:
    normalized = _normalize_broker_name(sym)
    print(f"   Step 0 - Normalized: '{normalized}'")
    
    # 2. Run the full resolver
    canonical = get_canonical(sym)
    print(f"   Final canonical: '{canonical}'")