# test_client.py (in v7/)
import sys, os
sys.path.insert(0, os.path.abspath("backend"))
from backend.auth_manager import get_client

client = get_client()
print("Client type:", type(client))
print("Has historical_data?", hasattr(client, "historical_data"))