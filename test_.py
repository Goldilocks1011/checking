import sys, os

# --- Fix the path exactly like your daily_startup.py ---
project_root = os.path.dirname(os.path.abspath(__file__))
backend_folder = os.path.join(project_root, "backend")

if backend_folder not in sys.path:
    sys.path.insert(0, backend_folder)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# --- Now the imports will work ---
from backend.services.symbol_resolver import get_canonical

symbols = [
    "Bajaj Auto", "Bank of Baroda", "Canara Bank", "Engineers India",
    "Global Health", "Hero MotoCorp", "Housing & Urban Dev.", "ICICI Bank",
    "Infosys", "Jio Financial Serv.", "Kotak Mahindra Bank", "Maha. Scooters",
    "Nexus REIT", "Noida Toll Bridge", "Tata Consult. Serv.", "Tata Motors",
    "Wonderla Holidays"
]

print("\n=== Testing resolution after fix ===\n")
for s in symbols:
    can = get_canonical(s)
    print(f"{s:30s} → {can}")