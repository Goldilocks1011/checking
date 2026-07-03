import streamlit as st
import pandas as pd
import sys, os
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

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

# ============================================
# CACHING FUNCTIONS
# ============================================

@st.cache_data
def load_scrip_master():
    """Load CSV once and cache it"""
    try:
        df = pd.read_csv(CSV_PATH)
        
        # Filter futures only
        df_futures = df[(df['ExchType'].isin(['D', 'N', 'B'])) & 
                        (df['Expiry'].notna()) & 
                        (df['ScripType'] == 'XX')].copy()
        
        # Normalize expiry dates
        df_futures['NormalizedExpiry'] = df_futures['Expiry'].apply(normalize_expiry_date)
        
        return df_futures
    except FileNotFoundError:
        st.error(f"❌ CSV not found: {CSV_PATH}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def load_db_positions():
    """Cache positions for 1 minute"""
    if not DB_AVAILABLE: 
        return []
    try:
        return get_formatted_open_positions()
    except:
        return []

def get_5paisa_client():
    """Get 5paisa client"""
    try: 
        return get_client()
    except: 
        return None

def normalize_expiry_date(expiry_str):
    """Fast expiry normalization"""
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
# SCRIP CODE LOOKUP
# ============================================

def get_future_scrip_code(symbol, expiry_date, futures_df):
    """Get scrip code for a future contract"""
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

def get_next_month_expiry(current_expiry_str):
    """Calculate next month's expiry (last Thursday of next month)"""
    try:
        current_expiry = pd.to_datetime(current_expiry_str)
        
        # Move to next month
        next_month = current_expiry + relativedelta(months=1)
        
        # Find last Thursday of next month
        # Get last day of the month
        if next_month.month == 12:
            last_day = datetime(next_month.year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(next_month.year, next_month.month + 1, 1) - timedelta(days=1)
        
        # Find last Thursday
        # Thursday is weekday 3
        days_back = (last_day.weekday() - 3) % 7
        last_thursday = last_day - timedelta(days=days_back)
        
        return last_thursday.strftime('%Y-%m-%d')
    except:
        return None

# ============================================
# FETCH MARKET DATA (BID/ASK)
# ============================================

def fetch_market_feed(scrip_code, futures_df):
    """Fetch current bid/ask prices from 5paisa"""
    try:
        client = get_5paisa_client()
        if not client:
            return None, None
        
        scrip_row = futures_df[futures_df['ScripCode'] == scrip_code]
        if scrip_row.empty:
            return None, None
        
        row_data = scrip_row.iloc[0]
        csv_exch = str(row_data.get('Exch', 'N')).strip().upper()
        csv_type = str(row_data.get('ExchType', 'D')).strip().upper()
        
        # Fetch market feed (this should give bid/ask)
        # Note: 5paisa's fetch_market_feed method signature
        try:
            req_list = [{
                "Exch": csv_exch,
                "ExchType": csv_type,
                "ScripCode": int(scrip_code)
            }]
            
            response = client.fetch_market_feed(req_list)
            
            if response and 'Data' in response and len(response['Data']) > 0:
                data = response['Data'][0]
                
                # Extract bid and ask prices
                bid_price = float(data.get('BidRate', 0) or data.get('Bid', 0))
                ask_price = float(data.get('AskRate', 0) or data.get('Ask', 0))
                ltp = float(data.get('LastRate', 0) or data.get('LTP', 0))
                
                # If bid/ask not available, try to get from LTP
                if ask_price == 0 and ltp > 0:
                    ask_price = ltp
                if bid_price == 0 and ltp > 0:
                    bid_price = ltp
                
                return bid_price, ask_price
        except:
            pass
        
        # Fallback: try to get LTP from historical data
        try:
            end_intraday = datetime.now().strftime('%Y-%m-%d')
            start_intraday = datetime.now().strftime('%Y-%m-%d')
            
            df_intraday = client.historical_data(csv_exch, csv_type, int(scrip_code), '1m', start_intraday, end_intraday)
            if df_intraday is not None and not df_intraday.empty:
                df_intraday.columns = [c.strip() for c in df_intraday.columns]
                if 'Close' in df_intraday.columns:
                    ltp = float(df_intraday.iloc[-1]['Close'])
                    # Use LTP as both bid and ask (approximation)
                    return ltp, ltp
        except:
            pass
        
        return None, None
        
    except Exception as e:
        st.error(f"Error fetching market data: {str(e)}")
        return None, None

# ============================================
# ROLLOVER CALCULATION
# ============================================

def calculate_rollover_metrics(entry_price, current_bid, next_month_ask):
    """Calculate rollover metrics: Loss, Interest, and recommendations"""
    
    # C = Entry Price (Buy Price for current period)
    C = entry_price
    
    # B = Current Bid Price (Spot/Current Price - what you can sell at now)
    B = current_bid
    
    # N = Next Month Ask Price (Buy Price for next period)
    N = next_month_ask
    
    # Loss = C - B (how much you're down)
    loss = C - B
    
    # Interest/Difference = N - C (extra cost to rollover)
    interest = N - C
    
    # Calculate percentages
    loss_pct = (loss / C) * 100 if C > 0 else 0
    interest_pct = (interest / C) * 100 if C > 0 else 0
    
    # Total cost of rollover
    total_rollover_cost = loss + interest
    total_rollover_cost_pct = (total_rollover_cost / C) * 100 if C > 0 else 0
    
    return {
        'C': C,
        'B': B,
        'N': N,
        'loss': loss,
        'interest': interest,
        'loss_pct': loss_pct,
        'interest_pct': interest_pct,
        'total_rollover_cost': total_rollover_cost,
        'total_rollover_cost_pct': total_rollover_cost_pct
    }

# ============================================
# DISPLAY FUNCTIONS
# ============================================

def display_rollover_analysis(position_name, metrics, qty):
    """Display rollover analysis in a beautiful format"""
    
    st.markdown(f"### 📊 {position_name}")
    st.markdown("---")
    
    # The Rent Analogy Section
    st.markdown("""
    ### 🏠 **The Future Logic (The "Rent" Analogy)**
    
    Think of this like **renewing a lease**. You are comparing your old rent to the new rent.
    """)
    
    # Variables Section
    st.markdown("### 📋 **The Variables:**")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            label="**C** (Buy Price - Current Period)",
            value=f"₹{metrics['C']:,.2f}",
            help="You entered the trade at this price"
        )
    
    with col2:
        st.metric(
            label="**B** (Spot/Current Bid Price)",
            value=f"₹{metrics['B']:,.2f}",
            delta=f"{metrics['loss']:.2f}" if metrics['loss'] != 0 else None,
            delta_color="inverse",
            help="The current market price (what you can sell at now)"
        )
    
    with col3:
        st.metric(
            label="**N** (Next Month Ask Price)",
            value=f"₹{metrics['N']:,.2f}",
            help="The price to buy the contract for next month"
        )
    
    st.markdown("---")
    
    # Calculations Section
    st.markdown("### 🧮 **The Calculations:**")
    
    # 1. Find Loss
    loss_color = "🔴" if metrics['loss'] > 0 else "🟢" if metrics['loss'] < 0 else "⚪"
    
    st.markdown(f"""
    #### 1️⃣ **Find Loss:**
    
    **Formula:** `Loss = C - B`
    
    **Calculation:** `₹{metrics['C']:,.2f} - ₹{metrics['B']:,.2f} = ₹{metrics['loss']:,.2f}`
    
    {loss_color} **Meaning:** You are currently **{'down' if metrics['loss'] > 0 else 'up'}** ₹**{abs(metrics['loss']):,.2f}** ({metrics['loss_pct']:+.2f}%) on the trade.
    
    **Total Loss on Position:** ₹**{abs(metrics['loss'] * qty):,.2f}** (Qty: {qty})
    """)
    
    st.markdown("---")
    
    # 2. Find the Interest
    interest_color = "🔴" if metrics['interest'] > 0 else "🟢"
    
    st.markdown(f"""
    #### 2️⃣ **Find the "Interest" (Rollover Cost):**
    
    **Formula:** `D = N - C`
    
    **Calculation:** `₹{metrics['N']:,.2f} - ₹{metrics['C']:,.2f} = ₹{metrics['interest']:,.2f}`
    
    {interest_color} **Meaning:** To keep this position open for another month, you are effectively paying ₹**{abs(metrics['interest']):,.2f}** ({metrics['interest_pct']:+.2f}%) **{'extra' if metrics['interest'] > 0 else 'less'}** on top of your original entry price.
    
    **Total Rollover Cost:** ₹**{abs(metrics['interest'] * qty):,.2f}** (Qty: {qty})
    """)
    
    st.markdown("---")
    
    # 3. The Goal
    st.markdown(f"""
    #### 3️⃣ **The Goal:**
    
    🎯 You want **D** (rollover cost) to be as **low as possible**.
    
    **Current D:** ₹{metrics['interest']:,.2f}
    
    **Example:** If **N** (Next Month Price) drops to ₹{metrics['C'] + 0.50:.2f}, your **D** becomes only ₹**0.50**. 
    This is **much better** because you are paying less "interest" to extend the trade.
    """)
    
    # Total Rollover Summary
    st.markdown("---")
    st.markdown("### 💰 **Total Rollover Summary:**")
    
    total_cost_color = "#ff4444" if metrics['total_rollover_cost'] > 0 else "#44ff44"
    
    st.markdown(f"""
    <div style='background: linear-gradient(135deg, {total_cost_color}20 0%, {total_cost_color}10 100%); 
                padding: 20px; 
                border-radius: 12px; 
                border: 2px solid {total_cost_color};
                margin: 15px 0;'>
        <h3 style='color: {total_cost_color}; margin: 0;'>
            Total Cost to Rollover: ₹{abs(metrics['total_rollover_cost'] * qty):,.2f}
        </h3>
        <p style='color: #aaa; margin: 10px 0 0 0;'>
            Per Unit: ₹{metrics['total_rollover_cost']:,.2f} ({metrics['total_rollover_cost_pct']:+.2f}%)
        </p>
        <p style='color: #aaa; margin: 5px 0 0 0; font-size: 14px;'>
            = Current Loss (₹{abs(metrics['loss'] * qty):,.2f}) + Rollover Interest (₹{abs(metrics['interest'] * qty):,.2f})
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # Recommendation
    if metrics['interest'] < 0:
        recommendation = "✅ **FAVORABLE** - You're getting a discount on rollover!"
        rec_color = "#44ff44"
    elif metrics['interest'] < metrics['C'] * 0.005:  # Less than 0.5%
        recommendation = "✅ **REASONABLE** - Rollover cost is acceptable (< 0.5%)"
        rec_color = "#ffaa44"
    elif metrics['interest'] < metrics['C'] * 0.01:  # Less than 1%
        recommendation = "⚠️ **MODERATE** - Consider if rollover is worth it (0.5% - 1%)"
        rec_color = "#ffaa44"
    else:
        recommendation = "❌ **EXPENSIVE** - Rollover cost is high (> 1%). Consider exit."
        rec_color = "#ff4444"
    
    st.markdown(f"""
    <div style='background-color: {rec_color}20; 
                padding: 15px; 
                border-radius: 10px; 
                border-left: 4px solid {rec_color};
                margin: 20px 0;'>
        <h4 style='margin: 0; color: {rec_color};'>{recommendation}</h4>
    </div>
    """, unsafe_allow_html=True)

# ============================================
# MAIN APP
# ============================================

def app():
    st.markdown("<h1 style='text-align: center;'>🔄 Futures Rollover Analyzer</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #888;'>Analyze the cost of rolling over your futures positions to the next month</p>", unsafe_allow_html=True)
    st.markdown("---")
    
    # Load data
    futures_df = load_scrip_master()
    if futures_df.empty:
        st.error("⚠️ CSV not loaded!")
        st.stop()
    
    position_map = load_db_positions()
    
    if not DB_AVAILABLE:
        st.error("❌ Database not available!")
        return
    
    if not position_map:
        st.info("📂 No positions found in database")
        return
    
    # Filter only FUTURES positions
    futures_positions = {}
    for k, v in position_map.items():
        instrument = str(v.get('instrument_type', '')).upper().strip()
        if instrument == 'FUTURES':
            futures_positions[k] = v
    
    if not futures_positions:
        st.warning("📊 No futures positions found in database")
        return
    
    st.success(f"✅ Found {len(futures_positions)} futures position(s)")
    st.markdown("---")
    
    # Process each futures position
    for label, data in futures_positions.items():
        symbol = data.get('extracted_symbol', data.get('symbol', ''))
        qty = abs(data.get('net_quantity', 0))
        side = 'buy' if data.get('net_quantity', 0) > 0 else 'sell'
        entry_price = float(data.get('avg_buy_price', 0)) if side == 'buy' else float(data.get('avg_sell_price', 0))
        current_expiry = normalize_expiry_date(data.get('extracted_expiry', data.get('expiry_date', '2026-01-27')))
        
        # Only process BUY positions for rollover
        if side != 'buy':
            st.info(f"ℹ️ Skipping {symbol} - Rollover analysis only for BUY positions")
            continue
        
        with st.spinner(f"🔍 Analyzing {symbol} FUT..."):
            
            # Get current month scrip code
            current_scrip_code, error = get_future_scrip_code(symbol, current_expiry, futures_df)
            
            if not current_scrip_code:
                st.error(f"❌ Could not find scrip code for {symbol} (Expiry: {current_expiry})")
                continue
            
            # Get next month expiry
            next_expiry = get_next_month_expiry(current_expiry)
            
            if not next_expiry:
                st.error(f"❌ Could not calculate next month expiry for {symbol}")
                continue
            
            # Get next month scrip code
            next_scrip_code, error = get_future_scrip_code(symbol, next_expiry, futures_df)
            
            if not next_scrip_code:
                st.error(f"❌ Could not find scrip code for {symbol} next month (Expiry: {next_expiry})")
                continue
            
            # Fetch current month bid/ask
            current_bid, current_ask = fetch_market_feed(current_scrip_code, futures_df)
            
            # Fetch next month bid/ask
            next_bid, next_ask = fetch_market_feed(next_scrip_code, futures_df)
            
            if current_bid is None or next_ask is None:
                st.error(f"❌ Could not fetch market prices for {symbol}")
                st.info(f"Current Expiry: {current_expiry}, Next Expiry: {next_expiry}")
                continue
            
            # Calculate rollover metrics
            metrics = calculate_rollover_metrics(entry_price, current_bid, next_ask)
            
            # Display analysis
            position_name = f"{symbol} FUT - {side.upper()} {qty} @ ₹{entry_price:,.2f}"
            display_rollover_analysis(position_name, metrics, qty)
            
            st.markdown("---")
            st.markdown("---")
    
    st.markdown("---")
    st.markdown("<p style='text-align: center; color: #666; font-size: 12px;'>💡 Tip: Lower rollover cost (D) means better opportunity to extend your position</p>", unsafe_allow_html=True)

if __name__ == "__main__":
    app()