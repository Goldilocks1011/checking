import streamlit as st
import pandas as pd
from datetime import datetime
import numpy as np

# This mirrors your existing DB connection logic
def analyze_rollover_decision(account_id):
    from put_sell import get_db_connection, get_5paisa_client, load_scrip_master
    
    conn = get_db_connection()
    client = get_5paisa_client()
    _, _, _, _, df_futures = load_scrip_master()
    
    if not conn or not client:
        return

    # 1. Fetch current open Future positions from your DB
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT symbol, net_quantity, avg_buy_price, current_price, total_pnl 
        FROM positions 
        WHERE account_id = %s AND is_open = TRUE AND instrument_type LIKE '%FUT%'
    """, (account_id,))
    positions = cursor.fetchall()

    st.subheader("🔄 Rollover vs. Profit Booking Analysis")

    for pos in positions:
        symbol = pos['symbol']
        qty = pos['net_quantity']
        
        # 2. Fetch Price for Next Month Expiry
        # Logic: Find the next expiry date in ScripMaster
        future_contracts = df_futures[df_futures['Name'].str.contains(symbol)]
        # Filter for the 'Far' month (next month)
        # This is a simplified search for the next available expiry
        
        st.write(f"### Analysis for {symbol}")
        
        # Calculation for Rollover Cost
        # Formula: ((Next_Month_Price - Current_Month_Price) / Current_Month_Price) * 100
        current_price = pos['current_price']
        next_month_price = current_price * 1.005  # Placeholder: replace with actual API fetch
        
        roll_cost_pct = ((next_month_price - current_price) / current_price) * 100
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Current P&L", f"₹{pos['total_pnl']:,.2f}")
        col2.metric("Rollover Cost", f"{roll_cost_pct:.2f}%")
        
        # 3. Decision Matrix
        if roll_cost_pct > 0.8:
            st.error(f"❌ **Action: BOOK PROFIT.** Rollover is too expensive ({roll_cost_pct:.2f}%). It's better to take the money now.")
        elif current_price < pos['avg_buy_price']:
            st.warning("⚠️ **Action: EXIT.** Price is trending below your average; don't roll a losing trade.")
        else:
            st.success(f"✅ **Action: ROLLOVER.** Cost is low ({roll_cost_pct:.2f}%) and you are in profit. Potential for bounce in the next series.")

    cursor.close()
    conn.close()

# Example usage for your Account 1 (where your Bajaj/HDFC positions are)
# analyze_rollover_decision(1)