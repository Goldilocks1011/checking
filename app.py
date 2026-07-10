import sys
import os
from holdings_reconciliation_ui import (
    render_holdings_upload_section,
    render_reconciliation_results
)
# Add the backend folder to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import math  
import streamlit as st
from datetime import date
import requests
from tax_harvest_ui import render_harvest_ui
import backend.services.market_status as market_status
from ce_pe_ui import render_ce_pe_tab, render_group_ce_pe_tab 
from ledger_ui import render_ledger_tab
from api_client import (
    list_users, create_user, upload_equity, get_holdings,
    get_transactions, get_pnl, get_intraday,
    upload_fno, get_fno_positions, get_fno_pnl, auto_populate_master,
    get_stock_master_grid, get_unmatched_symbols, link_symbol,
    run_harvest, upload_ledger, get_ledger, seed_corp_actions, get_corp_actions, add_manual_corp_action,
    sync_nse_corp_actions, add_manual_equity, add_manual_fno, delete_manual_equity, delete_manual_fno,
    create_group, list_groups, add_group_member, remove_group_member, get_group_members, get_group_holdings,
    delete_user, delete_group,
    get_group_stock_master,
    get_merged_transactions, get_merged_pnl, get_merged_intraday,
    get_merged_fno_positions, get_merged_fno_pnl,
    run_harvest_multi, fetch_prices, rename_stock,
    get_fno_transactions,
    get_processed_files, download_file_content, get_fy_holdings, get_holding_lots,
    get_user_stats, create_portfolio_user, get_ce_pe_screener_data, get_scrip_master_stats, upload_scrip_master,
    refresh_fno_from_scrip_master, download_scrip_master, get_advanced_screener_data, get_group_advanced_screener_data,
    get_covered_call_analysis, get_master_reference_positions,     get_covered_call_analysis, get_master_reference_positions, get_wishlist, add_to_wishlist, remove_from_wishlist, sync_wishlist, clear_wishlist_auto,
    clear_wishlist_all, get_pending_adjustments_stored,
        detect_pending_adjustments, fetch_prices_with_change,
        apply_fno_adjustment, fetch_prices_with_change,
        skip_fno_adjustment, upload_holdings_reconcile, apply_holdings_corrections,
        get_adjustment_history,
)
from backend.services.engine import (
    get_fy_list, 
    get_fy_realized_pnl, 
    get_fy_realized_pnl_summary,
    get_fy_intraday,
    get_fy_intraday_summary,
    get_fy_transactions,
    get_fy_transactions_summary,
    get_fy_summary
)
from be_graph_utils import extract_legs_from_op_df, build_be_figure
import pandas as pd


API_BASE = "http://localhost:8001/api/v1"

# ─────────────────────────────────────────────────────────────────────────────
# Lazy-load helper: fetch only when NOT already cached.
# Returns True if data was already in session_state (no spinner needed).
# Usage:
#   _lazy("txn_data", get_transactions, user_id)
# ─────────────────────────────────────────────────────────────────────────────
def _lazy(key: str, fn, *args, force: bool = False, **kwargs):
    """
    If session_state[key] is None (or key missing), call fn(*args, **kwargs)
    and store the result.  Shows a spinner while loading.
    Pass force=True to re-fetch even if cached.
    Returns the cached value.

    If the background preload already populated the key, the function
    returns instantly with no API call.
    """
    if not force and key in st.session_state and st.session_state[key] is not None:
        # Already in cache (possibly from background preload) — instant return
        return st.session_state[key]

    if force or key not in st.session_state or st.session_state[key] is None:
        with st.spinner(f"Loading…"):
            try:
                st.session_state[key] = fn(*args, **kwargs)
            except Exception as e:
                st.error(f"Load error: {e}")
                st.session_state[key] = []
    return st.session_state.get(key, [])


def fmt_qty(v):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return v


def fmt_inr(v, decimals: int = 2) -> str:
    """
    Format a number in Indian number system (lakhs/crores).
      1234        -> Rs 1,234.00
      123456      -> Rs 1,23,456.00
      12345678    -> Rs 1,23,45,678.00
    """
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    
    # ⭐ CRITICAL FIX: Safely handle NaN and Infinity
    if math.isnan(n) or math.isinf(n):
        return "—"
    sign = "-" if n < 0 else ""
    abs_n = abs(n)
    int_part = int(abs_n)
    s = str(int_part)
    if len(s) > 3:
        result = s[-3:]
        s = s[:-3]
        while s:
            result = s[-2:] + "," + result
            s = s[:-2]
    else:
        result = s
    if decimals > 0:
        frac = round(abs_n - int_part, decimals)
        dec_str = f"{frac:.{decimals}f}"[1:]  # ".50"
        return f"₹{sign}{result}{dec_str}"
    return f"₹{sign}{result}"

def _guarded_button(label: str, user_id: int, task_name: str, key: str, help_text: str = ""):
    """
    Shows a button, but first checks if this task is already running for
    the user. If so, shows a spinner-style badge instead of the button
    and returns False so the caller doesn't fire a duplicate request.
    """
    status = api_client.get_task_status(user_id, task_name)
    if status.get("status") == "running":
        st.info(f"⏳ {status.get('message', 'Processing in background…')}")
        st.caption("This button is disabled until the current job finishes. Refresh to check progress.")
        return False
    if status.get("status") == "error":
        st.error(f"⚠️ Last run failed: {status.get('error', 'unknown error')}")
    return st.button(label, key=key, help=help_text)

def _fno_unrealized_total():
    """
    Sums live_pnl across all cached F&O open positions.
    Returns (total, any_data_loaded).
    Uses st.session_state['fno_pos_data'] — already populated by the
    background preload worker, so this costs ZERO extra API calls.
    """
    data = st.session_state.get("fno_pos_data") or []
    total = 0.0
    has_live = False
    for p in data:
        v = p.get("live_pnl")
        if v is not None:
            try:
                total += float(v)
                has_live = True
            except (TypeError, ValueError):
                pass
    return total, has_live

# ─────────────────────────────────────────────────────────────────────────────
# Auth pages
# ─────────────────────────────────────────────────────────────────────────────

def login_page():
    st.title("📈 Portfolio Tracker Login")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        try:
            resp = requests.post(f"{API_BASE}/auth/login",
                                 json={"email": email, "password": password})
            if resp.status_code != 200:
                st.error("Invalid email or password")
                return
            data = resp.json()
            st.session_state.token = data["access_token"]
            st.session_state.display_name = data.get("display_name", email)
            st.rerun()
        except KeyError:
            st.error("Login succeeded but token missing.")
        except Exception as e:
            st.error(f"Login failed: {e}")

    if st.button("Forgot Password?"):
        st.session_state.show_forgot = True
        st.rerun()


def signup_page():
    st.title("Create Account")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    confirm = st.text_input("Confirm Password", type="password")
    display_name = st.text_input("Display Name (optional)")
    if st.button("Sign Up"):
        if password != confirm:
            st.error("Passwords do not match")
        else:
            try:
                resp = requests.post(f"{API_BASE}/auth/signup",
                                     json={"email": email, "password": password,
                                           "display_name": display_name})
                resp.raise_for_status()
                st.success("Account created! You can now log in.")
                st.session_state.show_signup = False
                st.rerun()
            except Exception:
                st.error("Signup failed. Email may already be in use.")


def forgot_page():
    st.title("Forgot Password")
    email = st.text_input("Enter your registered email")
    if st.button("Send Reset Link"):
        try:
            resp = requests.post(f"{API_BASE}/auth/forgot-password",
                                 json={"email": email})
            resp.raise_for_status()
            st.success("If the email is registered, a reset link has been sent.")
        except Exception:
            st.error("Something went wrong.")


