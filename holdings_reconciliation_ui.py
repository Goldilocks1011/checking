"""
Holdings Reconciliation UI Components
========================================
Separate UI module for Holdings Reconciliation (like ledger_ui, wishlist_ui).
Components:
  1. Upload UI (for Tab 1 - Upload & Manage)
  2. Result UI (for Tab 2 - Holdings / Stock Master)
"""
import streamlit as st
import pandas as pd
import json
import requests

API_BASE = "http://localhost:8001/api/v1"


def render_holdings_upload_section(user_id: int, token: str):
    """
    Renders the Holdings Upload section in Tab 1 (Upload & Manage).
    Parallel to Equity Upload and F&O Upload.
    
    Stores reconciliation diff in: st.session_state.last_reconciliation_diff
    """
    with st.expander("🏦 Holdings Upload (for Reconciliation)", expanded=False):
        st.caption(
            "Upload your broker's holdings/portfolio statement (CSV or Excel) "
            "to compare with your transaction-derived holdings."
        )
        
        # Broker selector
        broker = st.selectbox(
            "Select Broker (Holdings)",
            ["5paisa", "IIFL", "Zerodha"],
            key="holdings_broker_select",
            help="Choose the broker where these holdings are held"
        )
        
        # Info about file format
        if broker == "Zerodha":
            st.info("📌 **Download path:** Console → Portfolio → Positions (export as CSV)")
        elif broker == "IIFL":
            st.info("📌 **Download path:** Backoffice → Portfolio Summary (Excel) OR CDSL CAS Statement")
        elif broker == "5paisa":
            st.info("📌 **Download path:** Portfolio → Holdings Report (Excel)")
        
        # File uploader
        uploaded_file = st.file_uploader(
            "Choose holdings file (.csv / .xlsx / .xls)",
            type=["csv", "xlsx", "xls"],
            key="holdings_file_uploader",
            help="Upload your broker's holdings statement. Supported: Zerodha, IIFL, 5paisa"
        )
        
        # Upload button
        if st.button(
            "▶ Upload & Compare Holdings",
            type="primary",
            disabled=(uploaded_file is None),
            key="holdings_upload_btn",
            help="Upload and compare with your transaction-derived holdings"
        ):
            with st.spinner("📡 Uploading and analyzing holdings..."):
                try:
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
                    data = {"user_id": user_id, "broker": broker}
                    headers = {"Authorization": f"Bearer {token}"}

                    resp = requests.post(
                        f"{API_BASE}/holdings/reconcile/upload",
                        files=files,
                        data=data,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()

                    if result.get("status") == "success":
                        # Save diff to session state for Tab 2 to display
                        st.session_state.last_reconciliation_diff = result.get("diff", {})
                        st.session_state.holdings_reconciliation_active = True
                        st.success(
                            f"✅ Holdings uploaded successfully!\n"
                            f"Broker: {result.get('broker_detected', 'Unknown')}\n"
                            f"File: {result.get('file_name', '')}"
                        )
                        st.info("Go to the **Holdings / Stock Master** tab to review matching results.")
                        st.rerun()
                    else:
                        st.error(f"❌ Upload failed: {result.get('message', 'Unknown error')}")
                except Exception as e:
                    st.error(f"❌ Upload error: {e}")


def render_reconciliation_results(user_id: int, token: str, diff: dict):
    """
    Renders the Holdings Reconciliation Results in Tab 2 (Holdings / Stock Master).
    Called after holdings file is uploaded.
    
    Shows:
      - Matched holdings (no action needed)
      - Extra holdings (in broker, not in transactions) → user picks source
      - Missing holdings (in transactions, not in broker) → user confirms action
    """
    if diff is None:
        return False  # No diff to display
    
    st.markdown("---")
    st.subheader("⚖️ Holdings Reconciliation Results")
    
    # Summary metrics
    summary = diff.get("summary", {})
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("✅ Matched", summary.get("total_matched", 0), help="Holdings match broker statement")
    m2.metric("❌ Extra in Broker", summary.get("total_extra", 0), help="Broker has these, you don't record")
    m3.metric("❌ Missing from Broker", summary.get("total_missing", 0), help="You record these, broker doesn't show")
    m4.metric("📅 As of", diff.get("comparison_date", "—"))
    
    st.divider()
    
    # ── MATCHED HOLDINGS (collapsible, read-only) ──
    matched = diff.get("matched", [])
    with st.expander(f"✅ Matched Holdings ({len(matched)}) — No action needed"):
        if matched:
            matched_df = pd.DataFrame(matched)
            matched_df = matched_df[["symbol", "isin", "broker_qty", "your_qty", "status"]].copy()
            matched_df.columns = ["Symbol", "ISIN", "Broker Qty", "Your Qty", "Status"]
            st.dataframe(matched_df, use_container_width=True, hide_index=True)
        else:
            st.info("No matched holdings.")
    
    st.divider()
    
    # ── EXTRA IN BROKER (Action Required) ──
    extra = diff.get("extra", [])
    corrections_extra = []
    
    if extra:
        st.subheader(f"❌ Extra in Broker ({len(extra)}) — Confirm Source")
        st.caption(
            "These holdings exist in your broker account but not in your transaction history. "
            "Select where these came from:"
        )
        
        for i, item in enumerate(extra):
            with st.expander(
                f"**{item['symbol']}** ({item['isin'] or '—'}) — {item['difference']:.0f} shares @ ₹{item.get('avg_cost', 0):.2f}",
                expanded=(i == 0)  # Expand first item by default
            ):
                col1, col2 = st.columns(2)
                
                source = col1.selectbox(
                    "How did you get this holding?",
                    [
                        "IPO (Initial Public Offering)",
                        "Bonus (Free shares)",
                        "Split (Stock split)",
                        "Merger",
                        "Demerger",
                        "Transfer (Inter-demat)",
                        "Manual Buy (Unrecorded purchase)",
                        "Other",
                        "Ignore (No action)"
                    ],
                    key=f"extra_source_{i}",
                    help="Select the source of this holding"
                )
                
                price_input = col2.number_input(
                    "Entry Price (₹)",
                    value=float(item.get("avg_cost", 0)) or 0.0,
                    step=1.0,
                    key=f"extra_price_{i}",
                    help="Price at which you acquired this (if applicable)"
                )
                
                # Map display name to API name
                source_map = {
                    "IPO (Initial Public Offering)": "IPO",
                    "Bonus (Free shares)": "BONUS",
                    "Split (Stock split)": "SPLIT",
                    "Merger": "MERGER",
                    "Demerger": "DEMERGER",
                    "Transfer (Inter-demat)": "TRANSFER",
                    "Manual Buy (Unrecorded purchase)": "MANUAL_BUY",
                    "Other": "OTHER",
                    "Ignore (No action)": "IGNORE"
                }
                
                corrections_extra.append({
                    "symbol": item["symbol"],
                    "isin": item["isin"],
                    "quantity": item["difference"],
                    "source": source_map.get(source, source),
                    "price": price_input,
                    "type": "extra",
                })
        
        st.session_state.corrections_extra = corrections_extra
    else:
        st.success("✅ No extra holdings found.")
    
    st.divider()
    
    # ── MISSING FROM BROKER (Action Required) ──
    missing = diff.get("missing", [])
    corrections_missing = []
    
    if missing:
        st.subheader(f"❌ Missing from Broker ({len(missing)}) — Confirm Ownership")
        st.caption(
            "These holdings are in your transaction history but not in your broker statement. "
            "What happened to them?"
        )
        
        for i, item in enumerate(missing):
            with st.expander(
                f"**{item['symbol']}** ({item['isin'] or '—'}) — {item['difference']:.0f} shares",
                expanded=(i == 0)
            ):
                col1, col2 = st.columns(2)
                
                action = col1.selectbox(
                    "What happened?",
                    [
                        "Sell (unrecorded sale)",
                        "Transfer Out (inter-demat)",
                        "Broker Error (stale statement)",
                        "Rights Entitlement (-RE)",
                        "Other"
                    ],
                    key=f"missing_action_{i}",
                    help="Confirm what happened to this holding"
                )
                
                price_input = col2.number_input(
                    "Exit Price (₹) if sold",
                    value=0.0,
                    step=1.0,
                    key=f"missing_price_{i}",
                    help="Price at which you sold (if applicable)"
                )
                
                action_map = {
                    "Sell (unrecorded sale)": "SELL",
                    "Transfer Out (inter-demat)": "TRANSFER",
                    "Broker Error (stale statement)": "IGNORE",
                    "RIGHTS": "RIGHTS",
                    "Other": "OTHER"
                }
                
                corrections_missing.append({
                    "symbol": item["symbol"],
                    "isin": item["isin"],
                    "quantity": item["difference"],
                    "source": action_map.get(action, action),
                    "price": price_input,
                    "type": "missing",
                })
        
        st.session_state.corrections_missing = corrections_missing
    else:
        st.success("✅ No missing holdings found.")
    
    st.divider()
    
    # ── APPLY CORRECTIONS BUTTON ──
    if extra or missing:
        if st.button(
            "✅ Apply Corrections & Rebuild Holdings",
            type="primary",
            key="apply_reconciliation_btn",
            help="Submit corrections and rebuild holdings/P&L"
        ):
            # Merge corrections
            all_corrections = (
                st.session_state.get("corrections_extra", []) +
                st.session_state.get("corrections_missing", [])
            )
            
            # Filter out ignored actions
            corrections_to_apply = [
                {
                    "symbol": c["symbol"],
                    "isin": c["isin"],
                    "quantity": c["quantity"],
                    "source": c["source"],
                    "price": c.get("price", 0),
                }
                for c in all_corrections
                if c["source"] not in ("IGNORE", "OTHER")
            ]
            
            if corrections_to_apply:
                with st.spinner("⚙️ Applying corrections and rebuilding holdings..."):
                    try:
                        resp = requests.post(
                            f"{API_BASE}/holdings/reconcile/apply",
                            data={
                                "user_id": user_id,
                                "corrections": json.dumps(corrections_to_apply),
                            },
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        resp.raise_for_status()
                        result = resp.json()
                        
                        if result.get("status") in ("success", "partial"):
                            st.success(f"✅ {result.get('message', 'Corrections applied!')}")
                            
                            # Show actions taken
                            actions = result.get("actions_taken", [])
                            if actions:
                                with st.expander("📋 Actions Applied", expanded=True):
                                    action_df = pd.DataFrame(actions)
                                    st.dataframe(action_df, use_container_width=True, hide_index=True)
                            
                            # Show errors if any
                            errors = result.get("errors", [])
                            if errors:
                                st.warning("⚠️ Some errors occurred:")
                                for err in errors:
                                    st.caption(f"• {err}")
                            
                            # Clear session and show next steps
                            st.session_state.pop("last_reconciliation_diff", None)
                            st.session_state.pop("holdings_reconciliation_active", None)
                            st.session_state.pop("corrections_extra", None)
                            st.session_state.pop("corrections_missing", None)
                            
                            st.info(
                                "💡 **Holdings and P&L have been rebuilt.** "
                                "The results are reflected above. "
                                "You can now proceed to rename stocks or upload more data."
                            )
                            st.rerun()
                        else:
                            st.error(result.get("message", "Failed to apply corrections."))
                    except Exception as e:
                        st.error(f"❌ Error: {e}")
            else:
                st.warning("⚠️ No corrections to apply (all marked as 'Ignore' or 'Other').")
    
    st.markdown("---")
    return True  # Diff was displayed