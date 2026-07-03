import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import sys, os
from concurrent.futures import ThreadPoolExecutor, as_completed
import functools

# PATH SETUP
current_file_path = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file_path)
PROJECT_ROOT = os.path.dirname(current_dir)
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
CSV_PATH = os.path.join(PROJECT_ROOT, 'ScripMaster_all.csv')

# IMPORTS
try:
    from auth_manager import get_client
except ImportError:
    st.error("❌ 'auth_manager.py' not found.")

try:
    from db_helper import get_formatted_open_positions
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

try:
    from connection import get_connection
    DB_CONNECTION_AVAILABLE = True
except ImportError:
    DB_CONNECTION_AVAILABLE = False

# ==================== SESSION STATE INITIALIZATION ====================
if 'selected_account' not in st.session_state:
    st.session_state.selected_account = None
if 'selected_account_id' not in st.session_state:
    st.session_state.selected_account_id = None

# ==================== DATABASE FUNCTIONS ====================
def get_accounts():
    """Fetch all active accounts from database using connection.py"""
    if not DB_CONNECTION_AVAILABLE:
        return []
    
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT account_id, holder_name, client_code, broker, 
                   capital, available_balance, margin_used
            FROM accounts
            WHERE is_active = TRUE
            ORDER BY account_id
        """
        cursor.execute(query)
        accounts = cursor.fetchall()
        cursor.close()
        conn.close()
        return accounts
    except Exception as e:
        st.error(f"❌ Error fetching accounts: {e}")
        return []

# ============================================
# CACHING & PERFORMANCE OPTIMIZATIONS
# ============================================

@st.cache_data
def load_scrip_master():
    """Load CSV once and cache it"""
    try:
        df = pd.read_csv(CSV_PATH)
        df_stocks = df[df['Series'] == 'EQ'].copy()
        
        df_options = df[((df['ExchType'] == 'D') | (df['ExchType'] == 'N') | (df['ExchType'] == 'B')) & 
                        (df['Expiry'].notna())].copy()
        
        df_futures = df[(df['ExchType'].isin(['D', 'N', 'B'])) & 
                        (df['Expiry'].notna()) & 
                        (df['ScripType'] == 'XX')].copy()
        
        # Pre-normalize expiry dates for faster lookup
        df_options['NormalizedExpiry'] = df_options['Expiry'].apply(normalize_expiry_date)
        df_futures['NormalizedExpiry'] = df_futures['Expiry'].apply(normalize_expiry_date)
        
        scrip_dict = dict(zip(df_stocks['Name'], df_stocks['ScripCode']))
        
        return scrip_dict, df_stocks, df_options, df_futures
    except FileNotFoundError:
        st.error(f"❌ CSV not found: {CSV_PATH}")
        return {}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

@st.cache_data(ttl=60)  # Cache for 1 minute
def load_db_positions(account_id):
    """Cache positions for 1 minute - NOW WITH ACCOUNT FILTERING"""
    if not DB_AVAILABLE: 
        return []
    try:
        return get_formatted_open_positions(account_id)  # ✅ PASS account_id
    except:
        return []

@functools.lru_cache(maxsize=1)
def get_5paisa_client():
    """Cache client object"""
    try: 
        return get_client()
    except: 
        return None

def normalize_expiry_date(expiry_str):
    """Fast expiry normalization with caching"""
    if pd.isna(expiry_str):
        return None
    
    if isinstance(expiry_str, (datetime, pd.Timestamp)):
        return expiry_str.strftime('%Y-%m-%d')
    
    if isinstance(expiry_str, str):
        for fmt in ['%m/%d/%Y', '%d %b %Y', '%Y-%m-%d']:
            try:
                dt = pd.to_datetime(expiry_str, format=fmt)
                return dt.strftime('%Y-%m-%d')
            except:
                continue
    
    return str(expiry_str)

# ============================================
# FASTER SCRIP CODE LOOKUP (Vectorized)
# ============================================

def get_option_scrip_code_fast(symbol, strike, option_type, expiry_date, options_df):
    """Optimized version - no debug overhead"""
    try:
        symbol_root = symbol.split()[0].strip().upper()
        target_expiry = normalize_expiry_date(expiry_date)
        strike_float = float(strike)
        
        # Single vectorized filter
        mask = (
            (options_df['SymbolRoot'].str.upper() == symbol_root) &
            (options_df['StrikeRate'] == strike_float) &
            (options_df['ScripType'].str.upper() == option_type.upper()) &
            (options_df['NormalizedExpiry'] == target_expiry)
        )
        
        result = options_df[mask]
        
        if not result.empty:
            # Prefer NSE
            nse_results = result[result['Exch'] == 'N']
            if not nse_results.empty:
                return nse_results.iloc[0]['ScripCode'], None
            return result.iloc[0]['ScripCode'], None
        
        return None, f"Not found: {symbol_root}"
        
    except Exception as e:
        return None, f"Error: {str(e)}"

def get_future_scrip_code_fast(symbol, expiry_date, futures_df):
    """Optimized future lookup"""
    try:
        symbol_root = symbol.split()[0].strip().upper()
        target_expiry = normalize_expiry_date(expiry_date)
        
        mask = (
            (futures_df['SymbolRoot'].str.upper() == symbol_root) &
            (futures_df['NormalizedExpiry'] == target_expiry) &
            (futures_df['ScripType'] == 'XX')
        )
        
        result = futures_df[mask]
        
        if not result.empty:
            nse_results = result[result['Exch'] == 'N']
            if not nse_results.empty:
                return nse_results.iloc[0]['ScripCode'], None
            return result.iloc[0]['ScripCode'], None
        
        return None, f"Future not found: {symbol_root}"
        
    except Exception as e:
        return None, f"Error: {str(e)}"

# ============================================
# BATCH API CALLS (Parallel Processing)
# ============================================

def fetch_current_ltp_batch(scrip_code, scrip_df):
    """Simplified LTP fetch for parallel execution"""
    try:
        client = get_5paisa_client()
        if not client: 
            return scrip_code, None, None
        
        scrip_row = scrip_df[scrip_df['ScripCode'] == scrip_code]
        if scrip_row.empty:
            return scrip_code, None, None

        row_data = scrip_row.iloc[0]
        csv_exch = str(row_data.get('Exch', 'N')).strip().upper()
        csv_type = str(row_data.get('ExchType', 'D')).strip().upper()

        current_ltp = None
        prev_close = None
        
        end_daily = datetime.now().strftime('%Y-%m-%d')
        start_daily = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        
        # Try daily data
        try:
            df_daily = client.historical_data(csv_exch, csv_type, int(scrip_code), '1d', start_daily, end_daily)
            if df_daily is not None and not df_daily.empty:
                df_daily.columns = [c.strip() for c in df_daily.columns]
                if 'Close' in df_daily.columns and len(df_daily) >= 2:
                    prev_close = float(df_daily.iloc[-2]['Close'])
        except:
            pass
        
        # Try intraday data
        end_intraday = datetime.now().strftime('%Y-%m-%d')
        start_intraday = datetime.now().strftime('%Y-%m-%d')
        
        try:
            df_intraday = client.historical_data(csv_exch, csv_type, int(scrip_code), '1m', start_intraday, end_intraday)
            if df_intraday is not None and not df_intraday.empty:
                df_intraday.columns = [c.strip() for c in df_intraday.columns]
                if 'Close' in df_intraday.columns:
                    current_ltp = float(df_intraday.iloc[-1]['Close'])
        except:
            pass
        
        return scrip_code, current_ltp, prev_close
        
    except Exception as e:
        return scrip_code, None, None

def fetch_all_ltps_parallel(scrip_codes, scrip_df, max_workers=15):
    """Parallel LTP fetching"""
    results = {}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_current_ltp_batch, code, scrip_df): code for code in scrip_codes}
        
        for future in as_completed(futures):
            scrip_code, ltp, prev_close = future.result()
            results[scrip_code] = (ltp, prev_close)
    
    return results

# ============================================
# P/L CALCULATION
# ============================================

def calculate_position_pl(entry_price, current_price, qty, side):
    """Calculate P/L based on side (buy/sell)"""
    if side.lower() == 'buy':
        return (current_price - entry_price) * qty
    else:  # sell
        return (entry_price - current_price) * qty

def calculate_price_change_pct(prev_close, current_price):
    """Calculate percentage change"""
    if not prev_close or prev_close == 0:
        return 0
    return ((current_price - prev_close) / prev_close) * 100

def render_price_change_slider(pct_change):
    """Compact price change display"""
    display_pct = max(-100, min(100, pct_change))
    
    if pct_change > 0:
        bar_color = "#00ff00"
        emoji = "🟢"
    elif pct_change < 0:
        bar_color = "#ff0000"
        emoji = "🔴"
    else:
        bar_color = "#808080"
        emoji = "⚪"
    
    fill_pct = abs(display_pct) / 2
    
    if pct_change > 0:
        gradient = f"linear-gradient(to right, #1a1a1a 0%, #1a1a1a 50%, {bar_color}40 50%, {bar_color}80 {50 + fill_pct}%, #1a1a1a {50 + fill_pct}%, #1a1a1a 100%)"
    elif pct_change < 0:
        gradient = f"linear-gradient(to right, #1a1a1a 0%, #1a1a1a {50 - fill_pct}%, {bar_color}80 {50 - fill_pct}%, {bar_color}40 50%, #1a1a1a 50%, #1a1a1a 100%)"
    else:
        gradient = "#1a1a1a"
    
    slider_pos = 50 + (display_pct / 2)
    
    slider_html = f"""
    <div style='margin: 10px 0; padding: 8px; background-color: #0a0a0a; border-radius: 6px;'>
        <div style='position: relative; width: 100%; height: 16px; background: {gradient}; border-radius: 8px; border: 1px solid #333;'>
            <div style='position: absolute; left: 50%; width: 1px; height: 100%; background-color: #444; transform: translateX(-50%);'></div>
            <div style='position: absolute; left: {slider_pos}%; top: 50%; transform: translate(-50%, -50%); background-color: {bar_color}; width: 20px; height: 20px; border-radius: 50%; border: 2px solid #000; box-shadow: 0 0 8px {bar_color}80;'></div>
        </div>
        <div style='text-align: center; margin-top: 5px; font-weight: bold; color: {bar_color}; font-size: 13px;'>
            {emoji} {pct_change:+.2f}%
        </div>
    </div>
    """
    
    st.markdown(slider_html, unsafe_allow_html=True)

# ============================================
# MAIN APP
# ============================================

def app():
    st.markdown("<h2 style='text-align: center;'>💰 Fast P/L Tracker (Optimized)</h2>", unsafe_allow_html=True)
    st.markdown("---")

    # ==================== ACCOUNT SELECTION (SIDEBAR) ====================
    st.sidebar.title("🏦 Account Selection")
    st.sidebar.markdown("---")
    
    accounts = get_accounts()
    
    if not accounts:
        st.sidebar.error("❌ No active accounts found in database!")
        st.error("❌ Cannot load positions without an active account. Please add accounts to the database.")
        return
    
    # Create account display names
    account_options = {}
    for acc in accounts:
        display_name = f"{acc['holder_name']} ({acc['client_code']}) - {acc['broker']}"
        account_options[display_name] = acc
    
    # Account selection dropdown
    selected_display = st.sidebar.selectbox(
        "Select Account",
        options=list(account_options.keys()),
        key='account_selector'
    )
    
    # Get selected account details
    selected_account = account_options[selected_display]
    st.session_state.selected_account = selected_account
    st.session_state.selected_account_id = selected_account['account_id']
    
    # Display account info
    st.sidebar.success(f"✅ Selected: {selected_account['holder_name']}")
    st.sidebar.info(f"💼 Account ID: {selected_account['account_id']}")
    st.sidebar.info(f"🏢 Broker: {selected_account['broker']}")
    
    with st.sidebar.expander("📊 Account Details", expanded=False):
        st.write(f"**Capital:** ₹{selected_account.get('capital', 0):,.2f}")
        st.write(f"**Available:** ₹{selected_account.get('available_balance', 0):,.2f}")
        st.write(f"**Margin Used:** ₹{selected_account.get('margin_used', 0):,.2f}")
    
    st.sidebar.markdown("---")

    # ==================== LOAD DATA FOR SELECTED ACCOUNT ====================
    scrip_dict, scrip_df, options_df, futures_df = load_scrip_master()
    if not scrip_dict or (options_df.empty and futures_df.empty):
        st.error("⚠️ CSV not loaded!")
        st.stop()

    # ✅ Load positions for SELECTED ACCOUNT ONLY
    position_map = load_db_positions(st.session_state.selected_account_id)

    if not DB_AVAILABLE:
        st.error("❌ Database not available!")
        return
    
    if not position_map:
        st.info(f"📂 No positions found for account: {selected_account['holder_name']}")
        return

    # Separate options and futures
    options_positions = {}
    futures_positions = {}
    
    for k, v in position_map.items():
        instrument = str(v.get('instrument_type', '')).upper().strip()
        if instrument == 'OPTIONS':
            options_positions[k] = v
        elif instrument == 'FUTURES':
            futures_positions[k] = v
    
    st.info(f"📋 {len(position_map)} positions ({len(options_positions)} Options, {len(futures_positions)} Futures) for {selected_account['holder_name']}")
    
    # ============================================
    # STEP 1: Build scrip code list (Fast)
    # ============================================
    
    position_data = []
    
    with st.spinner("🔍 Resolving scrip codes..."):
        for label, data in {**options_positions, **futures_positions}.items():
            instrument = data.get('instrument_type', '').upper()
            symbol = data.get('extracted_symbol', data.get('symbol', ''))
            qty = abs(data.get('net_quantity', 0))
            side = 'buy' if data.get('net_quantity', 0) > 0 else 'sell'
            entry_price = float(data.get('avg_buy_price', 0)) if side == 'buy' else float(data.get('avg_sell_price', 0))
            expiry_date_str = normalize_expiry_date(data.get('extracted_expiry', data.get('expiry_date', '2026-01-27')))
            
            if instrument == 'OPTIONS':
                strike = data.get('extracted_strike', data.get('strike_price', 0.0))
                option_type = data.get('extracted_type', data.get('option_type', 'CE'))
                scrip_code, error = get_option_scrip_code_fast(symbol, strike, option_type, expiry_date_str, options_df)
                display_name = f"{symbol} {strike} {option_type}"
            else:  # FUTURES
                scrip_code, error = get_future_scrip_code_fast(symbol, expiry_date_str, futures_df)
                display_name = f"{symbol} FUT"
            
            if scrip_code:
                position_data.append({
                    'scrip_code': scrip_code,
                    'symbol': symbol,
                    'display_name': display_name,
                    'qty': qty,
                    'side': side,
                    'entry_price': entry_price,
                    'expiry': expiry_date_str,
                    'instrument': instrument
                })
    
    if not position_data:
        st.warning("⚠️ No valid scrip codes found")
        return
    
    # ============================================
    # STEP 2: Fetch ALL LTPs in PARALLEL (Fast!)
    # ============================================
    
    scrip_codes = [p['scrip_code'] for p in position_data]
    
    with st.spinner(f"📡 Fetching {len(scrip_codes)} prices in parallel..."):
        # Determine appropriate dataframe for each scrip
        combined_df = pd.concat([options_df, futures_df], ignore_index=True)
        ltp_results = fetch_all_ltps_parallel(scrip_codes, combined_df, max_workers=15)
    
    # ============================================
    # STEP 3: Display Results (Fast)
    # ============================================
    
    position_pls = []
    total_pl_placeholder = st.empty()
    
    st.markdown("---")
    
    for pos in position_data:
        scrip_code = pos['scrip_code']
        current_ltp, prev_close = ltp_results.get(scrip_code, (None, None))
        
        if current_ltp is not None and prev_close is not None:
            pl = calculate_position_pl(pos['entry_price'], current_ltp, pos['qty'], pos['side'])
            pct_change = calculate_price_change_pct(prev_close, current_ltp)
            position_pls.append(pl)
            
            pl_color = "#00ff00" if pl >= 0 else "#ff0000"
            pl_emoji = "🟢" if pl >= 0 else "🔴"
            price_diff = current_ltp - prev_close
            change_color = "#00ff00" if price_diff >= 0 else "#ff0000"
            
            with st.container():
                col1, col2 = st.columns([3, 1])
                
                with col1:
                    icon = "📊" if pos['instrument'] == 'OPTIONS' else "📈"
                    st.markdown(f"**{icon} {pos['display_name']} ({pos['side'].upper()})**")
                    st.caption(f"Qty: {pos['qty']:,} | Entry: ₹{pos['entry_price']:,.2f} | Exp: {pos['expiry']}")
                
                with col2:
                    st.markdown(f"<div style='text-align: right;'><span style='font-size: 22px; color: {pl_color}; font-weight: bold;'>{pl_emoji} ₹{pl:,.2f}</span></div>", unsafe_allow_html=True)
                    st.caption(f"LTP: ₹{current_ltp:,.2f}")
                    st.markdown(f"<p style='text-align: right; color: {change_color}; font-size: 12px; margin: 0;'>Chg: ₹{price_diff:+,.2f}</p>", unsafe_allow_html=True)
                
                render_price_change_slider(pct_change)
            st.markdown("---")
        else:
            st.warning(f"⚠️ LTP fetch failed for {pos['display_name']}")
            position_pls.append(0)
    
    # ============================================
    # TOTAL P/L SUMMARY
    # ============================================
    
    total_pl = sum(position_pls)
    total_color = "#00ff00" if total_pl >= 0 else "#ff0000"
    total_emoji = "🟢" if total_pl >= 0 else "🔴"
    
    total_pl_placeholder.markdown(f"""
    <div style='background: linear-gradient(135deg, {total_color}20 0%, {total_color}10 100%); 
                padding: 25px; 
                border-radius: 15px; 
                text-align: center;
                border: 2px solid {total_color};
                box-shadow: 0 0 20px {total_color}40;
                margin-bottom: 30px;'>
        <h1 style='color: {total_color}; margin: 0; font-size: 42px; text-shadow: 0 0 10px {total_color}60;'>
            {total_emoji} ₹{total_pl:,.2f}
        </h1>
        <p style='color: #aaa; margin: 10px 0 0 0; font-size: 14px;'>
            Total: {len(position_pls)} | 
            Profitable: {sum(1 for p in position_pls if p > 0)} | 
            Loss: {sum(1 for p in position_pls if p < 0)}
        </p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    st.set_page_config(
        page_title="Total P/L Tracker",
        page_icon="💰",
        layout="wide"
    )
    app()