import requests

API_BASE = "http://localhost:8001/api/v1"
TOKEN = None

def _request(method, url, **kwargs):
    headers = {}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return requests.request(method, url, headers=headers, **kwargs)

def _post(endpoint, files=None, data=None):
    url = f"{API_BASE}{endpoint}"
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    resp = requests.post(url, files=files, data=data, headers=headers)
    resp.raise_for_status()
    return resp.json()


def create_user(username: str, broker: str) -> dict:
    resp = _request("POST",f"{API_BASE}/users/", json={"username": username, "broker": broker})
    resp.raise_for_status()
    return resp.json()

def list_users() -> list[dict]:
    resp = _request("GET",f"{API_BASE}/users/")
    resp.raise_for_status()
    return resp.json()

def upload_equity(file, user_id, broker, file_type="EQ"):
    files = {"file": file}
    data = {"user_id": user_id, "broker": broker, "file_type": file_type}
    return _post("/upload/equity", files=files, data=data)

def get_holdings(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/holdings/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_transactions(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/transactions/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_pnl(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/pnl/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_intraday(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/intraday/{user_id}")
    resp.raise_for_status()
    return resp.json()

def upload_fno(file, user_id, broker, file_type="FNO"):
    files = {"file": file}
    data = {"user_id": user_id, "broker": broker, "file_type": file_type}
    return _post("/upload/fno", files=files, data=data)

def get_fno_positions(user_id: int, show_expired: bool = False) -> list[dict]:
    resp = _request(
        "GET",
        f"{API_BASE}/fno/positions/{user_id}",
        params={"show_expired": str(show_expired).lower()}
    )
    resp.raise_for_status()
    return resp.json()
 

def get_fno_pnl(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/fno/pnl/{user_id}")
    resp.raise_for_status()
    return resp.json()

def auto_populate_master(user_id: int) -> dict:
    resp = _request("POST",f"{API_BASE}/stock-master/auto-populate/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_stock_master_grid(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/stock-master/grid/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_unmatched_symbols(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/stock-master/unmatched/{user_id}")
    resp.raise_for_status()
    return resp.json()

def link_symbol(user_id: int, raw_symbol: str, broker: str, isin: str) -> dict:
    resp = _request("POST",
        f"{API_BASE}/stock-master/link",
        params={"user_id": user_id, "raw_symbol": raw_symbol, "broker": broker, "isin": isin}
    )
    resp.raise_for_status()
    return resp.json()

def get_user_stats(user_id: int) -> dict:
    resp = _request("GET",f"{API_BASE}/stats/{user_id}")
    resp.raise_for_status()
    return resp.json()

def run_harvest(user_id: int, start: str, end: str):
    resp = _request("POST",f"{API_BASE}/tax-harvest/{user_id}?start={start}&end={end}")
    resp.raise_for_status()
    return resp.json()

def upload_ledger(file, user_id: int, broker: str = 'auto') -> dict:
    """
    Upload a ledger file.
    broker: '5paisa' | 'IIFL' | 'Zerodha' | 'auto' (auto-detect from filename)
    """
    resp = _request(
        'POST',
        f'{API_BASE}/ledger/upload',
        files={'file': (file.name, file.getvalue())},
        data={'user_id': user_id, 'broker': broker},
    )
    resp.raise_for_status()
    return resp.json()
 
 
def get_ledger(user_id: int) -> list[dict]:
    resp = _request('GET', f'{API_BASE}/ledger/{user_id}')
    resp.raise_for_status()
    return resp.json()
 
 
def get_ledger_periods(user_id: int) -> list[dict]:
    """Return per-upload period summaries (opening/closing balance, date range, etc.)."""
    resp = _request('GET', f'{API_BASE}/ledger/periods/{user_id}')
    resp.raise_for_status()
    return resp.json()



def seed_corp_actions(user_id: int) -> dict:
    resp = _request("POST",f"{API_BASE}/corp-actions/seed/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_corp_actions(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/corp-actions/{user_id}")
    resp.raise_for_status()
    return resp.json()

def add_manual_corp_action(user_id: int, data: dict) -> dict:
    resp = _request("POST",f"{API_BASE}/corp-actions/manual/{user_id}", json=data)
    resp.raise_for_status()
    return resp.json()

def sync_nse_corp_actions(user_id: int) -> dict:
    resp = _request("POST",f"{API_BASE}/corp-actions/sync-nse/{user_id}")
    resp.raise_for_status()
    return resp.json()

def add_manual_equity(user_id: int, txn: dict) -> dict:
    resp = _request("POST",f"{API_BASE}/manual/equity?user_id={user_id}", json=txn)
    resp.raise_for_status()
    return resp.json()

def add_manual_fno(user_id: int, txn: dict) -> dict:
    resp = _request("POST",f"{API_BASE}/manual/fno?user_id={user_id}", json=txn)
    resp.raise_for_status()
    return resp.json()

def delete_manual_equity(txn_id: int, user_id: int) -> dict:
    resp = _request("DELETE",f"{API_BASE}/manual/equity/{txn_id}?user_id={user_id}")
    resp.raise_for_status()
    return resp.json()

def delete_manual_fno(txn_id: int, user_id: int) -> dict:
    resp = _request("DELETE",f"{API_BASE}/manual/fno/{txn_id}?user_id={user_id}")
    resp.raise_for_status()
    return resp.json()

def create_group(name: str) -> dict:
    resp = _request("POST",f"{API_BASE}/groups/", json={"name": name})
    resp.raise_for_status()
    return resp.json()

def list_groups() -> list[dict]:
    resp = _request("GET",f"{API_BASE}/groups/")
    resp.raise_for_status()
    return resp.json()

def add_group_member(group_id: int, user_id: int, role: str = "") -> dict:
    resp = _request("POST",f"{API_BASE}/groups/{group_id}/members/{user_id}?broker_role={role}")
    resp.raise_for_status()
    return resp.json()

def remove_group_member(group_id: int, user_id: int) -> dict:
    resp = _request("DELETE",f"{API_BASE}/groups/{group_id}/members/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_group_members(group_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/groups/{group_id}/members")
    resp.raise_for_status()
    return resp.json()

def get_group_holdings(group_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/groups/{group_id}/holdings")
    resp.raise_for_status()
    return resp.json()

def delete_user(user_id: int):
    resp = _request("DELETE",f"{API_BASE}/users/{user_id}")
    resp.raise_for_status()
    
# ---------- Group delete (if not already present) ----------
def delete_group(group_id: int):
    resp = _request("DELETE",f"{API_BASE}/groups/{group_id}")
    resp.raise_for_status()
    
def get_merged_transactions(user_ids: list[int]) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/merged/transactions", params={"user_ids": user_ids})
    resp.raise_for_status()
    return resp.json()

def get_merged_pnl(user_ids: list[int]) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/merged/pnl", params={"user_ids": user_ids})
    resp.raise_for_status()
    return resp.json()

def get_merged_intraday(user_ids: list[int]) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/merged/intraday", params={"user_ids": user_ids})
    resp.raise_for_status()
    return resp.json()

def get_merged_fno_positions(user_ids: list[int]) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/merged/fno_positions", params={"user_ids": user_ids})
    resp.raise_for_status()
    return resp.json()

def get_merged_fno_pnl(user_ids: list[int]) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/merged/fno_pnl", params={"user_ids": user_ids})
    resp.raise_for_status()
    return resp.json()

def run_harvest_multi(user_ids: list[int], start: str, end: str):
    params = {
        "start": start,
        "end": end,
    }
    # Pass user_ids as a list – requests will encode it as user_ids=1&user_ids=2&...
    resp = _request("POST",
        f"{API_BASE}/tax-harvest",
        params={**params, "user_ids": user_ids}
    )
    resp.raise_for_status()
    return resp.json()

def fetch_prices(symbols: list[str]) -> dict:
    resp = _request("GET",f"{API_BASE}/prices", params={"symbols": symbols})
    resp.raise_for_status()
    return resp.json()

def rename_stock(isin: str, new_name: str) -> dict:
    resp = requests.put(f"{API_BASE}/stock-master/rename", json={"isin": isin, "new_name": new_name})
    resp.raise_for_status()
    return resp.json()

def get_processed_files(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/upload/history/{user_id}")
    resp.raise_for_status()
    return resp.json()

def download_file_content(user_id: int, filename: str) -> bytes | None:
    resp = _request("GET",f"{API_BASE}/upload/download/{user_id}/{filename}")
    if resp.status_code == 200:
        return resp.content
    return None

def get_fno_transactions(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/fno/transactions/{user_id}")
    resp.raise_for_status()
    return resp.json()

# ---------- Group Stock Master (already present? If not, add) ----------
def get_group_stock_master(group_id):
    resp = _request("GET",f"{API_BASE}/groups/{group_id}/stock-master")
    resp.raise_for_status()
    return resp.json()

def get_fy_holdings(user_id: int, fy_end: str = None) -> list[dict]:
    params = {}
    if fy_end:
        params['fy_end'] = fy_end
    resp = _request("GET",f"{API_BASE}/holdings/fy/{user_id}", params=params)
    resp.raise_for_status()
    return resp.json()

def get_holding_lots(user_id: int) -> list[dict]:
    resp = _request("GET",f"{API_BASE}/holdings/lots/{user_id}")
    resp.raise_for_status()
    return resp.json()


def create_portfolio_user(username: str, broker: str) -> dict:
    resp = _request("POST", f"{API_BASE}/portfolio-users", json={"username": username, "broker": broker})
    resp.raise_for_status()
    return resp.json()

def get_ce_pe_screener_data(user_id: int) -> dict:
    resp = _request("GET", f"{API_BASE}/ce-pe-screener/{user_id}")
    resp.raise_for_status()
    return resp.json()



def download_scrip_master() -> dict:
    """Trigger auto-download of ScripMaster from 5paisa URL into DB."""
    resp = _request("POST", f"{API_BASE}/stock-master/download-scrip-master")
    resp.raise_for_status()
    return resp.json()


def upload_scrip_master(file) -> dict:
    """Upload ScripMaster_all.csv to seed the DB cache."""
    resp = _request(
        "POST",
        f"{API_BASE}/stock-master/upload-scrip-master",
        files={"file": (file.name, file.getvalue(), "text/csv")},
    )
    resp.raise_for_status()
    return resp.json()


def get_scrip_master_stats() -> dict:
    """Get scrip_master_cache table stats."""
    resp = _request("GET", f"{API_BASE}/stock-master/scrip-master-stats")
    resp.raise_for_status()
    return resp.json()


def refresh_fno_from_scrip_master() -> dict:
    """Re-resolve F&O lot sizes for all stocks from the new ScripMaster DB."""
    resp = _request("POST", f"{API_BASE}/stock-master/refresh-fno-from-scrip-master")
    resp.raise_for_status()
    return resp.json()


def get_ce_pe_screener_data(user_id: int) -> dict:
    resp = _request("GET", f"{API_BASE}/ce-pe-screener/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_advanced_screener_data(user_id: int) -> dict:
    """
    Advanced options screener — full 8-step pipeline (Section B).
    Returns {"status": "success"/"error", "rows": int, "data": [...]}.
    Heavy call — allow 30-40 sec.
    """
    resp = _request("GET", f"{API_BASE}/advanced-options-screener/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_group_advanced_screener_data(group_id: int) -> dict:
    """
    Group advanced options screener — full pipeline aggregated across members.
    Returns {"status": "success"/"error", "rows": int, "data": [...]}.
    Heavy call — allow 30-50 sec.
    """
    resp = _request("GET", f"{API_BASE}/advanced-options-screener/group/{group_id}")
    resp.raise_for_status()
    return resp.json()


def get_covered_call_analysis(user_id: int) -> dict:
    """
    Returns covered_calls, uncovered, correction_module tables for tab5.
    """
    resp = _request("GET", f"{API_BASE}/covered-call-analysis/{user_id}")
    resp.raise_for_status()
    return resp.json()


def get_master_reference_positions(requesting_account_id: int) -> dict:
    """
    For child accounts: returns Account 1's positions that are NOT covered calls.
    """
    resp = _request("GET", f"{API_BASE}/master-reference-positions/{requesting_account_id}")
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# Wishlist — Single User
# ─────────────────────────────────────────────────────────────────────────────

def get_wishlist(user_id: int) -> list[dict]:
    """Fetch all wishlist symbols for a user."""
    resp = _request("GET", f"{API_BASE}/wishlist/{user_id}")
    resp.raise_for_status()
    return resp.json()


def add_to_wishlist(
    user_id: int,
    symbol: str,
    canonical_symbol: str = "",
    is_auto_added: bool = False,
    notes: str = "",
) -> dict:
    """Add a symbol to a user's wishlist."""
    resp = _request(
        "POST",
        f"{API_BASE}/wishlist/{user_id}/add",
        json={
            "symbol":           symbol,
            "canonical_symbol": canonical_symbol,
            "is_auto_added":    is_auto_added,
            "notes":            notes,
        },
    )
    resp.raise_for_status()
    return resp.json()


def remove_from_wishlist(user_id: int, symbol: str) -> dict:
    """Remove a symbol from a user's wishlist."""
    resp = _request("DELETE", f"{API_BASE}/wishlist/{user_id}/symbol/{symbol}")
    resp.raise_for_status()
    return resp.json()


def sync_wishlist(user_id: int) -> dict:
    """Auto-populate user wishlist from holdings + open F&O positions."""
    resp = _request("POST", f"{API_BASE}/wishlist/{user_id}/sync")
    resp.raise_for_status()
    return resp.json()


def clear_wishlist_auto(user_id: int) -> dict:
    """Remove auto-synced symbols; keep manually added ones."""
    resp = _request("DELETE", f"{API_BASE}/wishlist/{user_id}/clear-auto")
    resp.raise_for_status()
    return resp.json()


def clear_wishlist_all(user_id: int) -> dict:
    """Remove ALL symbols from a user's wishlist."""
    resp = _request("DELETE", f"{API_BASE}/wishlist/{user_id}/clear-all")
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# Wishlist — Group
# ─────────────────────────────────────────────────────────────────────────────

def get_group_wishlist(group_id: int) -> list[dict]:
    """Fetch all wishlist symbols for a group."""
    resp = _request("GET", f"{API_BASE}/wishlist/group/{group_id}")
    resp.raise_for_status()
    return resp.json()


def add_to_group_wishlist(
    group_id: int,
    symbol: str,
    canonical_symbol: str = "",
    is_auto_added: bool = False,
    notes: str = "",
) -> dict:
    """Add a symbol to a group's wishlist."""
    resp = _request(
        "POST",
        f"{API_BASE}/wishlist/group/{group_id}/add",
        json={
            "symbol":           symbol,
            "canonical_symbol": canonical_symbol,
            "is_auto_added":    is_auto_added,
            "notes":            notes,
        },
    )
    resp.raise_for_status()
    return resp.json()


def remove_from_group_wishlist(group_id: int, symbol: str) -> dict:
    """Remove a symbol from a group's wishlist."""
    resp = _request("DELETE", f"{API_BASE}/wishlist/group/{group_id}/symbol/{symbol}")
    resp.raise_for_status()
    return resp.json()


def sync_group_wishlist(group_id: int) -> dict:
    """Auto-populate group wishlist from all members' holdings + F&O."""
    resp = _request("POST", f"{API_BASE}/wishlist/group/{group_id}/sync")
    resp.raise_for_status()
    return resp.json()


def clear_group_wishlist_auto(group_id: int) -> dict:
    """Remove auto-synced symbols from group wishlist."""
    resp = _request("DELETE", f"{API_BASE}/wishlist/group/{group_id}/clear-auto")
    resp.raise_for_status()
    return resp.json()


def clear_group_wishlist_all(group_id: int) -> dict:
    """Remove ALL symbols from a group's wishlist."""
    resp = _request("DELETE", f"{API_BASE}/wishlist/group/{group_id}/clear-all")
    resp.raise_for_status()
    return resp.json()


# def get_impact(symbol: str, user_id: int = None) -> dict:
#     params = {}
#     if user_id:
#         params["user_id"] = user_id
#     resp = _request("GET", f"{API_BASE}/impact/{symbol}", params=params)
#     resp.raise_for_status()
#     return resp.json()

# def get_suggestion(symbol: str, user_id: int, spot: float = 0) -> dict:
#     resp = _request("GET", f"{API_BASE}/suggest/{symbol}",
#                     params={"user_id": user_id, "spot": spot})
#     resp.raise_for_status()
#     return resp.json()




# ─────────────────────────────────────────────────────────────────────────────
# NSE autocomplete search
# ─────────────────────────────────────────────────────────────────────────────

def nse_search(query: str) -> list[dict]:
    """
    Autocomplete NSE symbols.
    Returns list of {symbol, name, exchange}.
    """
    if not query or not query.strip():
        return []
    try:
        resp = _request("GET", f"{API_BASE}/nse-search", params={"q": query.strip()})
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Holding intelligence (My Holdings tab in Why dropdown)
# ─────────────────────────────────────────────────────────────────────────────

def get_holding_intel(symbol: str, user_id: int) -> dict:
    """
    Returns holding details + sell/hold signal for a user's position.
    Keys: held, qty, avg_buy_price, buy_date, holding_days, term,
          pnl, pnl_pct, xirr_approx, signal, signal_reason, confidence_add
    """
    resp = _request(
        "GET",
        f"{API_BASE}/holding-intel/{symbol}",
        params={"user_id": user_id}
    )
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# Full impact bundle (Why dropdown — all tabs)
# ─────────────────────────────────────────────────────────────────────────────

def get_impact(symbol: str, user_id: int | None = None) -> dict:
    """
    Returns full analysis bundle: price_levels, mmm, seasonal, trend,
    momentum, analyst, news, corp_events, account_context.
    """
    params = {}
    if user_id:
        params["user_id"] = user_id
    resp = _request("GET", f"{API_BASE}/impact/{symbol}", params=params)
    resp.raise_for_status()
    return resp.json()


def get_impact_quick_batch(symbols: list[str], user_id: int | None = None) -> dict:
    """
    Fast batch: price_levels + trend + momentum only for multiple symbols.
    Returns { SYMBOL: { price_levels, trend, momentum } }
    """
    if not symbols:
        return {}
    params = {"symbols": ",".join(symbols)}
    if user_id:
        params["user_id"] = user_id
    try:
        resp = _request("GET", f"{API_BASE}/impact/quick-batch", params=params)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def get_impact_batch(symbols: list[str], user_id: int | None = None, quick: bool = False) -> dict:
    """
    Full batch impact for multiple symbols.
    Returns { SYMBOL: <full impact dict> }
    Set quick=True to skip analyst/news APIs (faster).
    """
    if not symbols:
        return {}
    params = {"symbols": ",".join(symbols), "quick": str(quick).lower()}
    if user_id:
        params["user_id"] = user_id
    try:
        resp = _request("GET", f"{API_BASE}/impact/batch", params=params)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Suggestion engine
# ─────────────────────────────────────────────────────────────────────────────


def get_suggestion_batch(symbols: list[str], user_id: int = 0) -> dict:
    """
    Batch suggestions for multiple symbols in one API call.
    Returns { SYMBOL: <suggestion dict> }
    """
    if not symbols:
        return {}
    try:
        resp = _request("GET", f"{API_BASE}/suggest/batch",
                        params={"symbols": ",".join(symbols), "user_id": user_id})
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}

def get_suggestion(symbol: str, user_id: int, spot: float = 0.0) -> dict:
    resp = _request(
        "GET",
        f"{API_BASE}/suggest/{symbol}",
        params={"user_id": user_id, "spot": spot}
    )
    resp.raise_for_status()
    return resp.json()

def get_pending_adjustments_stored(user_id: int) -> list[dict]:
    """
    Fast read — returns PENDING records already in DB.
    Use for notification badge on page load (no 5paisa API calls).
    """
    resp = _request("GET", f"{API_BASE}/fno/adjustments/pending-stored/{user_id}")
    resp.raise_for_status()
    return resp.json()
 
 
def detect_pending_adjustments(user_id: int) -> list[dict]:
    """
    Full detection run (hits 5paisa for spot prices).
    Call once per session when the user opens Tab 5.
    """
    resp = _request("GET", f"{API_BASE}/fno/adjustments/pending/{user_id}")
    resp.raise_for_status()
    return resp.json()
 
 
def apply_fno_adjustment(adjustment_id: int, user_id: int) -> dict:
    """Apply a dividend adjustment synthetically (P&L neutral SELL+BUY pair)."""
    resp = _request(
        "POST",
        f"{API_BASE}/fno/adjustments/apply/{adjustment_id}",
        params={"user_id": user_id},
    )
    resp.raise_for_status()
    return resp.json()
 
 
def skip_fno_adjustment(adjustment_id: int, user_id: int) -> dict:
    """Mark adjustment as SKIPPED (user will upload adjusted trades)."""
    resp = _request(
        "POST",
        f"{API_BASE}/fno/adjustments/skip/{adjustment_id}",
        params={"user_id": user_id},
    )
    resp.raise_for_status()
    return resp.json()
 
 
def get_adjustment_history(user_id: int) -> list[dict]:
    """Return full audit log of all dividend adjustments for a user."""
    resp = _request("GET", f"{API_BASE}/fno/adjustments/history/{user_id}")
    resp.raise_for_status()
    return resp.json()

def get_stale_fno_positions(user_id: int) -> dict:
        resp = _request("GET", f"{API_BASE}/fno/stale-positions/{user_id}")
        resp.raise_for_status()
        return resp.json()
    
def fetch_prices_with_change(symbols: list[str]) -> dict:
    """Fetch live prices with % change for a list of symbols."""
    resp = _request("GET", f"{API_BASE}/prices/with-change", params={"symbols": symbols})
    resp.raise_for_status()
    return resp.json()  

def get_task_status(user_id: int, task_name: str = None) -> dict:
    """Poll whether a background task is running for this user."""
    try:
        params = {"task_name": task_name} if task_name else {}
        resp = _request("GET", f"{API_BASE}/task-status/{user_id}", params=params)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"status": "idle"}
    
# Add at end of file:
def upload_holdings(file, user_id: int, broker: str) -> dict:
    """Upload broker holdings file for reconciliation."""
    files = {"file": (file.name, file.getvalue())}
    data = {"user_id": user_id, "broker": broker}
    resp = requests.post(
        f"{API_BASE}/holdings/reconcile/upload",
        files=files, data=data,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    resp.raise_for_status()
    return resp.json()

def apply_holdings_corrections(user_id: int, corrections: str) -> dict:
    """Apply user-confirmed corrections."""
    resp = requests.post(
        f"{API_BASE}/holdings/reconcile/apply",
        data={"user_id": user_id, "corrections": corrections},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    resp.raise_for_status()
    return resp.json()




def upload_holdings_reconcile(file, user_id: int, broker: str) -> dict:
    """Upload broker holdings file for reconciliation"""
    files = {"file": (file.name, file.getvalue())}
    data = {"user_id": user_id, "broker": broker}
    resp = requests.post(
        f"{API_BASE}/holdings/reconcile/upload",
        files=files, data=data,
        headers={"Authorization": f"Bearer {TOKEN}"}
    )
    return resp.json()

def apply_holdings_corrections(user_id: int, corrections) -> dict:
    """Apply user-confirmed corrections"""
    import json
    corr_str = json.dumps(corrections) if isinstance(corrections, list) else corrections
    resp = requests.post(
        f"{API_BASE}/holdings/reconcile/apply",
        data={"user_id": user_id, "corrections": corr_str},
        headers={"Authorization": f"Bearer {TOKEN}"}
    )
    return resp.json()