def reset_page(token: str):
    st.title("Reset Password")
    new_password = st.text_input("New Password", type="password")
    confirm = st.text_input("Confirm Password", type="password")
    if st.button("Reset Password"):
        if new_password != confirm:
            st.error("Passwords do not match")
        else:
            try:
                resp = requests.post(f"{API_BASE}/auth/reset-password",
                                     json={"token": token, "new_password": new_password})
                resp.raise_for_status()
                st.success("Password reset! You can log in now.")
                st.query_params.clear()
                st.session_state.pop("token", None)
                st.rerun()
            except Exception:
                st.error("Reset link expired or invalid.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

reset_token = st.query_params.get("reset_token", None)
if reset_token:
    reset_page(reset_token)
else:
    if "token" not in st.session_state:
        if "show_signup" in st.session_state and st.session_state.show_signup:
            signup_page()
        elif "show_forgot" in st.session_state and st.session_state.show_forgot:
            forgot_page()
        else:
            choice = st.radio("", ["Login", "Sign Up", "Forgot Password"], horizontal=True)
            if choice == "Login":
                login_page()
            elif choice == "Sign Up":
                signup_page()
            else:
                forgot_page()
    else:
        # ── Set API token for all subsequent calls ─────────────────────────────
        import api_client
        api_client.TOKEN = st.session_state.token

        # ── Fetch current account_id (cached for session) ─────────────────────
        if "current_account_id" not in st.session_state:
            try:
                me_resp = requests.get(
                    f"{API_BASE}/auth/me",
                    headers={"Authorization": f"Bearer {st.session_state.token}"}
                )
                st.session_state.current_account_id = me_resp.json().get("id") if me_resp.status_code == 200 else None
            except Exception:
                st.session_state.current_account_id = None

        st.set_page_config(page_title="Portfolio Tracker v2", layout="wide")
        
        # ─────────────────────────────────────────────────────────────────────
        # Sidebar
        # ─────────────────────────────────────────────────────────────────────
        # ── Market status indicator ──────────────────────────────────────────────
        st.sidebar.markdown("---")
        st.sidebar.markdown(market_status.get_market_status_badge(), help="Market status based on NSE holiday list and trading hours (9:15 AM - 3:30 PM IST)")
        st.sidebar.markdown("---")
        st.sidebar.title("📈 Portfolio v2")
        mode = st.sidebar.radio("Mode", ["👤 Single User", "👥 Group"], horizontal=True)

        if mode == "👤 Single User":
            with st.sidebar.expander("➕ New User"):
                username = st.text_input("Username", help="Enter a unique name for this portfolio user")
                broker = st.selectbox("Broker", ["5paisa", "IIFL", "Zerodha", "Multiple"], help="Choose the broker where you trade")
                if st.button("Create", key="create_user", help="Create a new portfolio user"):
                    if username.strip():
                        try:
                            user = create_portfolio_user(username.strip(), broker)
                            st.success(f"User '{user['username']}' created")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
            
            users = []
            try:
                users = list_users()
            except Exception:
                st.sidebar.warning("Could not load users – is the API running?")
            
            selected_user = None
            if users:
                user_names = [u["username"] for u in users]
                chosen = st.sidebar.selectbox("Select user", user_names, help="Choose a user to view their portfolio data")
                selected_user = next((u for u in users if u["username"] == chosen), None)
                
                if selected_user:
                    with st.sidebar.expander("🗑️ Delete User"):
                        st.warning("Type the username to confirm deletion")
                        confirm_text = st.text_input("Confirm username", key="del_confirm")
                        if _guarded_button("Delete User", selected_user["id"], "delete_user", key="del_btn"):
                            if confirm_text == selected_user["username"]:
                                try:
                                    delete_user(selected_user["id"])
                                    st.sidebar.success("User deleted")
                                    st.rerun()
                                except Exception as e:
                                    st.sidebar.error(str(e))
                            else:
                                st.sidebar.error("Username does not match")
            else:
                st.sidebar.info("👤 **No users yet. Create your first user above.**")
            
            group_mode = False

        else:  # Group mode
            group_mode = True
            selected_user = None
            st.sidebar.subheader("Groups")
            with st.sidebar.expander("➕ Create Group"):
                gname = st.text_input("Group Name")
                if st.button("Create Group"):
                    if gname.strip():
                        try:
                            create_group(gname.strip())
                            st.success(f"Group '{gname}' created")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))
            groups = []
            try:
                groups = list_groups()
            except Exception:
                pass
            if groups:
                gnames = [g["name"] for g in groups]
                selected_group_name = st.sidebar.selectbox("Select group", gnames, help="Choose a group to view their portfolio data")
                selected_group = next((g for g in groups if g["name"] == selected_group_name), None)
                if selected_group:
                    with st.sidebar.expander("🗑️ Delete Group"):
                        st.warning("Type the group name to confirm deletion")
                        del_grp_confirm = st.text_input("Confirm group name", key="del_grp_confirm")
                        if st.button("Delete Group", type="primary", key="del_grp_btn"):
                            if del_grp_confirm == selected_group["name"]:
                                try:
                                    delete_group(selected_group["id"])
                                    st.sidebar.success("Group deleted")
                                    st.rerun()
                                except Exception as e:
                                    st.sidebar.error(str(e))
                            else:
                                st.sidebar.error("Group name does not match")
                    st.session_state["selected_group_id"] = selected_group["id"]
                    members = []
                    try:
                        members = get_group_members(selected_group["id"])
                    except Exception:
                        pass
                    if members:
                        st.sidebar.write("**Members:**")
                        for m in members:
                            st.sidebar.write(f"- {m['username']} ({m['broker']})")
                    with st.sidebar.expander("Manage Members"):
                        all_users = list_users()
                        for u in all_users:
                            cols = st.columns([1, 2, 1])
                            is_in = any(m["id"] == u["id"] for m in members)
                            if cols[1].button(f"{'✅' if is_in else '➕'} {u['username']}", key=f"tog_{u['id']}"):
                                if is_in:
                                    remove_group_member(selected_group["id"], u["id"])
                                else:
                                    add_group_member(selected_group["id"], u["id"])
                                st.rerun()
            else:
                st.sidebar.info("No groups yet.")

        # ── Settings ──────────────────────────────────────────────────────────
        with st.sidebar:
            with st.expander("⚙️ Settings"):
                st.write(f"Logged in as: {st.session_state.display_name}")
                new_name = st.text_input("Display Name", value=st.session_state.display_name)
                if st.button("Update Name"):
                    resp = requests.put(f"{API_BASE}/auth/profile",
                                        json={"display_name": new_name},
                                        headers={"Authorization": f"Bearer {st.session_state.token}"})
                    if resp.status_code == 200:
                        st.session_state.display_name = new_name
                        st.success("Name updated")
                    else:
                        st.error("Failed to update name")

                if st.button("Change Password"):
                    st.session_state.show_change_pwd = True

                if st.button("Sign Out"):
                    del st.session_state.token
                    st.rerun()

            if st.session_state.get("show_change_pwd"):
                with st.form("change_pwd_form"):
                    st.write("Change Password")
                    old_pwd = st.text_input("Old Password", type="password")
                    new_pwd = st.text_input("New Password", type="password")
                    confirm = st.text_input("Confirm New Password", type="password")
                    submitted = st.form_submit_button("Update Password")
                    if submitted:
                        if new_pwd != confirm:
                            st.error("Passwords don't match")
                        else:
                            resp = requests.put(
                                f"{API_BASE}/auth/change-password",
                                json={"old_password": old_pwd, "new_password": new_pwd},
                                headers={"Authorization": f"Bearer {st.session_state.token}"}
                            )
                            if resp.status_code == 200:
                                st.success("Password changed")
                                st.session_state.show_change_pwd = False
                                st.rerun()
                            else:
                                st.error("Old password is incorrect")
                if st.button("Cancel"):
                    st.session_state.show_change_pwd = False
                    st.rerun()
        
        # ── External links ──────────────────────────────────────────────────────────────
        st.sidebar.markdown("---")
        st.sidebar.markdown("### 🔗 External Tools")
        if st.sidebar.link_button(
            "📊 Stock Analyzer",
            "http://139.59.74.2:8080",
            help="Open Stock Analyzer website in new tab",
            use_container_width=True,
        ):
            pass  # link_button opens the URL automatically in a new tab

        if st.sidebar.link_button(
            "📰 Stock Pulse",
            "http://159.89.225.5:8010",
            help="Open Stock Pulse website in new tab",
            use_container_width=True,
        ):
            pass
        # ─────────────────────────────────────────────────────────────────────
        # Main Area
        # ─────────────────────────────────────────────────────────────────────
        if group_mode:
            group_id = st.session_state.get("selected_group_id")
            if not group_id:
                st.info("Select a group from the sidebar.")
            else:
                members = []
                try:
                    members = get_group_members(group_id)
                except Exception:
                    st.warning("Could not load group members")
                    members = []

                st.header(f"👥 {selected_group_name}")

                if "active_group_id" not in st.session_state:
                    st.session_state.active_group_id = None
                if group_id != st.session_state.active_group_id:
                    for key in list(st.session_state.keys()):
                        if key.startswith("grp_"):
                            del st.session_state[key]
                    st.session_state.active_group_id = group_id

                # ── Group background preload (stock master + merged F&O positions) ──
                if not st.session_state.get(f"_grp_preload_done_{group_id}"):
                    import threading as _th
                    def _grp_preload(gid: int, member_ids: list):
                        try:
                            if "grp_sm_data" not in st.session_state or st.session_state.get("grp_sm_data") is None:
                                st.session_state["grp_sm_data"] = get_group_stock_master(gid)
                        except Exception:
                            pass
                        try:
                            if "grp_fno_pos_data" not in st.session_state or st.session_state.get("grp_fno_pos_data") is None:
                                st.session_state["grp_fno_pos_data"] = get_merged_fno_positions(member_ids)
                        except Exception:
                            pass
                    _ids = [m["id"] for m in members]
                    _th.Thread(target=_grp_preload, args=(group_id, _ids), daemon=True).start()
                    st.session_state[f"_grp_preload_done_{group_id}"] = True

                if not members:
                    st.info("No members in this group. Add them in the sidebar.")
                else:
                    tab_g1, tab_g2, tab_g3, tab_g4, tab_g5, tab_g6, tab_g7, tab_g8, tab_g9, tab_g10 = st.tabs([
                        "📊 Group Stock Master", "🔁 Transactions", "📈 Realized P&L", "⚡ Intraday",
                        "📈 F&O Positions", "💰 F&O P&L", "🌾 Tax Harvest", "📉 BE Graphs",
                        "📈 CE/PE Screener", "📋 Group Wishlist"                                         
                    ])

                    with tab_g1:
                        st.subheader("📊 Group Stock Master")
                        if st.button("🔄 Refresh Group Master", key="grp_sm_refresh"):
                            st.session_state.grp_sm_data = None
                        data = _lazy("grp_sm_data", get_group_stock_master, group_id)
                        if data:
                            df = pd.DataFrame(data)
                            static_keys = ["ISIN", "Name", "Custom Name", "Canonical", "F&O", "Lot Size"]
                            dynamic_keys = sorted([c for c in df.columns if c.endswith("_symbol") or c.endswith("_qty")])
                            other_keys = ["Total Qty", "Pending Qty"]
                            all_ordered = []
                            seen = set()
                            for col in static_keys + dynamic_keys + other_keys:
                                if col in df.columns and col not in seen:
                                    all_ordered.append(col)
                                    seen.add(col)
                            for col in df.columns:
                                if col not in seen:
                                    all_ordered.append(col)
                                    seen.add(col)
                            df_disp = df[all_ordered].copy()
                            df_disp = df_disp.loc[:, ~df_disp.columns.duplicated(keep='first')]
                            st.dataframe(df_disp, use_container_width=True)
                        else:
                            st.info("No holdings found for this group.")

                    with tab_g2:
                        st.subheader("🔁 All Group Transactions")
                        if st.button("Refresh Transactions", key="grp_txn_refresh"):
                            st.session_state.grp_txn_data = None
                        ids = [m["id"] for m in members]
                        txn = _lazy("grp_txn_data", get_merged_transactions, ids)
                        if txn:
                            df = pd.DataFrame(txn)
                            df = df[["trade_date", "user_name", "symbol", "trade_type", "quantity", "price"]]
                            df.columns = ["Date", "User", "Symbol", "Type", "Qty", "Price"]
                            st.dataframe(df, use_container_width=True)
                        else:
                            st.info("No transactions yet.")

                    with tab_g3:
                        st.subheader("📈 Realized P&L (All Members)")
                        if st.button("Refresh Realized P&L", key="grp_pnl_refresh"):
                            st.session_state.grp_pnl_data = None
                        ids = [m["id"] for m in members]
                        pnl = _lazy("grp_pnl_data", get_merged_pnl, ids)
                        if pnl:
                            df = pd.DataFrame(pnl)
                            df = df[["sell_date", "user_name", "symbol", "quantity", "buy_price", "sell_price", "gross_pnl", "net_pnl"]]
                            df.columns = ["Sell Date", "User", "Symbol", "Qty", "Buy Price", "Sell Price", "Gross P&L", "Net P&L"]
                            st.dataframe(df, use_container_width=True)
                        else:
                            st.info("No realized P&L yet.")

                    with tab_g4:
                        st.subheader("⚡ Intraday Trades (All Members)")
                        if st.button("Refresh Intraday", key="grp_intra_refresh"):
                            st.session_state.grp_intraday_data = None
                        ids = [m["id"] for m in members]
                        intra = _lazy("grp_intraday_data", get_merged_intraday, ids)
                        if intra:
                            df = pd.DataFrame(intra)
                            df = df[["trade_date", "user_name", "symbol", "quantity", "buy_price", "sell_price", "gross_pnl"]]
                            df.columns = ["Date", "User", "Symbol", "Qty", "Buy", "Sell", "P&L"]
                            st.dataframe(df, use_container_width=True)
                        else:
                            st.info("No intraday trades.")

                    with tab_g5:
                        st.subheader("📈 F&O Open Positions (All Members)")
                        if st.button("Refresh F&O Positions", key="grp_fno_pos_refresh"):
                            st.session_state.grp_fno_pos_data = None
                        ids = [m["id"] for m in members]
                        pos = _lazy("grp_fno_pos_data", get_merged_fno_positions, ids)
                        if pos:
                            df = pd.DataFrame(pos)
                            df = df[["underlying", "instrument_type", "expiry_date", "strike_price", "open_qty", "avg_price", "user_name"]]
                            df.columns = ["Underlying", "Type", "Expiry", "Strike", "Qty", "Avg Price", "User"]
                            st.dataframe(df, use_container_width=True)
                        else:
                            st.info("No open F&O positions.")

                    with tab_g6:
                        st.subheader("💰 F&O Realized P&L (All Members)")
                        if st.button("Refresh F&O P&L", key="grp_fno_pnl_refresh"):
                            st.session_state.grp_fno_pnl_data = None
                        ids = [m["id"] for m in members]
                        fpnl = _lazy("grp_fno_pnl_data", get_merged_fno_pnl, ids)
                        if fpnl:
                            df = pd.DataFrame(fpnl)
                            df = df[["underlying", "instrument_type", "sell_date", "quantity", "buy_price", "sell_price", "gross_pnl", "user_name"]]
                            df.columns = ["Underlying", "Type", "Sell Date", "Qty", "Buy", "Sell", "Gross P&L", "User"]
                            st.dataframe(df, use_container_width=True)
                        else:
                            st.info("No realized F&O P&L.")

                    with tab_g7:
                        st.subheader("🌾 Tax Harvesting (Cross‑Account)")
                        col1, col2 = st.columns(2)
                        start = col1.date_input("From", value=date.today().replace(month=3, day=1), key="grp_th_start")
                        end   = col2.date_input("To",   value=date.today().replace(month=3, day=31), key="grp_th_end")

                        if "grp_th_result" not in st.session_state:
                            st.session_state.grp_th_result = None

                        if st.session_state.grp_th_result is None:
                            with st.spinner("Analysing…"):
                                try:
                                    ids = [m["id"] for m in members]
                                    st.session_state.grp_th_result = run_harvest_multi(ids, str(start), str(end))
                                except Exception as e:
                                    st.session_state.grp_th_result = {"error": str(e)}

                        if st.button("▶ Run Harvest Analysis", type="primary", key="grp_th_run"):
                            with st.spinner("Analysing…"):
                                try:
                                    ids = [m["id"] for m in members]
                                    st.session_state.grp_th_result = run_harvest_multi(ids, str(start), str(end))
                                    st.session_state.pop("_harvest_price_map", None)
                                    st.rerun()
                                except Exception as e:
                                    st.session_state.grp_th_result = {"error": str(e)}

                        render_harvest_ui(result=st.session_state.grp_th_result, fetch_prices_fn=fetch_prices)

                    with tab_g8:
                        st.subheader("📉 BE Graphs (per member)")
                        member_names = [m["username"] for m in members]
                        chosen = st.selectbox("Select member", member_names, key="be_member_select")
                        chosen_id = next(m["id"] for m in members if m["username"] == chosen)

                        cache_key = f"grp_be_pos_{chosen_id}"
                        if st.button("🔄 Refresh Positions", key=f"be_refresh_{chosen_id}"):
                            st.session_state[cache_key] = None

                        positions = _lazy(cache_key, get_fno_positions, chosen_id)
                        if positions:
                            pos_df = pd.DataFrame(positions)
                            stock_legs = extract_legs_from_op_df(pos_df)
                            if stock_legs:
                                chosen_stock = st.selectbox("Select underlying", sorted(stock_legs.keys()), key="be_stock")
                                if chosen_stock:
                                    legs = stock_legs[chosen_stock]
                                    cmp_val = st.number_input("Current Market Price (₹)", min_value=0.0, value=0.0, step=1.0, key="be_cmp")
                                    fig = build_be_figure(legs, chosen_stock, cmp=cmp_val)
                                    st.plotly_chart(fig, use_container_width=True)
                            else:
                                st.info("No valid F&O legs found.")
                        else:
                            st.info("No open positions found.")

                    with tab_g9:
                        # Dependency check: need F&O uploads for at least one member
                        st.header("📈 Group CE/PE — Advanced Options Screener")
 
                        # Warn if no F&O data has been seen for any member yet
                        any_fno = False
                        for m in members:
                            try:
                                check = requests.get(
                                    f"{API_BASE}/fno/positions/{m['id']}",
                                    headers={"Authorization": f"Bearer {st.session_state.token}"}
                                ).json()
                                if check:
                                    any_fno = True
                                    break
                            except Exception:
                                pass
 
                        if not any_fno:
                            st.info(
                                "ℹ️ **Prerequisite:** Upload F&O files for at least one group member "
                                "in the **Upload & Manage** tab (single-user mode) so that open "
                                "positions are visible here."
                            )
 
                        render_group_ce_pe_tab(
                            group_id              = group_id,
                            get_group_advanced_fn = get_group_advanced_screener_data,
                        )
        
                    with tab_g10:
                        from wishlist_ui import render_group_wishlist_tab
                        render_group_wishlist_tab(
                            group_id=st.session_state.get("selected_group_id"),
                            api_fns={
                                "get":        api_client.get_group_wishlist,
                                "add":        api_client.add_to_group_wishlist,
                                "remove":     api_client.remove_from_group_wishlist,
                                "sync":       api_client.sync_group_wishlist,
                                "clear_auto": api_client.clear_group_wishlist_auto,
                                "clear_all":  api_client.clear_group_wishlist_all,
                            }
                        )        
        
        else:
            # ─────────────────────────────────────────────────────────────────
            # Single user mode
            # ─────────────────────────────────────────────────────────────────
            if selected_user:
                st.success(f"Active user: **{selected_user['username']}** (ID {selected_user['id']})")

                # ── Invalidate caches when user changes ──────────────────────────────────
                if "active_user_id" not in st.session_state:
                    st.session_state.active_user_id = None

                user_changed = selected_user["id"] != st.session_state.active_user_id

                if user_changed:
                    uid = selected_user["id"]
                    for key in [
                        "sm_grid_data", "txn_data", "fno_txn_data", "pnl_data", "intraday_data",
                        "fno_pos_data", "fno_pnl_data", "su_th_result", "be_positions", "ledger_entries",
                        "cc_analysis_data", "master_ref_data", "_preload_done",
                        f"stats_{uid}", f"holdings_{uid}", f"unresolved_{uid}", f"corp_actions_{uid}",
                        f"pfiles_{uid}", f"ca_aware_{uid}",
                    ]:
                        st.session_state.pop(key, None)
                    st.session_state.active_user_id = uid

                _uid = selected_user["id"]

                # ── Background preload: kick off heavy tabs in parallel threads ──────────
                # Runs once per user selection; results land in session_state for instant
                # tab switching.  Uses daemon threads so they don't block Streamlit reruns.
                if not st.session_state.get("_preload_done"):
                    import threading, time as _t

                    def _preload_worker(uid: int):
                        """Fetch holdings grid + stats + F&O positions + basic CE/PE in background."""
                        import requests as _req
                        try:
                            # Holdings grid — Tab 2
                            if "sm_grid_data" not in st.session_state or st.session_state.get("sm_grid_data") is None:
                                st.session_state["sm_grid_data"] = get_stock_master_grid(uid)
                        except Exception:
                            pass
                        try:
                            # Stats — header metrics
                            key = f"stats_{uid}"
                            if key not in st.session_state or st.session_state.get(key) is None:
                                st.session_state[key] = get_user_stats(uid)
                        except Exception:
                            pass
                        try:
                            # F&O positions — Tab 5 + BE graphs + CE/PE screener
                            if "fno_pos_data" not in st.session_state or st.session_state.get("fno_pos_data") is None:
                                resp = _req.get(
                                    f"{API_BASE}/fno/positions/{uid}",
                                    headers={"Authorization": f"Bearer {st.session_state.get('token', '')}"},
                                    timeout=15,
                                )
                                if resp.status_code == 200:
                                    st.session_state["fno_pos_data"] = resp.json()
                        except Exception:
                            pass
                        try:
                            # Basic CE/PE screener — Tab 9 (30-40s, start early)
                            cepe_key = f"ce_pe_basic_data_{uid}"
                            if cepe_key not in st.session_state or st.session_state.get(cepe_key) is None:
                                resp = _req.get(
                                    f"{API_BASE}/ce-pe-screener/{uid}",
                                    headers={"Authorization": f"Bearer {st.session_state.get('token', '')}"},
                                    timeout=60,
                                )
                                if resp.status_code == 200:
                                    data = resp.json()
                                    if data.get("status") == "success":
                                        st.session_state[cepe_key] = data["data"]
                        except Exception:
                            pass

                    t = threading.Thread(target=_preload_worker, args=(_uid,), daemon=True)
                    t.start()
                    st.session_state["_preload_done"] = True

                stats = _lazy(f"stats_{_uid}", get_user_stats, _uid)
                if not stats:
                    stats = {"stocks_held": 0, "total_invested": 0, "realized_pnl": 0, "tax_due": 0, "total_records": 0}

                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric(
                    "Stocks Held (Current Holdings)",
                    stats.get("stocks_held", 0),
                    help="Number of unique equity symbols with quantity > 0"
                )
                c2.metric(
                    "Total Invested (Current Holdings)",
                    fmt_inr(stats.get("total_invested", 0)),
                    help="Total buy cost of shares currently held (not yet sold)"
                )
                c3.metric(
                    "Realized P&L (Equity Closed Trades)",
                    fmt_inr(stats.get("realized_pnl", 0)),
                    help="Profit/loss from completed equity sell transactions only. F&O P&L is in the F&O tab."
                )
                # c4 = Equity unrealized (populated after P&L tab computes it)
                _unreal_metric_placeholder = c4
                # c5 = F&O unrealized (populated from cached fno_pos_data — no extra API call)
                _fno_unreal_metric_placeholder = c5

                st.divider()

                # Equity Unrealized P&L
                _unreal_val = st.session_state.get("_total_unrealized")
                if _unreal_val is not None:
                    _unreal_metric_placeholder.metric(
                        "Unrealized P&L (Live)",
                        fmt_inr(_unreal_val),
                        help="Auto-refreshes every 5 min. Go to P&L tab for full breakdown."
                    )
                else:
                    _unreal_metric_placeholder.metric(
                        "Unrealized P&L (Live)",
                        "Loading…",
                        help="Opens automatically when you visit the P&L tab."
                    )

                # F&O Unrealized P&L (NEW)
                _fno_unreal_val, _fno_has_live = _fno_unrealized_total()
                if _fno_has_live:
                    _fno_metric_color = "normal" if _fno_unreal_val >= 0 else "inverse"
                    _fno_metric_delta = "Profit" if _fno_unreal_val >= 0 else "Loss"
                    _fno_unreal_metric_placeholder.metric(
                        "F&O Unrealized P&L (Live)",
                        fmt_inr(_fno_unreal_val),
                        delta=_fno_metric_delta,
                        delta_color=_fno_metric_color,
                        help="Sum of live_pnl across all open F&O positions. Visit F&O tab for per-contract breakdown."
                    )
                elif st.session_state.get("fno_pos_data"):
                    # Data loaded but no live prices could be fetched for any contract
                    _fno_unreal_metric_placeholder.metric(
                        "F&O Unrealized P&L (Live)",
                        "—",
                        help="Positions loaded, but live prices unavailable for any contract."
                    )
                else:
                    _fno_unreal_metric_placeholder.metric(
                        "F&O Unrealized P&L (Live)",
                        "Loading…",
                        help="Opens automatically when you visit the F&O tab."
                    )
               
               
                # Determine if a master reference tab should be shown
                _current_acct_id = st.session_state.get("current_account_id")
                _is_child_account = False
                if _current_acct_id is not None:
                    try:
                        _acct_resp = requests.get(
                            f"{API_BASE}/auth/me",
                            headers={"Authorization": f"Bearer {st.session_state.token}"}
                        )
                        # We already have the account id; check role via a simple approach:
                        # query DB indirectly — if account has role != master, show ref tab
                        # We'll check via the master-reference endpoint returning data vs error
                        _is_child_account = True  # default true; endpoint will return error if master
                    except Exception:
                        _is_child_account = False

                _tab_labels = [
                    "📤 Upload & Manage", "📊 Holdings / Stock Master", "🔁 Transactions",
                    "📈 P&L (Realized + Unrealized + Intraday)",
                    "📈 F&O (Positions & P&L)",
                    "🌾 Tax Harvest", "📉 BE Graphs", "📒 Ledger", "📈 CE/PE Screener",
                    "📡 Master Reference", "📋 Wishlist" 
                ]
                _all_tabs = st.tabs(_tab_labels)
                (tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11) = _all_tabs

                # ── Tab 1: Upload & Manage ─────────────────────────────────────
                with tab1:
                    st.subheader("📤 Upload & Manage")

                    with st.expander("📈 Equity Upload", expanded=True):
                        broker = st.selectbox("Broker for this file", ["5paisa", "IIFL", "Zerodha"], key="eq_broker_main")
                        if broker == "5paisa":
                            st.info("📌 **Download path:** Report → Trade Report → Transaction → Select date & transaction type")
                        elif broker == "IIFL":
                            st.info("📌 **Download path:** Report → Trade Listing → Select date")
                        elif broker == "Zerodha":
                            st.info("📌 **Download path:** Console → Reports → Tradebook → Select date and file type (Equity)")

                        uploaded_files = st.file_uploader(
                            "Choose Equity Excel files", type=["xls", "xlsx"], accept_multiple_files=True, key="eq_upload_main",
                            help="Upload trade transaction files from your broker. Supported: 5paisa, IIFL, Zerodha"
                        )
                        _upload_status = api_client.get_task_status(selected_user["id"], "upload_equity")
                        if _upload_status.get("status") == "running":
                            st.info(f"⏳ {_upload_status.get('message', 'Upload already processing…')}")
                        if st.button("▶ Process Equity Files", type="primary",
                                    disabled=(not uploaded_files or _upload_status.get("status") == "running"),
                                    key="eq_process_main"):
                            with st.spinner("Uploading and processing..."):
                                for file in uploaded_files:
                                    try:
                                        result = upload_equity(file, selected_user["id"], broker, file_type="EQ")
                                        if result["status"] == "success":
                                            st.success(result["message"])
                                        elif result["status"] == "skipped":
                                            st.warning(result["message"])
                                        else:
                                            st.error(result["message"])
                                    except Exception as e:
                                        st.error(f"Upload failed for {file.name}: {e}")
                            try:
                                auto_populate_master(selected_user["id"])
                            except Exception:
                                pass
                            st.session_state.pop("sm_grid_data", None)
                            st.session_state.pop(f"pfiles_{_uid}", None)
                            st.rerun()

                    with st.expander("📊 F&O Upload"):
                        fno_broker = st.selectbox("Broker (F&O)", ["5paisa", "IIFL", "Zerodha"], key="fno_broker_main")
                        if fno_broker == "5paisa":
                            st.info("📌 **Download path:** Report → Trade Report FNO → Select date")
                        elif fno_broker == "IIFL":
                            st.info("📌 **Download path:** Report → Trade Listing (F&O) → Select date")
                        elif fno_broker == "Zerodha":
                            st.info("📌 **Download path:** Console → Reports → F&O → Select date")

                        fno_files = st.file_uploader(
                            "Choose F&O Excel files", type=["xls", "xlsx"], accept_multiple_files=True, key="fno_upload_main"
                        )
                        if st.button("▶ Process F&O Files", type="primary", disabled=(not fno_files), key="fno_process_main"):
                            with st.spinner("Uploading and processing..."):
                                for file in fno_files:
                                    try:
                                        result = upload_fno(file, selected_user["id"], fno_broker, file_type="FNO")
                                        if result["status"] == "success":
                                            st.success(result["message"])
                                        elif result["status"] == "skipped":
                                            st.warning(result["message"])
                                        else:
                                            st.error(result["message"])
                                    except Exception as e:
                                        st.error(f"Upload failed for {file.name}: {e}")
                            st.session_state.pop("fno_pos_data", None)
                            st.session_state.pop(f"pfiles_{_uid}", None)
                            st.rerun()

                    with st.expander("🏦 Holdings Upload (for Reconciliation)"):
                        render_holdings_upload_section(
                            user_id=selected_user["id"],
                            token=st.session_state.token
                        )
                            
                    st.divider()
                    st.subheader("✏️ Manual Entry")
                    entry_type = st.radio("Type", ["Equity", "F&O"], horizontal=True, key="manual_type")
                    with st.form("manual_form"):
                        if entry_type == "Equity":
                            sym = st.text_input("Symbol")
                            tt = st.selectbox("Trade Type", ["BUY","SELL","TRANSFER_IN","TRANSFER_OUT","BONUS","DEMERGER_IN","MERGER_OUT"])
                            qty = st.number_input("Quantity", min_value=0.0, value=1.0)
                            price = st.number_input("Price", min_value=0.0, value=1.0)
                            m_date = st.date_input("Date")
                            broker_man = st.text_input("Broker", value="Manual")
                            rem = st.text_input("Remarks", value="Manual")
                            if st.form_submit_button("Add Equity"):
                                data = {
                                    "symbol": sym, "trade_type": tt, "quantity": qty, "price": price,
                                    "trade_date": str(m_date), "broker": broker_man, "remarks": rem,
                                    "company_name": sym, "exchange": "NSE", "isin": "", "segment": "EQ",
                                    "brokerage": 0, "tax_charges": 0
                                }
                                try:
                                    res = add_manual_equity(selected_user["id"], data)
                                    if res["status"] == "success":
                                        st.success(res["message"])
                                        st.rerun()
                                    else:
                                        st.error(res["message"])
                                except Exception as e:
                                    st.error(str(e))
                        else:
                            und = st.text_input("Underlying")
                            itype = st.selectbox("Instrument", ["FUT","CE","PE"])
                            tt = st.selectbox("Trade Type", ["BUY","SELL"])
                            qty = st.number_input("Quantity", min_value=0.0, value=1.0)
                            price = st.number_input("Price", min_value=0.0, value=1.0)
                            exp = st.date_input("Expiry")
                            strike = st.number_input("Strike", min_value=0.0, value=0.0)
                            m_date = st.date_input("Trade Date")
                            broker_man = st.text_input("Broker", value="Manual")
                            rem = st.text_input("Remarks", value="Manual")
                            if st.form_submit_button("Add F&O"):
                                data = {
                                    "underlying": und, "instrument_type": itype, "expiry_date": str(exp),
                                    "strike_price": strike, "trade_date": str(m_date), "trade_type": tt,
                                    "quantity": qty, "price": price, "brokerage": 0, "tax_charges": 0,
                                    "broker": broker_man, "remarks": rem
                                }
                                try:
                                    res = add_manual_fno(selected_user["id"], data)
                                    if res["status"] == "success":
                                        st.success(res["message"])
                                        st.session_state.pop("fno_pos_data", None)
                                        st.rerun()
                                    else:
                                        st.error(res["message"])
                                except Exception as e:
                                    st.error(str(e))

                    st.divider()
                    st.subheader("🗑️ Delete Manual Entry")
                    del_type = st.radio("Delete type", ["Equity", "F&O"], key="del_type")
                    txn_id = st.number_input("Transaction ID", min_value=0, step=1, key="del_txn_id")
                    if st.button("Delete", key="del_manual_btn"):
                        try:
                            if del_type == "Equity":
                                res = delete_manual_equity(int(txn_id), selected_user["id"])
                            else:
                                res = delete_manual_fno(int(txn_id), selected_user["id"])
                            if res["status"] == "deleted":
                                st.success("Deleted!")
                                st.rerun()
                            else:
                                st.error(res.get("message","Error"))
                        except Exception as e:
                            st.error(str(e))

                    st.divider()
                    st.subheader("📋 Uploaded Files")

                    if st.button("🔄 Refresh File List", key="pfiles_refresh"):
                        st.session_state.pop(f"pfiles_{_uid}", None)
                    pfiles = _lazy(f"pfiles_{_uid}", get_processed_files, selected_user["id"]) or []

                    if pfiles:
                        if len(pfiles) >= 2:
                            import zipfile, io as _io
                            zip_buf = _io.BytesIO()
                            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                                for pf in pfiles:
                                    fb = download_file_content(selected_user["id"], pf["filename"])
                                    if fb:
                                        zf.writestr(pf["filename"], fb)
                            zip_buf.seek(0)
                            st.download_button(
                                label=f"⬇️ Download All {len(pfiles)} Files as ZIP",
                                data=zip_buf.getvalue(),
                                file_name=f"{selected_user['username']}_uploaded_files.zip",
                                mime="application/zip",
                                key="dl_all_zip",
                            )

                        for pf in pfiles:
                            col1, col2, col3, col4, col5 = st.columns([3, 1, 1, 2, 1])
                            file_type_label = "📊 F&O" if pf.get("file_type") == "FNO" else "📄 EQ"
                            col1.write(pf["filename"])
                            col2.write(f"{pf['records_added']} recs")
                            col3.write(file_type_label)
                            col4.write(pf["processed_at"][:10])
                            file_bytes = download_file_content(selected_user["id"], pf["filename"])
                            if file_bytes:
                                col5.download_button(
                                    label="⬇️", data=file_bytes,
                                    file_name=pf["filename"],
                                    mime="application/vnd.ms-excel",
                                    key=f"dl_tab1_{pf['id']}"
                                )
                            else:
                                col5.caption("—")
                    else:
                        st.info("No files uploaded yet.")

                    st.divider()
                    with st.expander("📥 ScripMaster DB (Symbol/ISIN/F&O cache)", expanded=False):
                        st.caption(
                            "Upload `ScripMaster_all.csv` to enable accurate symbol→ISIN mapping "
                            "and correct F&O lot sizes."
                        )
                        try:
                            sm_stats = get_scrip_master_stats()
                            if sm_stats.get("populated"):
                                col_s1, col_s2, col_s3, col_s4 = st.columns(4)
                                col_s1.metric("Total Rows", f"{sm_stats.get('total_rows', 0):,}")
                                col_s2.metric("NSE EQ Rows", f"{sm_stats.get('nse_eq_rows', 0):,}")
                                col_s3.metric("Rows with ISIN", f"{sm_stats.get('rows_with_isin', 0):,}")
                                col_s4.metric("F&O Symbols", f"{sm_stats.get('symbols_with_lot', 0):,}")
                                st.success("✅ ScripMaster DB is populated")
                            else:
                                st.warning("⚠️ ScripMaster DB is empty")
                        except Exception as e:
                            st.error(f"Could not fetch stats: {e}")

                        st.divider()
                        st.markdown("**Option 1 — Auto-Download (recommended)**")
                        if st.button("⬇️ Download Latest ScripMaster from 5paisa", type="primary", key="sm_auto_download_btn"):
                            with st.spinner("Downloading from 5paisa (~34 MB)… please wait"):
                                try:
                                    result = download_scrip_master()
                                    st.success(f"✅ {result['message']}  |  Size: {result.get('download_size', '?')}  |  ~{result['inserted']:,} rows saved")
                                    if result.get("errors", 0) > 0:
                                        st.warning(f"{result['errors']} rows had errors (safe to ignore)")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Auto-download failed: {e}")

                        st.divider()
                        st.markdown("**Option 2 — Manual Upload**")
                        sm_file = st.file_uploader("Choose ScripMaster_all.csv", type=["csv"], key="sm_csv_uploader")
                        if st.button("⬆️ Upload & Save to DB", type="primary", disabled=(sm_file is None), key="sm_upload_btn"):
                            with st.spinner(f"Uploading {sm_file.name}…"):
                                try:
                                    result = upload_scrip_master(sm_file)
                                    st.success(f"✅ {result['message']}  |  ~{result['inserted']:,} inserted, ~{result['updated']:,} updated")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Upload failed: {e}")


                # ── Tab 2: Holdings / Stock Master ─────────────────────────────
                with tab2:
                    st.subheader("📊 Unified Portfolio View")
                    st.caption("After uploading, click below to fix lot sizes:")
                    if st.button("🔄 Refresh F&O Lot Sizes from ScripMaster DB", key="sm_refresh_fno"):
                        with st.spinner("Re-resolving F&O info…"):
                            try:
                                res = refresh_fno_from_scrip_master()
                                st.success(f"Updated {res.get('refreshed', 0)} stocks")
                                st.session_state.pop("sm_grid_data", None)
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))

                    st.caption("Or re-run Auto Populate:")
                    if _guarded_button("🔁 Re-run Auto Populate (with ScripMaster DB)", selected_user["id"], "auto_populate", key="sm_auto_pop"):
                        with st.spinner("Auto-populating stock master…"):
                            try:
                                res = auto_populate_master(selected_user["id"])
                                st.success(f"Done — added {res.get('added', 0)}, updated {res.get('updated', 0)}, unmatched {res.get('unmatched', 0)}")
                                st.session_state.pop("sm_grid_data", None)
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))

                    
                    hold_mode_col, _ = st.columns([3, 5])
                    hold_mode = hold_mode_col.radio(
                        "Holdings source",
                        ["📄 Transactions only", "🏛️ + Corporate Actions (CA‑aware)"],
                        horizontal=True,
                        key="hold_mode",
                    )
                    ca_mode = (hold_mode == "🏛️ + Corporate Actions (CA‑aware)")

                    # ── Refresh button for holdings grid ──────────────────────
                    if st.button("🔄 Refresh Holdings Grid", key="sm_grid_refresh_btn"):
                        st.session_state.pop("sm_grid_data", None)
                        st.session_state.pop(f"holdings_{_uid}", None)

                    grid_data = _lazy("sm_grid_data", get_stock_master_grid, selected_user["id"]) or []

                    holdings_lookup: dict = {}
                    raw_holdings = _lazy(f"holdings_{_uid}", get_holdings, selected_user["id"]) or []
                    for h in raw_holdings:
                        sym = str(h.get("symbol", "")).upper()
                        if sym:
                            holdings_lookup[sym] = {
                                "avg_buy_price":  h.get("avg_buy_price", 0),
                                "total_invested": h.get("total_invested", 0),
                                "first_buy_date": h.get("first_buy_date", ""),
                            }

                    ca_lookup: dict = {}
                    if ca_mode:
                        _ca_key = f"ca_aware_{_uid}"
                        if _ca_key not in st.session_state or st.session_state[_ca_key] is None:
                            with st.spinner("Computing CA‑aware holdings…"):
                                try:
                                    _ca_resp = requests.get(
                                        f"{API_BASE}/holdings/ca-aware/{selected_user['id']}",
                                        headers={"Authorization": f"Bearer {st.session_state.token}"}
                                    )
                                    _ca_resp.raise_for_status()
                                    st.session_state[_ca_key] = _ca_resp.json()
                                except Exception as e:
                                    st.error(f"CA holdings failed: {e}")
                                    st.session_state[_ca_key] = []
                        for row in (st.session_state.get(_ca_key) or []):
                            qty = float(row.get("quantity", 0) or 0)
                            if qty <= 0:
                                continue
                            _ca_isin = str(row.get("isin") or row.get("symbol", "")).upper()
                            if _ca_isin:
                                ca_lookup[_ca_isin] = {
                                    "avg_buy_price":  row.get("avg_buy_price", 0),
                                    "total_invested": row.get("total_invested", 0),
                                    "first_buy_date": row.get("first_buy_date", ""),
                                    "ca_summary":     row.get("ca_summary", "—"),
                                }

                    if not grid_data:
                        st.info("No holdings data found. Upload transaction files first.")
                    else:
                        sample = grid_data[0] if grid_data else {}
                        broker_qty_cols  = sorted([k for k in sample if k.endswith("_qty")])
                        broker_sym_cols  = sorted([k for k in sample if k.endswith("_symbol")])

                        display_rows = []
                        for g in grid_data:
                            has_qty = any(float(g.get(c, 0) or 0) > 0 for c in broker_qty_cols)
                            if not has_qty:
                                has_qty = float(g.get("total_qty", 0) or 0) > 0
                            if not has_qty:
                                continue

                            isin = str(g.get("isin", "") or "")
                            std  = g.get("standard_name", "")

                            price_data: dict = {}
                            if ca_mode:
                                price_data = (
                                    ca_lookup.get(isin.upper())
                                    or ca_lookup.get(str(std).upper())
                                    or {}
                                )
                            if not price_data:
                                for sc in broker_sym_cols:
                                    sym_val = str(g.get(sc, "") or "").upper()
                                    if sym_val and sym_val in holdings_lookup:
                                        price_data = holdings_lookup[sym_val]
                                        break

                            avg_price  = price_data.get("avg_buy_price",  "—")
                            invested   = price_data.get("total_invested",  "—")
                            first_buy  = price_data.get("first_buy_date",  "—")
                            ca_summary = price_data.get("ca_summary", "—") if ca_mode else None

                            row_out = {
                                "Company":     std,
                                "Custom Name": g.get("user_custom_name") or "—",
                            }
                            for qc, sc in zip(broker_qty_cols, broker_sym_cols):
                                qty_val = g.get(qc, 0) or 0
                                sym_val = g.get(sc, "") or "—"
                                row_out[qc] = int(float(qty_val)) if qty_val else 0
                                row_out[sc] = sym_val
                            row_out["Avg Price (₹)"]  = fmt_inr(avg_price, 2).lstrip("₹") if avg_price not in ("—", None, "") else "—"
                            row_out["Invested (₹)"]   = fmt_inr(invested, 2).lstrip("₹") if invested not in ("—", None, "") else "—"
                            row_out["First Buy"]      = first_buy or "—"
                            row_out["F&O"]            = "✅" if g.get("fno_available") else "—"
                            row_out["Lot Size"]       = int(g.get("lot_size") or 0) or "—"
                            row_out["Pending Qty"]    = int(float(g.get("pending_qty") or 0))
                            row_out["Resolved"]       = g.get("resolved", "—")
                            row_out["Last Update"]    = str(g.get("updated_at") or "—")[:10]
                            if ca_mode and ca_summary and ca_summary != "—":
                                ca_types = sorted({t for t in ["SPLIT","BONUS","MERGER","DEMERGER"] if t in str(ca_summary).upper()})
                                row_out["CA Events"] = ", ".join(ca_types) if ca_types else "—"
                            display_rows.append(row_out)

                        if not display_rows:
                            st.info("No holdings with quantity > 0.")
                        else:
                            disp = pd.DataFrame(display_rows).reset_index(drop=True)
                            disp.index = disp.index + 1
                            disp = disp.loc[:, ~disp.columns.duplicated(keep="first")]
                            st.metric("Stocks held (qty > 0)", len(disp))
                            st.dataframe(disp, use_container_width=True)

                            if ca_mode:
                                with st.expander("📋 CA Event Log"):
                                    try:
                                        resp = requests.get(
                                            f"{API_BASE}/holdings/ca-events/{selected_user['id']}",
                                            headers={"Authorization": f"Bearer {st.session_state.token}"}
                                        )
                                        resp.raise_for_status()
                                        ev_log = pd.DataFrame(resp.json())
                                        if not ev_log.empty:
                                            st.dataframe(ev_log, use_container_width=True)
                                        else:
                                            st.info("No corporate actions applied yet.")
                                    except Exception as e:
                                        st.error(f"Failed to load event log: {e}")
                    # 👈 NEW: Reconciliation Result UI (interactive diff tables)
   # ── Holdings Reconciliation Results ────────────────────────
                    st.divider()
                    if st.session_state.get("last_reconciliation_diff"):
                        render_reconciliation_results(
                            user_id=_uid,
                            token=st.session_state.token,
                            diff=st.session_state.last_reconciliation_diff
                        )
                        st.markdown("---")
                    else:
                        st.info(
                            "💡 **No holdings reconciliation yet.** "
                            "Upload a broker holdings file in the **Upload & Manage** tab to compare."
                        )
                    
                    st.divider()
                    with st.expander("✏️ Rename a Stock (Custom Name)"):
                        all_rows = st.session_state.get("sm_grid_data") or []
                        if all_rows:
                            name_options = {
                                r["isin"]: f"{r.get('user_custom_name') or r['standard_name']} ({r['isin']})"
                                for r in all_rows if r.get("isin")
                            }
                            if name_options:
                                selected_label = st.selectbox("Select stock", list(name_options.values()), key="rename_select")
                                selected_isin = [k for k, v in name_options.items() if v == selected_label][0]
                                current_custom = next((r.get("user_custom_name", "") for r in all_rows if r["isin"] == selected_isin), "")
                                new_name = st.text_input("Custom name", value=current_custom, key="rename_input")
                                if st.button("💾 Save Name", key="save_name"):
                                    try:
                                        rename_stock(selected_isin, new_name)
                                        st.session_state.pop("sm_grid_data", None)
                                        st.success("Custom name updated")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(str(e))
                                if st.button("✖ Clear Custom Name", key="clear_name"):
                                    try:
                                        rename_stock(selected_isin, "")
                                        st.session_state.pop("sm_grid_data", None)
                                        st.success("Custom name cleared")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(str(e))

                    st.divider()
                    st.subheader("📅 Historical Holdings — FY Snapshot")
                    with st.expander("📐 Column Guide"):
                        st.markdown("""
| Column | What it means |
|---|---|
| **Symbol** | Ticker symbol |
| **Company** | Company name |
| **Exchange** | NSE or BSE |
| **Segment** | EQ = Equity |
| **Qty** | Shares held as of the FY end date |
| **Avg Price (₹)** | Average buy price at that point in time |
| **Invested (₹)** | Total investment value at that FY end |
""")
                    fy_list = [f"FY {y}-{str(y+1)[-2:]}" for y in range(date.today().year, 2015, -1)]
                    sel_fy = st.selectbox("Select FY", fy_list, key="fy_select")
                    as_of = f"{int(sel_fy.split('-')[0].split()[-1])+1}-03-31"
                    _fy_key = f"fy_holdings_{_uid}_{as_of}"
                    fy_df = _lazy(_fy_key, get_fy_holdings, selected_user["id"], as_of)
                    if not fy_df:
                        st.info(f"No holdings as of {as_of}.")
                    else:
                        st.dataframe(pd.DataFrame(fy_df), use_container_width=True)

                    st.divider()
                    st.subheader("Unresolved Symbols")
                    _unres_key = f"unresolved_{_uid}"
                    if st.button("🔄 Refresh Unresolved", key="unres_refresh_btn"):
                        st.session_state.pop(_unres_key, None)
                    if _unres_key not in st.session_state or st.session_state[_unres_key] is None:
                        try:
                            st.session_state[_unres_key] = requests.get(
                                f"{API_BASE}/stock-master/unresolved-holdings/{selected_user['id']}",
                                headers={"Authorization": f"Bearer {st.session_state.token}"}
                            ).json()
                        except Exception:
                            st.session_state[_unres_key] = []
                    unresolved = st.session_state.get(_unres_key) or []
                    if not unresolved:
                        st.success("All symbols resolved!")
                    else:
                        st.warning(f"{len(unresolved)} unresolved holding(s)")
                        udf = pd.DataFrame(unresolved)
                        st.dataframe(udf[["symbol", "company_name", "quantity"]], use_container_width=True)
                        with st.form("manual_link_form"):
                            sel_sym = st.selectbox("Symbol to link", udf["symbol"].tolist())
                            sel_broker = st.selectbox("Broker", ["5paisa", "Zerodha", "IIFL", "Manual"])
                            isin_input = st.text_input("ISIN (e.g., INE001A01036)").strip().upper()
                            submit = st.form_submit_button("🔗 Link")
                            if submit and isin_input:
                                try:
                                    res = requests.post(
                                        f"{API_BASE}/stock-master/link",
                                        params={"user_id": selected_user["id"], "raw_symbol": sel_sym,
                                                "broker": sel_broker, "isin": isin_input},
                                        headers={"Authorization": f"Bearer {st.session_state.token}"}
                                    )
                                    res.raise_for_status()
                                    st.success(f"Linked {sel_sym} to {isin_input}")
                                    st.session_state.pop("sm_grid_data", None)
                                    st.session_state.pop(f"unresolved_{_uid}", None)
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))

                    st.divider()
                    st.subheader("📋 Corporate Actions")
                    _ca_actions_key = f"corp_actions_{_uid}"
                    _ca_btn_col1, _ca_btn_col2, _ca_btn_col3 = st.columns(3)
                    if _ca_btn_col1.button("🌱 Seed from Transactions", key="ca_seed"):
                        with st.spinner("Seeding..."):
                            try:
                                res = seed_corp_actions(selected_user["id"])
                                st.success(f"Inserted {res['inserted']}, skipped {res['skipped']}")
                                st.session_state.pop(_ca_actions_key, None)
                            except Exception as e:
                                st.error(str(e))
                    if _ca_btn_col2.button("📡 Sync from NSE", key="ca_nse"):
                        with st.spinner("Fetching from NSE..."):
                            try:
                                res = sync_nse_corp_actions(selected_user["id"])
                                st.success(f"Fetched {res['fetched']} stocks, inserted {res['inserted']} events")
                                st.session_state.pop(_ca_actions_key, None)
                            except Exception as e:
                                st.error(str(e))
                    if _ca_btn_col3.button("🔄 Refresh", key="ca_refresh"):
                        st.session_state.pop(_ca_actions_key, None)

                    ca_actions_data = _lazy(_ca_actions_key, get_corp_actions, selected_user["id"])
                    if ca_actions_data:
                        _ca_df = pd.DataFrame(ca_actions_data)
                        _ca_df = _ca_df[["ex_date", "symbol", "action_type", "company_name", "source", "notes"]]
                        _ca_df.columns = ["Ex-Date", "Symbol", "Type", "Company", "Source", "Notes"]
                        st.dataframe(_ca_df, use_container_width=True)
                    else:
                        st.info("No corporate actions found.")
                    with st.expander("➕ Add Manually", expanded=False):
                        with st.form("ca_form"):
                            ca_sym = st.text_input("Symbol")
                            ca_type = st.selectbox("Type", ["BONUS","SPLIT","DIVIDEND","DEMERGER","MERGER","TRANSFER","OTHER"])
                            ca_ex = st.date_input("Ex-Date")
                            ca_detail = st.text_input("Details (e.g., ratio '1:1')")
                            if st.form_submit_button("Save"):
                                try:
                                    result = add_manual_corp_action(selected_user["id"], {
                                        "symbol": ca_sym, "action_type": ca_type,
                                        "ex_date": str(ca_ex),
                                        "action_details": {"ratio": ca_detail} if ca_detail else {},
                                        "company_name": ca_sym, "notes": ""
                                    })
                                    st.success("Added")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))

                # with tab2b:
                #     render_holdings_reconciliation_tab(
                #         user_id=selected_user["id"],
                #         token=st.session_state.token,
                #     )
                # # ── Tab 3: Transactions ────────────────────────────────────────
                with tab3:
                    st.subheader("All Transactions")
                    if st.button("Refresh Transactions", key="txn_refresh_btn"):
                        st.session_state.pop("txn_data", None)
                    data = _lazy("txn_data", get_transactions, selected_user["id"])
                    if data:
                        df = pd.DataFrame(data)
                        df = df[["trade_date", "symbol", "trade_type", "quantity", "price", "brokerage"]]
                        df.columns = ["Date", "Symbol", "Type", "Qty", "Price", "Brokerage"]
                        st.dataframe(df, use_container_width=True)
                    else:
                        st.info("No transactions yet. Upload files in the Upload & Manage tab.")

                    st.divider()
                    st.subheader("📊 F&O Transactions")
                    if st.button("Refresh F&O Transactions", key="fno_txn_refresh"):
                        st.session_state.pop("fno_txn_data", None)
                    fno_txn_data = _lazy("fno_txn_data", get_fno_transactions, selected_user["id"])
                    if fno_txn_data:
                        fdf = pd.DataFrame(fno_txn_data)
                        fdf = fdf[["trade_date", "underlying", "instrument_type", "expiry_date",
                                   "strike_price", "trade_type", "quantity", "price", "broker"]]
                        fdf.columns = ["Date", "Underlying", "Type", "Expiry", "Strike", "B/S", "Qty", "Price", "Broker"]
                        st.dataframe(fdf, use_container_width=True)
                    else:
                        st.info("No F&O transactions.")

                # ── Tab 4: P&L ─────────────────────────────────────────────────
                with tab4:
                    import time as _t_mod

                    # ── Auto-refresh every 5 min in background ─────────────────
                    _pnl_last_fetch  = st.session_state.get("_unreal_last_fetch", 0)
                    _pnl_now         = _t_mod.time()
                    _pnl_five_min    = 5 * 60

                    def _do_fetch_unrealized(uid, holdings_list=None):
                        """Fetch live prices + compute unrealized rows; store in session_state."""
                        try:
                            hlist = holdings_list or get_holdings(uid)
                            if not hlist:
                                st.session_state["_unreal_rows"]       = []
                                st.session_state["_total_unrealized"]  = 0.0
                                st.session_state["_unreal_last_fetch"] = _t_mod.time()
                                return
                            sm_data       = st.session_state.get("sm_grid_data") or []
                            canonical_map = {}
                            for r in sm_data:
                                if r.get("canonical_symbol"):
                                    for k in ("fivepaisa_symbol", "zerodha_symbol", "iifl_symbol"):
                                        if r.get(k):
                                            canonical_map[r[k]] = r["canonical_symbol"]
                            symbols     = [canonical_map.get(h["symbol"], h["symbol"]) for h in hlist]
                            prices_data = fetch_prices_with_change(symbols)
                            rows          = []
                            total_unreal  = 0.0
                            for h, fetch_sym in zip(hlist, symbols):
                                sym    = h["symbol"]
                                _pdata = prices_data.get(fetch_sym) or prices_data.get(sym) or {}
                                raw_cmp = _pdata.get("price", 0) if isinstance(_pdata, dict) else 0
                                try:
                                    qty = float(h["quantity"] or 0)
                                    qty = 0.0 if qty != qty else qty
                                except (TypeError, ValueError):
                                    qty = 0.0
                                try:
                                    avg = float(h["avg_buy_price"] or 0)
                                    avg = 0.0 if avg != avg else avg
                                except (TypeError, ValueError):
                                    avg = 0.0
                                try:
                                    cmp = float(raw_cmp or 0)
                                    cmp = 0.0 if cmp != cmp else cmp
                                except (TypeError, ValueError):
                                    cmp = 0.0
                                unreal     = round((cmp - avg) * qty, 2) if cmp > 0 else 0.0
                                pct_change = float(_pdata.get("pct_change", 0) or 0) if isinstance(_pdata, dict) else 0.0
                                total_unreal += unreal
                                rows.append({
                                    "Symbol":              sym,
                                    "Canonical":           fetch_sym if fetch_sym != sym else "—",
                                    "Qty":                 qty,
                                    "Avg Cost":            avg,
                                    "CMP":                 cmp,
                                    "% Change":            pct_change,
                                    "Unrealized P&L (₹)":  unreal,
                                })
                            st.session_state["_unreal_rows"]       = rows
                            st.session_state["_total_unrealized"]  = total_unreal
                            st.session_state["_unreal_last_fetch"] = _t_mod.time()
                        except Exception as e:
                            st.error(f"Price fetch error: {e}")

                    # ── Auto-load on first open (no button click needed) ────────
                    _unreal_rows = st.session_state.get("_unreal_rows")
                    if _unreal_rows is None:
                        with st.spinner("📡 Fetching live prices for your holdings…"):
                            _do_fetch_unrealized(_uid)
                        st.rerun()

                    # ── Background auto-refresh every 5 min ────────────────────
                    if (_pnl_now - _pnl_last_fetch) >= _pnl_five_min and st.session_state.get("_unreal_rows") is not None:
                        import threading as _pnl_thread
                        def _bg_pnl_refresh():
                            try:
                                hlist       = get_holdings(_uid)
                                sm_data     = st.session_state.get("sm_grid_data") or []
                                cmap        = {}
                                for r in sm_data:
                                    if r.get("canonical_symbol"):
                                        for k in ("fivepaisa_symbol", "zerodha_symbol", "iifl_symbol"):
                                            if r.get(k):
                                                cmap[r[k]] = r["canonical_symbol"]
                                syms        = [cmap.get(h["symbol"], h["symbol"]) for h in hlist]
                                prices_data = fetch_prices_with_change(syms)
                                rows = []
                                total = 0.0
                                for h, fs in zip(hlist, syms):
                                    _pd  = prices_data.get(fs) or prices_data.get(h["symbol"]) or {}
                                    try:
                                        qty = float(h["quantity"] or 0); qty = 0.0 if qty!=qty else qty
                                    except: qty = 0.0
                                    try:
                                        avg = float(h["avg_buy_price"] or 0); avg = 0.0 if avg!=avg else avg
                                    except: avg = 0.0
                                    try:
                                        cmp = float(_pd.get("price",0) or 0); cmp = 0.0 if cmp!=cmp else cmp
                                    except: cmp = 0.0
                                    unreal = round((cmp-avg)*qty, 2) if cmp>0 else 0.0
                                    total += unreal
                                    rows.append({
                                        "Symbol": h["symbol"],
                                        "Canonical": fs if fs!=h["symbol"] else "—",
                                        "Qty": qty, "Avg Cost": avg, "CMP": cmp,
                                        "% Change": float(_pd.get("pct_change",0) or 0),
                                        "Unrealized P&L (₹)": unreal,
                                    })
                                st.session_state["_unreal_rows"]       = rows
                                st.session_state["_total_unrealized"]  = total
                                st.session_state["_unreal_last_fetch"] = _t_mod.time()
                            except Exception:
                                pass
                        _pnl_thread.Thread(target=_bg_pnl_refresh, daemon=True).start()
                        st.session_state["_unreal_last_fetch"] = _pnl_now  # prevent double-trigger

                    # ══════════════════════════════════════════════════════════
                    # SECTION 1 — UNREALIZED P&L  (shown first, like a live terminal)
                    # ══════════════════════════════════════════════════════════
                    _elapsed_sec = int(_pnl_now - st.session_state.get("_unreal_last_fetch", 0))
                    _age_str     = "just now" if _elapsed_sec < 60 else (
                        f"{_elapsed_sec // 60}m ago" if _elapsed_sec < 3600 else f"{_elapsed_sec // 3600}h ago"
                    )
                    _next_sec    = max(0, _pnl_five_min - _elapsed_sec)
                    _next_str    = f"{_next_sec // 60}m {_next_sec % 60}s" if _next_sec > 0 else "any moment"

                    _rc1, _rc2, _rc3 = st.columns([2, 2, 4])
                    with _rc1:
                        st.subheader("💹 Live Holdings P&L")
                    with _rc2:
                        if st.button("🔄 Refresh Now", key="fetch_unreal"):
                            with st.spinner("Fetching latest prices…"):
                                _do_fetch_unrealized(_uid)
                            st.rerun()
                    with _rc3:
                        st.caption(f"⏱ Updated {_age_str} · next auto-refresh in {_next_str}")

                    _unreal_rows = st.session_state.get("_unreal_rows")
                    if _unreal_rows:
                        _total_unrealized = st.session_state.get("_total_unrealized", 0.0)

                        # ── Summary metrics bar ──────────────────────────────
                        _profitable   = [r for r in _unreal_rows if r["Unrealized P&L (₹)"] > 0]
                        _losing       = [r for r in _unreal_rows if r["Unrealized P&L (₹)"] < 0]
                        _mc1, _mc2, _mc3, _mc4 = st.columns(4)
                        _sign  = "+" if _total_unrealized >= 0 else ""
                        _color = "#6fcf97" if _total_unrealized >= 0 else "#f48fb1"
                        _mc1.markdown(
                            f"<div style='background:#1a1a2e;padding:12px;border-radius:8px;"
                            f"border-left:4px solid {_color};'>"
                            f"<div style='color:#888;font-size:11px;'>Total Unrealized P&L</div>"
                            f"<div style='color:{_color};font-size:20px;font-weight:800;'>"
                            f"{_sign}₹{abs(_total_unrealized):,.0f}</div></div>",
                            unsafe_allow_html=True
                        )
                        _mc2.metric("Stocks", len(_unreal_rows))
                        _mc3.metric("In Profit 🟢", len(_profitable))
                        _mc4.metric("In Loss 🔴", len(_losing))

                        st.markdown("")

                        # Sort controls
                        _sort_col = st.selectbox(
                            "Sort by",
                            ["Symbol", "Qty", "Avg Cost", "CMP", "% Change", "Unrealized P&L (₹)"],
                            key="unreal_sort_col"
                        )
                        _sort_asc = st.radio(
                            "Order", ["▲ Ascending", "▼ Descending"],
                            horizontal=True, key="unreal_sort_order"
                        ) == "▲ Ascending"

                        df_unreal = pd.DataFrame(_unreal_rows)
                        df_unreal = df_unreal.sort_values(_sort_col, ascending=_sort_asc, na_position="last")

                        def _color_pct(val):
                            try:
                                v = float(val)
                                if v > 0: return "color:#6fcf97;font-weight:bold"
                                if v < 0: return "color:#f48fb1;font-weight:bold"
                            except Exception:
                                pass
                            return ""

                        df_display = df_unreal.copy()
                        df_display["Avg Cost"] = df_display["Avg Cost"].apply(
                            lambda v: fmt_inr(v).lstrip("₹") if v else "—")
                        df_display["CMP"] = df_display["CMP"].apply(
                            lambda v: fmt_inr(v).lstrip("₹") if v else "—")
                        df_display["% Change"] = df_display["% Change"].apply(
                            lambda v: f"{float(v):+.2f}%" if v == v else "—")
                        df_display["Unrealized P&L (₹)"] = df_display["Unrealized P&L (₹)"].apply(
                            lambda v: f"₹{float(v):+,.2f}" if v == v else "—")

                        try:
                            styled = df_display.style \
                                .map(_color_pct, subset=["% Change"]) \
                                .map(_color_pct, subset=["Unrealized P&L (₹)"])
                        except AttributeError:
                            styled = df_display.style \
                                .applymap(_color_pct, subset=["% Change"]) \
                                .applymap(_color_pct, subset=["Unrealized P&L (₹)"])

                        st.dataframe(styled, use_container_width=True)
                    else:
                        st.info("No holdings data found. Upload equity transaction files first.")

                    st.divider()

                    # ══════════════════════════════════════════════════════════
                    # SECTION 2 — INTRADAY TRADES  (day-trading summary)
                    # ══════════════════════════════════════════════════════════
                    st.subheader("⚡ Intraday Trades — FY Snapshot")
                    
                    # FY Selector
                    fy_list = get_fy_list(current_year=date.today().year, years_back=10)
                    fy_options = [fy["fy_label"] for fy in fy_list]
                    sel_fy_intraday = st.selectbox(
                        "Select FY (Intraday)", 
                        fy_options, 
                        key="fy_select_intraday"
                    )
                    
                    # Get FY end date for selected FY
                    sel_fy_data = next(fy for fy in fy_list if fy["fy_label"] == sel_fy_intraday)
                    fy_end_intraday = sel_fy_data["end_date"]
                    
                    if st.button("🔄 Refresh Intraday", key="intraday_refresh_pnl"):
                        st.session_state.pop(f"intraday_data_fy_{fy_end_intraday}", None)
                    
                    _intra_key = f"intraday_data_fy_{fy_end_intraday}_{selected_user['id']}"
                    intra = _lazy(_intra_key, get_fy_intraday, selected_user["id"], fy_end_intraday)
                    intra_summary = _lazy(f"intraday_summary_fy_{fy_end_intraday}_{selected_user['id']}", 
                                         get_fy_intraday_summary, selected_user["id"], fy_end_intraday)
                    
                    if intra is not None and len(intra) > 0:
                        idf = pd.DataFrame(intra)
                        
                        # ── Intraday summary metrics ──────────────────────────
                        _total_intra = intra_summary.get("total_intraday_pnl", 0)
                        _winning_trades = intra_summary.get("winning_intraday", 0)
                        _losing_trades = intra_summary.get("losing_intraday", 0)
                        _total_trades = intra_summary.get("num_intraday_trades", 0)
                        _win_rate = intra_summary.get("intraday_win_rate", 0)
                        _best_trade = intra_summary.get("best_intraday", 0)
                        
                        _ic1, _ic2, _ic3, _ic4 = st.columns(4)
                        _isign = "+" if _total_intra >= 0 else ""
                        _icolor = "#6fcf97" if _total_intra >= 0 else "#f48fb1"
                        
                        _ic1.markdown(
                            f"<div style='background:#1a1a2e;padding:12px;border-radius:8px;"
                            f"border-left:4px solid {_icolor};'>"
                            f"<div style='color:#888;font-size:11px;'>Total Intraday P&L</div>"
                            f"<div style='color:{_icolor};font-size:20px;font-weight:800;'>"
                            f"{_isign}₹{abs(_total_intra):,.0f}</div></div>",
                            unsafe_allow_html=True
                        )
                        _ic2.metric("Total Trades", _total_trades)
                        _ic3.metric("Win Rate", f"{_win_rate}%", 
                                   delta=f"W:{_winning_trades} L:{_losing_trades}")
                        _ic4.metric("Best Trade", f"₹{_best_trade:,.0f}")
                        
                        st.markdown("")
                        
                        # ── Full intraday table ────────────────────────────────
                        idf_disp = idf.copy()
                        idf_disp.columns = ["Date", "Symbol", "Qty", "Buy (₹)", "Sell (₹)", "P&L (₹)"]
                        idf_disp["Buy (₹)"] = pd.to_numeric(idf_disp["Buy (₹)"], errors="coerce").apply(
                            lambda v: fmt_inr(v).lstrip("₹") if pd.notna(v) else "—")
                        idf_disp["Sell (₹)"] = pd.to_numeric(idf_disp["Sell (₹)"], errors="coerce").apply(
                            lambda v: fmt_inr(v).lstrip("₹") if pd.notna(v) else "—")
                        
                        _raw_pnl = pd.to_numeric(idf["P&L (₹)"], errors="coerce").fillna(0)
                        idf_disp["P&L (₹)"] = _raw_pnl.apply(lambda v: f"₹{v:+,.2f}")
                        
                        def _color_intra_pnl(val):
                            try:
                                v = float(str(val).replace("₹","").replace(",",""))
                                if v > 0: return "color:#6fcf97;font-weight:bold"
                                if v < 0: return "color:#f48fb1;font-weight:bold"
                            except Exception:
                                pass
                            return ""
                        
                        try:
                            idf_styled = idf_disp.style.map(_color_intra_pnl, subset=["P&L (₹)"])
                        except AttributeError:
                            idf_styled = idf_disp.style.applymap(_color_intra_pnl, subset=["P&L (₹)"])
                        
                        st.dataframe(idf_styled, use_container_width=True, hide_index=True)
                        st.caption(
                            f"💡 Intraday trades in {sel_fy_intraday} (as of {fy_end_intraday}). "
                            "P&L shown is gross (before brokerage & taxes)."
                        )
                    else:
                        st.info(f"No intraday trades found in {sel_fy_intraday}.")
                    
                    st.divider()

                    # ══════════════════════════════════════════════════════════
                    # SECTION 3 — REALIZED P&L  (historical closed positions)
                    # ══════════════════════════════════════════════════════════
                   
                    st.subheader("📈 Realized P&L — FY Snapshot")
                    
                    # FY Selector
                    fy_list = get_fy_list(current_year=date.today().year, years_back=10)
                    fy_options = [fy["fy_label"] for fy in fy_list]
                    sel_fy_pnl = st.selectbox(
                        "Select FY (Realized P&L)", 
                        fy_options, 
                        key="fy_select_pnl"
                    )
                    
                    # Get FY end date for selected FY
                    sel_fy_data = next(fy for fy in fy_list if fy["fy_label"] == sel_fy_pnl)
                    fy_end_pnl = sel_fy_data["end_date"]
                    
                    if st.button("Refresh P&L", key="pnl_refresh_btn"):
                        st.session_state.pop(f"pnl_data_fy_{fy_end_pnl}", None)
                    
                    _pnl_key = f"pnl_data_fy_{fy_end_pnl}_{selected_user['id']}"
                    data = _lazy(_pnl_key, get_fy_realized_pnl, selected_user["id"], fy_end_pnl)
                    pnl_summary = _lazy(f"pnl_summary_fy_{fy_end_pnl}_{selected_user['id']}", 
                                       get_fy_realized_pnl_summary, selected_user["id"], fy_end_pnl)
                    
                    if data is not None and len(data) > 0:
                        df = pd.DataFrame(data)
                        
                        # ── Realized P&L summary metrics ──────────────────────
                        _total_gross = pnl_summary.get("total_gross_pnl", 0)
                        _total_net = pnl_summary.get("total_net_pnl", 0)
                        _num_trades = pnl_summary.get("num_trades", 0)
                        _winning = pnl_summary.get("winning_trades", 0)
                        _losing = pnl_summary.get("losing_trades", 0)
                        _avg_profit = pnl_summary.get("avg_profit", 0)
                        _avg_loss = pnl_summary.get("avg_loss", 0)
                        
                        _rp1, _rp2, _rp3 = st.columns(3)
                        _rsign = "+" if _total_gross >= 0 else ""
                        _rcolor = "#6fcf97" if _total_gross >= 0 else "#f48fb1"
                        
                        _rp1.markdown(
                            f"<div style='background:#1a1a2e;padding:12px;border-radius:8px;"
                            f"border-left:4px solid {_rcolor};'>"
                            f"<div style='color:#888;font-size:11px;'>Gross Realized P&L</div>"
                            f"<div style='color:{_rcolor};font-size:20px;font-weight:800;'>"
                            f"{_rsign}₹{abs(_total_gross):,.0f}</div></div>",
                            unsafe_allow_html=True
                        )
                        _rp2.metric("Net P&L (after tax)", fmt_inr(_total_net))
                        _rp3.metric("Closed Positions", _num_trades)
                        
                        st.markdown("")
                        
                        # Additional metrics row
                        _rp4, _rp5, _rp6 = st.columns(3)
                        _rp4.metric("Winning Trades", _winning)
                        _rp5.metric("Losing Trades", _losing)
                        _rp6.metric("Win Rate", f"{round(_winning/_num_trades*100) if _num_trades > 0 else 0}%")
                        
                        st.markdown("")
                        
                        df_r = df.copy()
                        
                        def _color_realized(val):
                            try:
                                v = float(str(val).replace(",",""))
                                if v > 0: return "color:#6fcf97;font-weight:bold"
                                if v < 0: return "color:#f48fb1;font-weight:bold"
                            except Exception:
                                pass
                            return ""
                        
                        try:
                            df_r_styled = df_r.style \
                                .map(_color_realized, subset=["Gross P&L"]) \
                                .map(_color_realized, subset=["Net P&L"])
                        except AttributeError:
                            df_r_styled = df_r.style \
                                .applymap(_color_realized, subset=["Gross P&L"]) \
                                .applymap(_color_realized, subset=["Net P&L"])
                        
                        st.dataframe(df_r_styled, use_container_width=True)
                        st.caption(f"📊 Realized P&L data for {sel_fy_pnl} (as of {fy_end_pnl})")
                    else:
                        st.info(f"No realized P&L found in {sel_fy_pnl}.")
                
                    st.divider()
                    
                    st.subheader("🔁 Transactions — FY Snapshot")
                    
                    # FY Selector
                    fy_list = get_fy_list(current_year=date.today().year, years_back=10)
                    fy_options = [fy["fy_label"] for fy in fy_list]
                    sel_fy_txn = st.selectbox(
                        "Select FY (Transactions)", 
                        fy_options, 
                        key="fy_select_txn"
                    )
                    
                    # Get FY end date for selected FY
                    sel_fy_data = next(fy for fy in fy_list if fy["fy_label"] == sel_fy_txn)
                    fy_end_txn = sel_fy_data["end_date"]
                    
                    # Segment filter
                    txn_segment = st.selectbox("Segment", ["All", "EQ", "FNO"], key="txn_segment")
                    seg_param = None if txn_segment == "All" else txn_segment
                    
                    if st.button("Refresh Transactions", key="fy_txn_refresh_btn"):
                        st.session_state.pop(f"txn_data_fy_{fy_end_txn}", None)
                    
                    _txn_key = f"txn_data_fy_{fy_end_txn}_{selected_user['id']}"
                    txn_data = _lazy(_txn_key, get_fy_transactions, selected_user["id"], fy_end_txn, seg_param)
                    txn_summary = _lazy(f"txn_summary_fy_{fy_end_txn}_{selected_user['id']}", 
                                       get_fy_transactions_summary, selected_user["id"], fy_end_txn, seg_param)
                    
                    if txn_data is not None and len(txn_data) > 0:
                        txn_df = pd.DataFrame(txn_data)
                        
                        # ── Transaction summary metrics ──────────────────────
                        _total_txn = txn_summary.get("total_transactions", 0)
                        _total_buys = txn_summary.get("total_buys", 0)
                        _total_sells = txn_summary.get("total_sells", 0)
                        _buy_value = txn_summary.get("total_buy_value", 0)
                        _sell_value = txn_summary.get("total_sell_value", 0)
                        
                        _tx1, _tx2, _tx3, _tx4 = st.columns(4)
                        _tx1.metric("Total Transactions", _total_txn)
                        _tx2.metric("Buy Orders", _total_buys)
                        _tx3.metric("Sell Orders", _total_sells)
                        _tx4.metric("Net Invested", fmt_inr(_buy_value - _sell_value))
                        
                        st.markdown("")
                        
                        # Display transactions
                        txn_disp = txn_df[["trade_date", "trade_type", "symbol", "quantity", "price", "exchange"]].copy()
                        txn_disp.columns = ["Date", "Type", "Symbol", "Qty", "Price (₹)", "Exchange"]
                        
                        st.dataframe(txn_disp, use_container_width=True, hide_index=True)
                        st.caption(f"📋 Transactions in {sel_fy_txn} ({txn_segment} segment) as of {fy_end_txn}")
                    else:
                        st.info(f"No transactions found in {sel_fy_txn} for {txn_segment} segment.")
                
                
                
                
                # ── Tab 5: F&O Positions & P&L ─────────────────────────────────
