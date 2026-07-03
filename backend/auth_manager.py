from py5paisa import FivePaisaClient
import json
import os
from datetime import datetime
import pyotp
from credentials import CRED, CLIENT_CODE, PIN, TOTP_SECRET

TOKEN_FILE = "token_data.json"

def save_token(access_token, client_code):
    """Token ko file mein save karo"""
    data = {
        "access_token": access_token,
        "client_code": client_code,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(TOKEN_FILE, 'w') as f:
        json.dump(data, f)
    print(f"✓ Token saved at {data['timestamp']}")

def load_token():
    """Saved token load karo"""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'r') as f:
            data = json.load(f)
        return data.get("access_token"), data.get("client_code")
    return None, None

def login_and_save():
    """TOTP se login karo aur token save karo"""
    client = FivePaisaClient(cred=CRED)
    
    # TOTP generate karo
    totp = pyotp.TOTP(TOTP_SECRET)
    current_totp = totp.now()
    
    print("Logging in...")
    client.get_totp_session(CLIENT_CODE, current_totp, PIN)
    
    # Token fetch karo
    access_token = client.get_access_token()
    
    if access_token:
        save_token(access_token, CLIENT_CODE)
        print("✓ Login successful!")
        return client
    else:
        print("✗ Login failed")
        return None

def get_client():
    """Saved token se client banao ya naya login karo"""
    access_token, client_code = load_token()
    
    if access_token and client_code:
        print("Using saved token...")
        client = FivePaisaClient(cred=CRED)
        client.set_access_token(access_token, client_code)
        return client
    else:
        print("No saved token found. Logging in...")
        return login_and_save()

if __name__ == "__main__":
    
    client = login_and_save()
    if client:
        # Test karo
        req_list = [{"Exch": "N", "ExchType": "C", "ScripData": "RELIANCE_EQ"}]
        print(client.fetch_market_feed_scrip(req_list))