# ── Tab 5: F&O Positions & P&L ─────────────────────────────────────────────
# REPLACE the entire `with tab5:` block in app.py with this code.
# Key fixes:
#   1. No "include expired" button — only open (non-expired) positions shown
#   2. FUT qty shown as plain signed integer (e.g. +550, -700) — no L/S prefix
#   3. Previously cached data shown immediately; Refresh fetches new data
#   4. Dependency hint shown only if no F&O transactions have been uploaded
#   5. CE/PE screener only needs to know IF position exists — no sign confusion

                with tab5:
                    # ── Dividend Adjustment Notification Banner ────────────────────────────
                    _adj_cache_key = f"div_adj_pending_{_uid}"

                    # Fast load from DB on first visit (no 5paisa API call)
                    if _adj_cache_key not in st.session_state:
                        try:
                            st.session_state[_adj_cache_key] = get_pending_adjustments_stored(_uid)
                        except Exception:
                            st.session_state[_adj_cache_key] = []

                    _pending_adjs = st.session_state.get(_adj_cache_key) or []

                    # Refresh button re-runs full detection (with 5paisa spot prices)
                    _adj_col1, _adj_col2 = st.columns([3, 1])
                    with _adj_col2:
                        if st.button("🔍 Detect Dividend Adjustments", key="div_adj_detect_btn",
                                     help="Runs detection engine — checks 5paisa for current spot prices"):
                            with st.spinner("Detecting dividend adjustments…"):
                                try:
                                    st.session_state[_adj_cache_key] = detect_pending_adjustments(_uid)
                                    _pending_adjs = st.session_state[_adj_cache_key]
                                    # Also refresh stale-check since new adjustments may have been found
                                    st.session_state.pop(f"stale_fno_{_uid}", None)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Detection error: {e}")

                    if _pending_adjs:
                        st.warning(
                            f"⚠️ **{len(_pending_adjs)} dividend adjustment(s) pending** — "
                            f"your F&O strikes/quantities may need updating. Review below."
                        )
                        with st.expander(
                            f"⚠️ Dividend Adjustments Pending ({len(_pending_adjs)})",
                            expanded=True,
                        ):
                            st.caption(
                                "SEBI mandates strike & lot-size adjustments when dividend > 10% of spot. "
                                "**Apply (Auto)** inserts P&L-neutral synthetic trades. "
                                "**I'll Upload** marks it skipped so you can upload the broker's adjusted file."
                            )
                            for _adj in _pending_adjs:
                                _cols = st.columns([2, 1, 1, 1, 1, 1, 1, 1, 1, 1])
                                _cols[0].markdown(f"**{_adj['underlying']}** {_adj['instrument_type']}")
                                _cols[1].markdown(f"Ex: `{_adj['ex_date']}`")
                                _cols[2].markdown(f"Div: ₹`{_adj['dividend_amount']:.2f}`")
                                _cols[3].markdown(f"S_prev: ₹`{_adj['spot_prev']:.0f}`")
                                _cols[4].markdown(f"Expiry: `{_adj.get('expiry_date','')}`")
                                _cols[5].markdown(
                                    f"Strike: `{_adj['old_strike']:.0f}` → **`{_adj['new_strike']:.0f}`**"
                                )
                                _cols[6].markdown(
                                    f"Qty: `{_adj['old_qty']:.0f}` → **`{_adj['new_qty']:.0f}`**"
                                )
                                _cols[7].markdown(
                                    f"Scenario: **{'A (future)' if _adj.get('scenario')=='A' else 'B (backfill)'}**"
                                )
                                _apply_key = f"apply_adj_{_adj['id']}"
                                _skip_key  = f"skip_adj_{_adj['id']}"
                                if _cols[8].button("✅ Apply", key=_apply_key,
                                                   help="Insert P&L-neutral synthetic adjustment"):
                                    with st.spinner("Applying adjustment…"):
                                        try:
                                            _res = apply_fno_adjustment(_adj["id"], _uid)
                                            if _res.get("status") == "success":
                                                st.success(f"✅ {_res['message']}")
                                                for _ck in [_adj_cache_key, "fno_pos_data",
                                                            "fno_pnl_data", "cc_analysis_data",
                                                            "fno_txn_data", f"stale_fno_{_uid}"]:
                                                    st.session_state.pop(_ck, None)
                                                st.rerun()
                                            else:
                                                st.error(_res.get("message", "Unknown error"))
                                        except Exception as _e:
                                            st.error(str(_e))

                                if _cols[9].button("📁 Upload", key=_skip_key,
                                                   help="I'll upload adjusted trades from broker"):
                                    try:
                                        skip_fno_adjustment(_adj["id"], _uid)
                                        st.info(
                                            "Marked as SKIPPED. Upload your broker's adjusted F&O file "
                                            "in the **Upload & Manage** tab."
                                        )
                                        st.session_state.pop(_adj_cache_key, None)
                                        st.session_state.pop(f"stale_fno_{_uid}", None)
                                        st.rerun()
                                    except Exception as _e:
                                        st.error(str(_e))

                    # ─────────────────────────────────────────────────────────────────────
                    # ── Stale / At-Risk Positions Panel (NEW) ────────────────────────────
                    # Shows:
                    #   • At-Risk   — open positions facing an upcoming adjustment (PENDING)
                    #   • Stale Txns — post-ex-date trades still at the old wrong strike
                    # ─────────────────────────────────────────────────────────────────────
                    _stale_cache_key = f"stale_fno_{_uid}"

                    # Auto-load once per session (pure DB query — very fast)
                    if _stale_cache_key not in st.session_state:
                        try:
                            _sresp = requests.get(
                                f"{API_BASE}/fno/stale-positions/{selected_user['id']}",
                                headers={"Authorization": f"Bearer {st.session_state.token}"}
                            )
                            st.session_state[_stale_cache_key] = (
                                _sresp.json() if _sresp.status_code == 200 else {}
                            )
                        except Exception:
                            st.session_state[_stale_cache_key] = {}

                    _stale_data    = st.session_state.get(_stale_cache_key) or {}
                    _stale_summary = _stale_data.get("summary", {})
                    _stale_txns    = _stale_data.get("stale_transactions", [])
                    _at_risk       = _stale_data.get("at_risk_positions", [])
                    _total_issues  = _stale_summary.get("total_issues", 0)

                    # Refresh button (top-right, inline with other buttons)
                    _sc1, _sc2 = st.columns([4, 1])
                    with _sc2:
                        if st.button("🔄 Refresh Stale Check", key="stale_refresh_btn",
                                     help="Re-check for stale strikes in uploaded transactions"):
                            st.session_state.pop(_stale_cache_key, None)
                            st.rerun()

                    if _total_issues > 0:
                        # ── Top-level error banner ────────────────────────────────────────
                        _banner_parts = []
                        if _stale_summary.get("at_risk_count", 0):
                            _banner_parts.append(
                                f"**{_stale_summary['at_risk_count']} open position(s)** face an upcoming adjustment"
                            )
                        if _stale_summary.get("stale_count", 0):
                            _banner_parts.append(
                                f"**{_stale_summary['stale_count']} transaction row(s)** still use the wrong pre-dividend strike"
                            )
                        st.error("⚠️ Dividend adjustment issues: " + " | ".join(_banner_parts))

                        # ── Panel A: At-Risk Open Positions ──────────────────────────────
                        if _at_risk:
                            with st.expander(
                                f"🚨 At-Risk Open Positions ({len(_at_risk)}) — adjustment needed before ex-date",
                                expanded=True,
                            ):
                                st.caption(
                                    "These positions are currently open and face a SEBI-mandated "
                                    "dividend strike / qty adjustment. Use **Apply** above or upload "
                                    "adjusted trades from your broker."
                                )
                                _ar_df = pd.DataFrame(_at_risk)
                                _col_map_ar = {
                                    "underlying":      "Underlying",
                                    "instrument_type": "Type",
                                    "expiry_date":     "Expiry",
                                    "strike_price":    "Current Strike",
                                    "net_qty":         "Net Qty",
                                    "avg_price":       "Avg Price (₹)",
                                    "ex_date":         "Ex-Date",
                                    "days_until_ex":   "Days to Ex",
                                    "dividend_amount": "Dividend (₹)",
                                    "new_strike":      "→ New Strike",
                                    "new_qty":         "→ New Qty",
                                    "urgency":         "Urgency",
                                }
                                _ar_cols = [c for c in _col_map_ar if c in _ar_df.columns]
                                _ar_df = _ar_df[_ar_cols].rename(columns=_col_map_ar)

                                def _urgency_color(val):
                                    return {
                                        "URGENT":   "color:#f44336;font-weight:bold",
                                        "SOON":     "color:#ff9800;font-weight:bold",
                                        "UPCOMING": "color:#ffeb3b",
                                    }.get(str(val), "")

                                if "Urgency" in _ar_df.columns:
                                    try:
                                        _ar_styled = _ar_df.style.map(_urgency_color, subset=["Urgency"])
                                    except AttributeError:
                                        _ar_styled = _ar_df.style.applymap(_urgency_color, subset=["Urgency"])
                                    st.dataframe(_ar_styled, use_container_width=True)
                                else:
                                    st.dataframe(_ar_df, use_container_width=True)

                                with st.expander("📋 Detailed issue descriptions", expanded=False):
                                    for _ar in _at_risk:
                                        st.markdown(
                                            f"- **{_ar['underlying']} {_ar['instrument_type']} "
                                            f"@ ₹{_ar['strike_price']:.0f}** — {_ar['issue']}"
                                        )

                        # ── Panel B: Stale Transactions ──────────────────────────────────
                        if _stale_txns:
                            with st.expander(
                                f"⚠️ Stale Transactions in Uploaded File ({len(_stale_txns)}) — wrong strike after ex-date",
                                expanded=False,
                            ):
                                st.caption(
                                    "These rows were recorded **after** the dividend ex-date but still "
                                    "use the **pre-dividend (wrong) strike price**. The broker should "
                                    "have issued corrected contract notes with the adjusted strike."
                                )
                                st.warning(
                                    "**What to do:** Contact your broker for corrected trade notes. "
                                    "Delete these transactions via the manual-delete tool in "
                                    "**Upload & Manage → Delete Manual Entry**, then re-upload with "
                                    "the correct post-adjustment strike."
                                )
                                _st_df = pd.DataFrame(_stale_txns)
                                _col_map_st = {
                                    "txn_id":            "Txn ID",
                                    "underlying":        "Underlying",
                                    "instrument_type":   "Type",
                                    "expiry_date":       "Expiry",
                                    "trade_date":        "Trade Date",
                                    "trade_type":        "B/S",
                                    "quantity":          "Qty",
                                    "price":             "Price (₹)",
                                    "old_strike":        "Strike in File ⚠️",
                                    "correct_strike":    "Correct Strike ✅",
                                    "ex_date":           "Div Ex-Date",
                                    "dividend_amount":   "Dividend (₹)",
                                    "adjustment_status": "Adj Status",
                                    "broker":            "Broker",
                                }
                                _st_cols = [c for c in _col_map_st if c in _st_df.columns]
                                _st_df = _st_df[_st_cols].rename(columns=_col_map_st)

                                def _wrong_strike_color(val):
                                    return "color:#f48fb1;font-weight:bold"

                                def _right_strike_color(val):
                                    return "color:#6fcf97;font-weight:bold"

                                try:
                                    _st_styled = _st_df.style
                                    if "Strike in File ⚠️" in _st_df.columns:
                                        _st_styled = _st_styled.map(
                                            _wrong_strike_color, subset=["Strike in File ⚠️"]
                                        )
                                    if "Correct Strike ✅" in _st_df.columns:
                                        _st_styled = _st_styled.map(
                                            _right_strike_color, subset=["Correct Strike ✅"]
                                        )
                                except AttributeError:
                                    _st_styled = _st_df.style
                                    if "Strike in File ⚠️" in _st_df.columns:
                                        _st_styled = _st_styled.applymap(
                                            _wrong_strike_color, subset=["Strike in File ⚠️"]
                                        )
                                    if "Correct Strike ✅" in _st_df.columns:
                                        _st_styled = _st_styled.applymap(
                                            _right_strike_color, subset=["Correct Strike ✅"]
                                        )
                                st.dataframe(_st_styled, use_container_width=True)

                                with st.expander("📋 Detailed issue descriptions", expanded=False):
                                    for _st in _stale_txns:
                                        st.markdown(
                                            f"- **Txn #{_st['txn_id']}** "
                                            f"({_st['underlying']} {_st['instrument_type']} "
                                            f"traded {_st['trade_date']}) — {_st['issue']}"
                                        )
                    else:
                        st.success("✅ No stale dividend-adjusted positions found in your transaction file.")

                    # ── Open F&O Positions ────────────────────────────────────────────────
                    st.subheader("Open F&O Positions")

                    # Dependency hint (only when nothing uploaded yet)
                    has_fno_uploads = bool(st.session_state.get("fno_txn_data"))
                    if not has_fno_uploads:
                        st.info(
                            "ℹ️ **Prerequisite:** Upload F&O trade files in the "
                            "**Upload & Manage** tab. Positions are computed automatically "
                            "from your transaction history."
                        )

                    st.caption("Only showing contracts with expiry ≥ today. "
                               "Green Qty = Long (bought). Red Qty = Short (sold).")

                    c_refresh, c_source = st.columns([1, 3])
                    cache_key = "fno_pos_data"

                    if c_refresh.button("🔄 Refresh Positions (~2-3 sec)", key="fno_pos_refresh_btn"):
                        st.session_state.pop(cache_key, None)
                        st.session_state.pop("fno_pos_last_loaded", None)

                    if cache_key not in st.session_state or st.session_state[cache_key] is None:
                        with st.spinner("Loading open F&O positions…"):
                            try:
                                resp = requests.get(
                                    f"{API_BASE}/fno/positions/{selected_user['id']}",
                                    headers={"Authorization": f"Bearer {st.session_state.token}"}
                                )
                                resp.raise_for_status()
                                st.session_state[cache_key] = resp.json()
                                from datetime import datetime as _dt
                                st.session_state["fno_pos_last_loaded"] = _dt.now().strftime("%H:%M:%S")
                            except Exception as e:
                                st.error(f"Error loading positions: {e}")
                                st.session_state[cache_key] = []

                    last_loaded = st.session_state.get("fno_pos_last_loaded")
                    if last_loaded:
                        c_source.caption(f"⏱ Last loaded: **{last_loaded}** — click Refresh to update")

                    data = st.session_state.get(cache_key, [])

                    if data:
                        sources = set(d.get("_source", "") for d in data)
                        if "computed" in sources:
                            st.info("📊 Positions computed from transaction history (no broker snapshot uploaded)")
                        else:
                            st.success("📁 Positions from uploaded broker file")

                        df = pd.DataFrame(data)

                        all_types = sorted(df["instrument_type"].unique().tolist()) if "instrument_type" in df.columns else []
                        type_filter = st.multiselect(
                            "Filter by Type", options=all_types, default=all_types,
                            key="fno_type_filter"
                        )
                        if type_filter:
                            df = df[df["instrument_type"].isin(type_filter)]

                        def _fmt_qty(val, itype):
                            try:
                                v = float(val)
                                if v == 0:
                                    return "—"
                                sign = "+" if v > 0 else ""
                                return f"{sign}{int(v):,}"
                            except Exception:
                                return str(val)

                        display_rows = []
                        for _, row in df.iterrows():
                            itype   = str(row.get("instrument_type", ""))
                            qty_raw = row.get("open_qty", 0)
                            try:
                                qty_f = float(qty_raw or 0)
                            except Exception:
                                qty_f = 0.0

                            if itype in ("CE", "PE"):
                                if qty_f < 0:
                                    qty_disp = f"{int(abs(qty_f)):,}  (Sold)"
                                elif qty_f > 0:
                                    qty_disp = f"{int(qty_f):,}  (Bought)"
                                else:
                                    qty_disp = "—"
                            else:
                                qty_disp = _fmt_qty(qty_f, itype)
                            live_pnl_raw = row.get("live_pnl")
                            live_pnl_disp = fmt_inr(live_pnl_raw) if live_pnl_raw is not None else "—"
                            live_price_raw = row.get("live_price")
                            live_price_disp = fmt_inr(live_price_raw) if live_price_raw is not None else "—"
                            display_rows.append({
                                "Underlying": row.get("underlying", ""),
                                "Type":       itype,
                                "Expiry":     str(row.get("expiry_date", "") or "")[:10],
                                "Strike":     f"{float(row.get('strike_price', 0) or 0):,.0f}" if itype in ("CE", "PE") else "—",
                                "Qty":        qty_disp,
                                "Avg Price":  fmt_inr(float(row.get("avg_price", 0) or 0)),
                                "Live Price": live_price_disp,
                                "P&L":        live_pnl_disp,
                                "Broker":     row.get("broker", ""),
                            })

                        disp = pd.DataFrame(display_rows)
                        def _color_pnl(val):
                            if val == "—":
                                return ""
                            try:
                                # Strip "₹" and "," to parse the float
                                n = float(str(val).replace("₹","").replace(",",""))
                                if n > 0: return "color:#6fcf97;font-weight:bold"
                                if n < 0: return "color:#f48fb1;font-weight:bold"
                            except:
                                pass
                            return ""
                        def _color_qty_disp(val):
                            if val == "—":
                                return "color:#888"
                            if "Sold" in str(val) or str(val).startswith("-"):
                                return "color:#f48fb1;font-weight:bold"
                            if "Bought" in str(val) or str(val).startswith("+"):
                                return "color:#6fcf97;font-weight:bold"
                            return ""

                        try:
                            styled = disp.style.map(_color_qty_disp, subset=["Qty"])
                            styled = styled.map(_color_pnl, subset=["P&L"]) 
                        except AttributeError:
                            styled = disp.style.applymap(_color_qty_disp, subset=["Qty"])

                        st.dataframe(styled, use_container_width=True)

                        if "instrument_type" in df.columns and len(df) > 0:
                            st.caption(f"**{len(df)} open position(s)**")
                            type_counts = df.groupby("instrument_type").size().reset_index(name="count")
                            metric_cols = st.columns(max(len(type_counts), 1))
                            for i, tc_row in type_counts.iterrows():
                                metric_cols[i % len(metric_cols)].metric(
                                    tc_row["instrument_type"], tc_row["count"]
                                )
                    else:
                        if has_fno_uploads:
                            st.info("No open F&O positions found. All contracts may have expired.")
                        else:
                            st.info("Upload F&O transaction files to see open positions here.")

                    # ── Dividend Adjustment Audit Log ─────────────────────────────────────
                    with st.expander("📋 Dividend Adjustment History", expanded=False):
                        st.caption("Full audit trail of all detected and applied dividend adjustments.")
                        _adj_refresh  = st.button("🔄 Refresh History", key="adj_hist_refresh")
                        _adj_hist_key = f"div_adj_history_{_uid}"
                        if _adj_refresh or _adj_hist_key not in st.session_state:
                            try:
                                st.session_state[_adj_hist_key] = get_adjustment_history(_uid)
                            except Exception as _he:
                                st.error(str(_he))
                                st.session_state[_adj_hist_key] = []

                        _hist = st.session_state.get(_adj_hist_key) or []
                        if _hist:
                            _hdf = pd.DataFrame(_hist)
                            _col_map_h = {
                                "ex_date":         "Ex-Date",
                                "underlying":      "Underlying",
                                "instrument_type": "Type",
                                "old_strike":      "Old Strike",
                                "new_strike":      "New Strike",
                                "old_qty":         "Old Qty",
                                "new_qty":         "New Qty",
                                "dividend_amount": "Dividend (₹)",
                                "spot_prev":       "S_prev (₹)",
                                "scenario":        "Scenario",
                                "status":          "Status",
                                "applied_at":      "Applied At",
                                "notes":           "Notes",
                            }
                            _existing_h = [c for c in _col_map_h if c in _hdf.columns]
                            _hdf = _hdf[_existing_h].rename(columns=_col_map_h)

                            def _status_color_h(val):
                                return {
                                    "APPLIED":       "color:#6fcf97;font-weight:bold",
                                    "PENDING":       "color:#ff9800;font-weight:bold",
                                    "SKIPPED":       "color:#888",
                                    "USER_UPLOADED": "color:#64b5f6;font-weight:bold",
                                }.get(str(val), "")

                            try:
                                _styled_h = _hdf.style.map(_status_color_h, subset=["Status"])
                            except AttributeError:
                                _styled_h = _hdf.style.applymap(_status_color_h, subset=["Status"])
                            st.dataframe(_styled_h, use_container_width=True)
                        else:
                            st.info("No dividend adjustment history yet.")

                    st.divider()
                    st.subheader("F&O Realized P&L")
                    if st.button("Refresh F&O P&L", key="fno_pnl_refresh_btn"):
                        st.session_state.pop("fno_pnl_data", None)
                    fno_pnl_data = _lazy("fno_pnl_data", get_fno_pnl, selected_user["id"])
                    if fno_pnl_data:
                        df = pd.DataFrame(fno_pnl_data)
                        df = df[["underlying", "instrument_type", "sell_date", "quantity", "buy_price", "sell_price", "gross_pnl"]]
                        df.columns = ["Underlying", "Type", "Sell Date", "Qty", "Buy Price", "Sell Price", "Gross P&L"]
                        st.dataframe(df, use_container_width=True)
                    else:
                        st.info("No realized F&O P&L.")

                    # ── Covered Call Analysis ─────────────────────────────────────────────
                    st.divider()
                    st.subheader("🛡️ Covered Call Analysis")
                    if st.button("🔄 Refresh Covered Call Analysis", key="cc_refresh_btn"):
                        st.session_state.pop("cc_analysis_data", None)

                    if "cc_analysis_data" not in st.session_state or st.session_state.cc_analysis_data is None:
                        with st.spinner("Analysing positions…"):
                            try:
                                cc_resp = get_covered_call_analysis(selected_user["id"])
                                # ⭐ CRITICAL FIX: Check if the backend returned a success status
                                if cc_resp.get("status") == "success":
                                    st.session_state.cc_analysis_data = cc_resp.get("data", {})
                                else:
                                    # Display the actual error message from the backend
                                    error_msg = cc_resp.get('message', 'Unknown backend error')
                                    st.error(f"Could not load covered call analysis: {error_msg}")
                                    st.session_state.cc_analysis_data = {}
                            except Exception as e:
                                st.error(f"Could not load covered call analysis: {e}")
                                st.session_state.cc_analysis_data = {}

                    cc_data = st.session_state.get("cc_analysis_data") or {}

                    # Table A — Active Covered Calls
                    st.markdown("#### ✅ Table A — Active Covered Calls")
                    st.caption("Positions where you have a Sold CE AND a matching Holding or Long FUT.")
                    cc_rows = cc_data.get("covered_calls", [])
                    if cc_rows:
                        cc_df = pd.DataFrame(cc_rows)
                        col_map = {
                            "symbol":            "Symbol",
                            "eq_qty":            "Holding Qty",
                            "eq_avg_price":      "Avg Cost (₹)",
                            "long_fut_qty":      "FUT Qty",
                            "ce_strike":         "CE Strike",
                            "ce_expiry":         "CE Expiry",
                            "ce_avg_premium":    "Premium Collected (₹)",
                            "ce_unrealized_pnl": "CE Unreal P&L (₹)",
                            "spot":              "Spot (₹)",
                        }
                        existing_cols = [c for c in col_map if c in cc_df.columns]
                        cc_df = cc_df[existing_cols].rename(columns=col_map)
                        st.dataframe(cc_df, use_container_width=True)
                    else:
                        st.info("No active covered call positions found.")

                    # Table B — Uncovered Holdings / FUTs
                    st.markdown("#### 📋 Table B — Uncovered Holdings / FUTs (No Sold CE Yet)")
                    st.caption("You hold these but haven't written a covered call on them yet.")
                    unc_rows = cc_data.get("uncovered", [])
                    if unc_rows:
                        unc_df = pd.DataFrame(unc_rows)
                        col_map_b = {
                            "symbol":            "Symbol",
                            "eq_qty":            "Holding Qty",
                            "eq_avg_price":      "Avg Cost (₹)",
                            "long_fut_qty":      "FUT Qty (shares)",
                            "fut_avg_entry":     "FUT Avg Entry (₹)",
                            "spot":              "Spot (₹)",
                            "eq_unrealized_pnl": "Unreal P&L (₹)",
                            "reason":            "Note",
                            "suggested_ce_near_expiry":   "CE Expiry (Near)",
                            "suggested_ce_near_strike":   "CE Strike (Near)",
                            "suggested_ce_near_premium":  "CE Prem (Near) ₹",
                            "suggested_ce_far_expiry":    "CE Expiry (Far)",
                            "suggested_ce_far_strike":    "CE Strike (Far)",
                            "suggested_ce_far_premium":   "CE Prem (Far) ₹"
                        }
                        existing_cols_b = [c for c in col_map_b if c in unc_df.columns]
                        unc_df = unc_df[existing_cols_b].rename(columns=col_map_b)

                        def _color_unreal_b(val):
                            try:
                                n = float(val)
                                if n > 0: return "color:#6fcf97"
                                if n < 0: return "color:#f48fb1"
                            except Exception:
                                pass
                            return ""

                        pnl_col  = [c for c in unc_df.columns if "Unreal" in c]
                        styled_b = unc_df.style
                        if pnl_col:
                            styled_b = styled_b.map(_color_unreal_b, subset=pnl_col)
                        
                        #  CRITICAL FIX: Format the float columns to 2 decimal places
                        styled_b = styled_b.format({
                            "Avg Cost (₹)": "{:.2f}",
                            "FUT Avg Entry (₹)": "{:.2f}",
                            "Unreal P&L (₹)": "{:.2f}",
                            "Spot (₹)": "{:.2f}",
                            "CE Strike (Near)": "{:.0f}",
                            "CE Prem (Near) ₹": "{:.2f}",
                            "CE Strike (Far)": "{:.0f}",
                            "CE Prem (Far) ₹": "{:.2f}"
                        })
                        
                        st.dataframe(styled_b, use_container_width=True)

                    # Table C — Correction Module
                    st.markdown("#### ⚠️ Table C — Correction Module (Loss > ₹10,000)")
                    st.caption(
                        "P&L calculated using **live option prices from 5paisa**. "
                        "If live price unavailable, intrinsic value is used as a conservative estimate. "
                        "Sorted by largest loss first."
                    )
                    corr_rows = cc_data.get("correction_module", [])
                    if corr_rows:
                        corr_df = pd.DataFrame(corr_rows)
                        col_map_c = {
                            "symbol":          "Symbol",
                            "instrument_type": "Type",
                            "strike":          "Strike",
                            "expiry":          "Expiry",
                            "open_qty":        "Qty",
                            "avg_price":       "Avg Price (₹)",
                            "live_price":      "Live Option Price (₹)",
                            "spot":            "Stock Spot (₹)",
                            "estimated_pnl":   "P&L (₹)",
                            "loss_amount":     "Loss (₹)",
                            "price_source":    "Price Source",
                            "suggestion":      "Suggested Action",
                        }
                        existing_cols_c = [c for c in col_map_c if c in corr_df.columns]
                        corr_df = corr_df[existing_cols_c].rename(columns=col_map_c)

                        def _highlight_loss(val):
                            try:
                                if float(val) > 50000:
                                    return "color:#f44336;font-weight:bold"
                                if float(val) > 20000:
                                    return "color:#ff9800;font-weight:bold"
                                return "color:#ffeb3b"
                            except Exception:
                                return ""

                        # Check for "Loss (₹)" instead of "Est. Loss (₹)"
                        if "Loss (₹)" in corr_df.columns:
                            try:
                                styled_corr = corr_df.style.map(_highlight_loss, subset=["Loss (₹)"])
                            except AttributeError:
                                styled_corr = corr_df.style.applymap(_highlight_loss, subset=["Loss (₹)"])
                            st.dataframe(styled_corr, use_container_width=True)
                        else:
                            st.dataframe(corr_df, use_container_width=True)
                    else:
                        st.success("✅ No positions with loss > ₹10,000.")
                # ── Tab 6: Tax Harvest ─────────────────────────────────────────
                with tab6:
                    st.subheader("🌾 Tax Harvesting Analysis")

                    # Dependency check
                    if not st.session_state.get("pnl_data") and not st.session_state.get("txn_data"):
                        st.info("ℹ️ For accurate results, load your transactions first (visit the **Transactions** tab).")

                    col1, col2 = st.columns(2)
                    start_date = col1.date_input("From", value=date.today().replace(month=3, day=1),  key="su_th_start")
                    end_date   = col2.date_input("To",   value=date.today().replace(month=3, day=31), key="su_th_end")

                    if "su_th_result" not in st.session_state:
                        st.session_state.su_th_result = None

                    if st.session_state.su_th_result is None:
                        with st.spinner("Analysing…"):
                            try:
                                st.session_state.su_th_result = run_harvest(
                                    selected_user["id"], str(start_date), str(end_date)
                                )
                            except Exception as e:
                                st.session_state.su_th_result = {"error": str(e)}

                    if st.button("▶ Run Harvest Analysis", type="primary", key="su_th_run"):
                        with st.spinner("Analysing…"):
                            try:
                                st.session_state.su_th_result = run_harvest(
                                    selected_user["id"], str(start_date), str(end_date)
                                )
                                st.session_state.pop("_harvest_price_map", None)
                                st.rerun()
                            except Exception as e:
                                st.session_state.su_th_result = {"error": str(e)}

                    render_harvest_ui(result=st.session_state.su_th_result, fetch_prices_fn=fetch_prices)

                # ── Tab 7: BE Graphs ───────────────────────────────────────────
                with tab7:
                    st.subheader("📉 Breakeven Strategy Visualiser")
                    st.caption("Select an underlying with open F&O positions to view payoff at expiry.")

                    # Dependency check
                    if not st.session_state.get("fno_pos_data") and not st.session_state.get("fno_pos_expired"):
                        st.info("ℹ️ Load F&O positions first — visit the **F&O (Positions & P&L)** tab.")

                    if st.button("🔄 Refresh Open Positions for BE", key="su_be_load"):
                        st.session_state.pop("be_positions", None)

                    positions = _lazy("be_positions", get_fno_positions, selected_user["id"])
                    if positions:
                        pos_df = pd.DataFrame(positions)
                        stock_legs = extract_legs_from_op_df(pos_df)
                        if stock_legs:
                            chosen_stock = st.selectbox("Select underlying", sorted(stock_legs.keys()), key="su_be_stock")
                            if chosen_stock:
                                legs = stock_legs[chosen_stock]
                                cmp_val = st.number_input("Current Market Price (₹)", min_value=0.0, value=0.0, step=1.0, key="su_be_cmp")
                                fig = build_be_figure(legs, chosen_stock, cmp=cmp_val)
                                st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info("No valid F&O legs found.")
                    else:
                        st.info("No open F&O positions found.")

                # ── Tab 8: Ledger ──────────────────────────────────────────────
                with tab8:
                    from ledger_ui import render_ledger_tab
                    render_ledger_tab(
                        user_id          = selected_user["id"],
                        upload_ledger_fn = upload_ledger,
                        get_ledger_fn    = get_ledger,
                    )
                # ── Tab 9: CE/PE Screener ──────────────────────────────────────
                with tab9:
                    # Ensure api_client.TOKEN is set (critical — fixes the Login page bug)
                    import api_client as _ac
                    _ac.TOKEN = st.session_state.token

                    # Dependency check: need holdings + stock master + F&O setup
                    has_holdings = bool(st.session_state.get("sm_grid_data"))
                    has_fno = bool(st.session_state.get("fno_pos_data"))
                    if not has_holdings:
                        st.info(
                            "ℹ️ **Prerequisite:** Upload transaction files and run Auto-Populate "
                            "in the **Upload & Manage** tab. Then come back here."
                        )
                    if not has_fno:
                        st.caption("💡 Upload F&O files in **Upload & Manage** for open position tracking in the screener.")

                    render_ce_pe_tab(
                        user_id         = selected_user["id"],
                        get_basic_fn    = get_ce_pe_screener_data,
                        get_advanced_fn = get_advanced_screener_data,
                    )

                # ── Tab 10: Master Reference ───────────────────────────────────
                with tab10:
                    st.header("📡 Master Reference Positions")
                    st.caption(
                        "Shows master account positions that are **not** covered calls there — "
                        "these are signals you can act on in your own account."
                    )

                    with st.expander("📐 Column Guide", expanded=False):
                        st.markdown("""
| Column | What it means |
|---|---|
| **Symbol** | NSE ticker (underlying stock) |
| **Position Type** | What the master account has open on this stock |
| **Inst.** | Instrument — PE / CE / FUT / EQ/FUT |
| **Strike** | Option strike price (— for FUT or equity) |
| **Expiry** | Contract expiry date (— for equity holding) |
| **Spot (₹)** | Current live market price of the underlying stock |
| **Your Status** | **NEW** = you have no matching position yet — opportunity to act · **PARTIAL** = you have a position on this stock but at a different strike · **TAKEN** = you already have the same/similar position open |
| **Suggestion** | Recommended action for your account based on master's position |
""")

                    _acct_id = st.session_state.get("current_account_id")

                    _ref_data = None
                    _is_master = False

                    if st.button("🔄 Refresh Reference Positions", key="master_ref_refresh"):
                        st.session_state.pop("master_ref_data", None)

                    if "master_ref_data" not in st.session_state or st.session_state.master_ref_data is None:
                        if _acct_id:
                            with st.spinner("Fetching master reference positions…"):
                                try:
                                    ref_resp = get_master_reference_positions(_acct_id)
                                    st.session_state.master_ref_data = ref_resp
                                except Exception as e:
                                    st.session_state.master_ref_data = {"status": "error", "message": str(e)}
                        else:
                            st.session_state.master_ref_data = {"status": "error", "message": "Account ID not found"}

                    _ref_resp = st.session_state.get("master_ref_data") or {}

                    if _ref_resp.get("status") == "error":
                        err_msg = _ref_resp.get("message", "Unknown error")
                        if "IS the master account" in err_msg:
                            st.info(
                                "ℹ️ **You are logged in as the master account (Account 1).** "
                                "The Master Reference tab is only relevant for child accounts. "
                                "Other accounts see your uncovered positions here as reference signals."
                            )
                            _is_master = True
                        else:
                            st.error(f"Could not load reference positions: {err_msg}")
                    else:
                        _ref_data = (_ref_resp.get("data") or {}).get("reference_positions", [])
                        _master_acct  = (_ref_resp.get("data") or {}).get("master_account_id")
                        _total        = (_ref_resp.get("data") or {}).get("total_reference_signals", 0)
                        _new_count    = (_ref_resp.get("data") or {}).get("new_signals", 0)

                        if _master_acct:
                            st.success(
                                f"Master Account (ID: {_master_acct}) — "
                                f"{_total} signal(s) found, **{_new_count} new** (not yet taken by you)"
                            )

                        if not _ref_data:
                            st.info("No reference signals available. The master account may have all positions covered or no open positions.")
                        else:
                            # Filter by Your Status
                            _status_filter = st.multiselect(
                                "Filter by Your Status",
                                options=["NEW", "PARTIAL", "TAKEN"],
                                default=["NEW", "PARTIAL"],
                                key="ref_status_filter"
                            )
                            filtered = [r for r in _ref_data if r.get("child_status", "NEW") in _status_filter]

                            if not filtered:
                                st.info("No signals match the selected status filter.")
                            else:
                                ref_df = pd.DataFrame(filtered)

                                col_map_ref = {
                                    "symbol":        "Symbol",
                                    "position_type": "Position Type",
                                    "instrument":    "Inst.",
                                    "strike":        "Strike",
                                    "expiry":        "Expiry",
                                    "spot":          "Spot (₹)",
                                    "child_status":  "Your Status",
                                    "suggestion":    "Suggestion",
                                }
                                existing_ref_cols = [c for c in col_map_ref if c in ref_df.columns]
                                ref_df_disp = ref_df[existing_ref_cols].rename(columns=col_map_ref)

                                def _status_color(val):
                                    return {
                                        "NEW":     "color:#6fcf97;font-weight:bold",
                                        "PARTIAL": "color:#ff9800;font-weight:bold",
                                        "TAKEN":   "color:#888",
                                    }.get(str(val), "")

                                def _pnl_color(val):
                                    try:
                                        n = float(val)
                                        if n > 0: return "color:#6fcf97"
                                        if n < 0: return "color:#f48fb1"
                                    except Exception:
                                        pass
                                    return ""

                                try:
                                    styled_ref = ref_df_disp.style
                                    if "Your Status" in ref_df_disp.columns:
                                        styled_ref = styled_ref.map(_status_color, subset=["Your Status"])
                                    if "Master P&L (₹)" in ref_df_disp.columns:
                                        styled_ref = styled_ref.map(_pnl_color, subset=["Master P&L (₹)"])
                                except AttributeError:
                                    styled_ref = ref_df_disp.style
                                    if "Your Status" in ref_df_disp.columns:
                                        styled_ref = styled_ref.applymap(_status_color, subset=["Your Status"])
                                    if "Master P&L (₹)" in ref_df_disp.columns:
                                        styled_ref = styled_ref.applymap(_pnl_color, subset=["Master P&L (₹)"])

                                st.dataframe(styled_ref, use_container_width=True)
                                st.caption(
                                    "**NEW** = act on it — you have no matching position yet  · "
                                    "**PARTIAL** = you have a position on same stock but different strike  · "
                                    "**TAKEN** = you already have the same/similar position open"
                                )

                with tab11:
                    from wishlist_ui import render_wishlist_tab
                    render_wishlist_tab(
                        user_id=selected_user["id"],
                        api_fns={
                            "get":        api_client.get_wishlist,
                            "add":        api_client.add_to_wishlist,
                            "remove":     api_client.remove_from_wishlist,
                            "sync":       api_client.sync_wishlist,
                            "clear_auto": api_client.clear_wishlist_auto,
                            "clear_all":  api_client.clear_wishlist_all,
                        }
                    )

            else:
                st.info("Select a user from the sidebar or create a new one.")
                
                