
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import sys, os

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

# HELPER FUNCTIONS
@st.cache_data
def load_scrip_master():
    try:
        df = pd.read_csv(CSV_PATH)
        df_stocks = df[df['Series'] == 'EQ'].copy()
        # ✅ FIXED: Include 'B' (BSE) exchange type
        df_options = df[((df['ExchType'] == 'D') | (df['ExchType'] == 'N') | (df['ExchType'] == 'B')) & (df['Expiry'].notna())].copy()
        scrip_dict = dict(zip(df_stocks['Name'], df_stocks['ScripCode']))
        return scrip_dict, df_stocks, df_options
    except FileNotFoundError:
        st.error(f"❌ CSV not found: {CSV_PATH}")
        return {}, pd.DataFrame(), pd.DataFrame()

@st.cache_data(ttl=300)
def load_db_positions(account_id=None):
    if not DB_AVAILABLE: return []
    try:
        return get_formatted_open_positions(account_id)
    except:
        return []

def get_5paisa_client():
    try: return get_client()
    except: return None

def fetch_spot_price(scrip_code, scrip_name):
    try:
        client = get_5paisa_client()
        if not client: 
            st.error("Client login failed")
            return None
            
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        
        # API Call
        df = client.historical_data('N', 'C', int(scrip_code), '1d', start, end)
        
        # Check 1: API returned None or Empty
        if df is None:
            st.error(f"API returned None for {scrip_name} (Code: {scrip_code})")
            return None
        if df.empty:
            st.error(f"API returned Empty Data for {scrip_name}")
            return None
            
        # Check 2: Check Columns
        if 'Datetime' not in df.columns:
            st.error(f"Missing 'Datetime' column. Got: {list(df.columns)}")
            return None

        df['Datetime'] = pd.to_datetime(df['Datetime'])
        latest = df.sort_values('Datetime').iloc[-1]
        return {'spot_price': latest['Close'], 'date': latest['Datetime'].strftime('%Y-%m-%d')}
        
    except Exception as e:
        st.error(f"CRITICAL ERROR for {scrip_name}: {str(e)}")
        return None

def normalize_expiry_date(expiry_str):
    """Normalize expiry date to consistent format"""
    if pd.isna(expiry_str):
        return None
    
    if isinstance(expiry_str, (datetime, pd.Timestamp)):
        return expiry_str.strftime('%Y-%m-%d')
    
    if isinstance(expiry_str, str):
        for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d-%b-%Y', '%d %b %Y']:
            try:
                dt = pd.to_datetime(expiry_str, format=fmt)
                return dt.strftime('%Y-%m-%d')
            except:
                continue
    
    return str(expiry_str)

def get_option_scrip_code(symbol, strike, option_type, expiry_date, options_df):
    """
    ✅ FIXED: Now returns (scrip_code, exch, exch_type) tuple to handle BSE
    ✅ PREFERS NSE EXCHANGE when both NSE and BSE are available (like totalPL.py)
    """
    try:
        symbol_root = symbol.split()[0].strip().upper()
        target_expiry = normalize_expiry_date(expiry_date)
        strike_float = float(strike)
        
        # Normalize expiry in dataframe for comparison
        if 'NormalizedExpiry' not in options_df.columns:
            options_df['NormalizedExpiry'] = options_df['Expiry'].apply(normalize_expiry_date)
        
        # Try SymbolRoot first (more reliable for BSE)
        if 'SymbolRoot' in options_df.columns:
            mask = ((options_df['SymbolRoot'].str.upper() == symbol_root) &
                    (options_df['StrikeRate'] == strike_float) &
                    (options_df['ScripType'].str.upper() == option_type.upper()) &
                    (options_df['NormalizedExpiry'] == target_expiry))
            result = options_df[mask]
            
            if not result.empty:
                # ✅ PREFER NSE OVER BSE (same logic as totalPL.py line 118-120)
                nse_results = result[result['Exch'] == 'N']
                if not nse_results.empty:
                    row = nse_results.iloc[0]
                else:
                    row = result.iloc[0]
                    
                scrip_code = row['ScripCode']
                exch = str(row.get('Exch', 'N')).strip().upper()
                exch_type = str(row.get('ExchType', 'D')).strip().upper()
                return scrip_code, exch, exch_type
        
        # Fallback to Name column
        mask = ((options_df['Name'].str.contains(symbol_root, case=False, na=False)) &
                (options_df['StrikeRate'] == strike_float) &
                (options_df['Name'].str.contains(option_type.upper(), case=False, na=False)) &
                (options_df['NormalizedExpiry'] == target_expiry))
        result = options_df[mask]
        
        if not result.empty:
            # ✅ PREFER NSE OVER BSE
            nse_results = result[result['Exch'] == 'N']
            if not nse_results.empty:
                row = nse_results.iloc[0]
            else:
                row = result.iloc[0]
                
            scrip_code = row['ScripCode']
            exch = str(row.get('Exch', 'N')).strip().upper()
            exch_type = str(row.get('ExchType', 'D')).strip().upper()
            return scrip_code, exch, exch_type
        
        return None, None, None
    except Exception as e:
        st.warning(f"Error finding option {symbol} {strike} {option_type}: {str(e)}")
        return None, None, None

def fetch_historical_option_price(scrip_code, exch, exch_type, days=30):
    """
    ✅ FIXED: Now accepts exch and exch_type parameters to handle BSE
    """
    try:
        client = get_5paisa_client()
        if not client: return None
        end = datetime.now().strftime('%Y-%m-%d')
        buffer = min(days + 30, 180)
        start = (datetime.now() - timedelta(days=buffer)).strftime('%Y-%m-%d')
        
        # ✅ FIXED: Use the provided exchange and exchange type instead of hardcoded 'N', 'D'
        df = client.historical_data(exch, exch_type, int(scrip_code), '1d', start, end)
        
        if df is None or df.empty: return None
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime')
        cutoff = datetime.now() - timedelta(days=days)
        return df[df['Datetime'] >= cutoff]
    except:
        return None

def calculate_historical_strategy_pl_with_api(legs, company_symbol, expiry_date, options_df, days=30):
    """
    ✅ FIXED: Updated to handle exchange info from get_option_scrip_code
    """
    try:
        leg_scrips = []
        for leg in legs:
            # ✅ Now receives (scrip_code, exch, exch_type) tuple
            scrip_code, exch, exch_type = get_option_scrip_code(
                company_symbol, 
                leg['strike'], 
                'CE' if leg['type'] == 'call' else 'PE', 
                expiry_date, 
                options_df
            )
            leg_scrips.append({
                'leg': leg, 
                'scrip_code': scrip_code,
                'exch': exch,
                'exch_type': exch_type
            })
        
        historical_dfs = []
        for ls in leg_scrips:
            if ls['scrip_code'] and ls['exch'] and ls['exch_type']:
                # ✅ Pass exchange info to fetch function
                df = fetch_historical_option_price(
                    ls['scrip_code'], 
                    ls['exch'], 
                    ls['exch_type'], 
                    days
                )
                if df is not None:
                    historical_dfs.append({'leg': ls['leg'], 'df': df})
        
        if not historical_dfs: return None
        
        common_dates = set(historical_dfs[0]['df']['Datetime'])
        for h in historical_dfs[1:]:
            common_dates = common_dates.intersection(set(h['df']['Datetime']))
        if not common_dates: return None
        
        pl_data = []
        for date in sorted(list(common_dates)):
            total_pl = 0
            for h in historical_dfs:
                date_data = h['df'][h['df']['Datetime'] == date]
                if not date_data.empty:
                    curr = date_data.iloc[0]['Close']
                    entry = h['leg']['entry_premium']
                    qty = h['leg']['qty']
                    pl = (curr - entry) * qty if h['leg']['position'] == 'buy' else (entry - curr) * qty
                    total_pl += pl
            pl_data.append({'Datetime': date, 'pl': total_pl})
        return pd.DataFrame(pl_data)
    except:
        return None

def calculate_pl_at_expiry(legs, spot_price):
    """Calculate P/L at a given spot price at expiry"""
    total_pl = 0
    for leg in legs:
        intrinsic = max(spot_price - leg['strike'], 0) if leg['type'] == 'call' else max(leg['strike'] - spot_price, 0)
        pl = (intrinsic - leg['entry_premium']) * leg['qty'] if leg['position'] == 'buy' else (leg['entry_premium'] - intrinsic) * leg['qty']
        total_pl += pl
    return total_pl

def calculate_breakevens_mathematically(legs):
    calls = [l for l in legs if l['type'] == 'call']
    puts = [l for l in legs if l['type'] == 'put']
    total_premium = sum((l['entry_premium'] * l['qty'] * (1 if l['position'] == 'sell' else -1)) for l in legs)
    breakevens = []
    
    if calls:
        qty = sum(c['qty'] for c in calls)
        w_strike = sum(c['qty'] * c['strike'] for c in calls)
        upper_be = (w_strike + total_premium) / qty
        breakevens.append(('Upper', upper_be))
    
    if puts:
        qty = sum(p['qty'] for p in puts)
        w_strike = sum(p['qty'] * p['strike'] for p in puts)
        lower_be = (w_strike - total_premium) / qty
        breakevens.append(('Lower', lower_be))
    
    return breakevens

def calculate_max_profit_loss(legs, spots):
    """Calculate max profit/loss and their spot prices"""
    pls = [calculate_pl_at_expiry(legs, s) for s in spots]
    max_pl = max(pls)
    min_pl = min(pls)
    max_pl_spot = spots[pls.index(max_pl)]
    min_pl_spot = spots[pls.index(min_pl)]
    return max_pl, min_pl, max_pl_spot, min_pl_spot

def create_strategy_pl_chart(df, scrip_name, days, breakevens=None, spot_price=None):
    """Historical P/L Chart (30 days)"""
    fig = go.Figure()
    
    # P/L Line
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['pl'], mode='lines', name='P/L',
                            line=dict(color='#2196f3', width=3), fill='tozeroy', 
                            fillcolor='rgba(33,150,243,0.2)', hovertemplate='P/L: ₹%{y:,.2f}'))
    
    # Zero line
    fig.add_hline(y=0, line_dash="dash", line_color="#ffc107", annotation_text="Zero P/L")
    
    # Breakeven lines in legend
    if breakevens:
        for be_type, be_val in breakevens:
            color = '#ff5252' if be_type == 'Upper' else '#ff9800'
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode='markers',
                marker=dict(color=color, size=12, symbol='diamond'),
                name=f'🎯 {be_type} BE: ₹{be_val:,.2f}',
                hoverinfo='skip'
            ))
    
    # Current Spot in legend
    if spot_price:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode='markers',
            marker=dict(color='#4caf50', size=12, symbol='star'),
            name=f'📍 Current Spot: ₹{spot_price:,.2f}',
            hoverinfo='skip'
        ))
    
    fig.update_layout(
        title=f'📊 Historical P/L - Last {days} Days',
        xaxis_title='Date', yaxis_title='P/L (₹)', 
        template='plotly_dark', height=450, margin=dict(b=80),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return fig

def create_payoff_diagram(legs, current_spot, breakevens=None):
    """Enhanced Payoff Diagram at Expiry with Max Profit Zone"""
    strikes = [l['strike'] for l in legs]
    min_s, max_s = min(strikes), max(strikes)
    diff = max_s - min_s
    
    # Ensure current spot is included in range
    range_min = min(min_s - diff * 0.5, current_spot - diff * 0.5)
    range_max = max(max_s + diff * 0.5, current_spot + diff * 0.5)
    
    spots = np.linspace(range_min, range_max, 300)
    pls = [calculate_pl_at_expiry(legs, s) for s in spots]
    
    # Calculate max profit/loss
    max_pl, min_pl, max_pl_spot, min_pl_spot = calculate_max_profit_loss(legs, spots)
    
    fig = go.Figure()
    
    # Add profit zone (green) and loss zone (red) backgrounds
    profit_mask = np.array(pls) > 0
    loss_mask = np.array(pls) < 0
    
    # Profit zone fill
    if any(profit_mask):
        profit_spots = spots[profit_mask]
        profit_pls = np.array(pls)[profit_mask]
        fig.add_trace(go.Scatter(
            x=profit_spots, y=profit_pls,
            fill='tozeroy', fillcolor='rgba(76, 175, 80, 0.2)',
            line=dict(width=0), showlegend=False, hoverinfo='skip'
        ))
    
    # Loss zone fill
    if any(loss_mask):
        loss_spots = spots[loss_mask]
        loss_pls = np.array(pls)[loss_mask]
        fig.add_trace(go.Scatter(
            x=loss_spots, y=loss_pls,
            fill='tozeroy', fillcolor='rgba(244, 67, 54, 0.2)',
            line=dict(width=0), showlegend=False, hoverinfo='skip'
        ))
    
    # Main P/L curve
    fig.add_trace(go.Scatter(
        x=spots, y=pls, mode='lines', name='P/L at Expiry',
        line=dict(color='#2196f3', width=3),
        hovertemplate='Spot: ₹%{x:,.2f}<br>P/L: ₹%{y:,.2f}<extra></extra>'
    ))
    
    # Zero line
    fig.add_hline(y=0, line_dash="dash", line_color="#ffc107", line_width=2)
    
    # Current spot - THICK GREEN LINE
    fig.add_vline(
        x=current_spot, line_dash="dot", line_color="#4caf50", line_width=3,
        annotation=dict(
            text=f"📍 Spot: ₹{current_spot:,.2f}",
            font=dict(size=12, color="#4caf50"),
            showarrow=False, yref="paper", y=0.95
        )
    )
    
    # Max Profit Point - STAR MARKER
    fig.add_trace(go.Scatter(
        x=[max_pl_spot], y=[max_pl],
        mode='markers+text',
        marker=dict(color='#4caf50', size=15, symbol='star'),
        text=[f'Max Profit<br>₹{max_pl:,.0f}'],
        textposition='top center',
        textfont=dict(size=10, color='#4caf50'),
        name='Max Profit',
        hovertemplate=f'Spot: ₹{max_pl_spot:,.2f}<br>Max Profit: ₹{max_pl:,.2f}<extra></extra>'
    ))
    
    # Max Loss Point - TRIANGLE DOWN MARKER
    fig.add_trace(go.Scatter(
        x=[min_pl_spot], y=[min_pl],
        mode='markers+text',
        marker=dict(color='#f44336', size=15, symbol='triangle-down'),
        text=[f'Max Loss<br>₹{min_pl:,.0f}'],
        textposition='bottom center',
        textfont=dict(size=10, color='#f44336'),
        name='Max Loss',
        hovertemplate=f'Spot: ₹{min_pl_spot:,.2f}<br>Max Loss: ₹{min_pl:,.2f}<extra></extra>'
    ))
    
    # Breakeven points - RED DASHED LINES
    if breakevens:
        for be_type, be_val in breakevens:
            fig.add_vline(
                x=be_val, line_dash="dash", line_color="#ff5252", line_width=2,
                annotation=dict(
                    text=f"{be_type} BE<br>₹{be_val:,.2f}",
                    font=dict(size=10, color="#ff5252"),
                    showarrow=False, yref="paper", y=0.85 if be_type == 'Lower' else 0.75
                )
            )
            fig.add_trace(go.Scatter(
                x=[be_val], y=[0], mode='markers',
                marker=dict(color='#ff5252', size=12, symbol='diamond'),
                name=f'{be_type} BE',
                showlegend=True,
                hovertemplate=f'{be_type} BE: ₹{be_val:,.2f}<extra></extra>'
            ))
    
    fig.update_layout(
        title="📈 Payoff at Expiry (Breakeven Analysis)",
        xaxis_title="Spot Price (₹)", yaxis_title="P/L (₹)",
        template='plotly_dark', height=450, hovermode='x unified',
        margin=dict(b=100),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5)
    )
    
    return fig

def group_positions_by_company(position_map):
    """Group all positions by company symbol (Safe Version) - OPTIONS ONLY"""
    company_groups = {}
    
    for label, data in position_map.items():
        # ✅ CRITICAL FIX: Skip FUTURES positions - only process OPTIONS
        instrument_type = str(data.get('instrument_type', '')).upper().strip()
        if instrument_type != 'OPTIONS':
            continue  # Skip futures and other instruments
        
        # Safe symbol extraction
        symbol = data.get('extracted_symbol') or data.get('symbol') or "UNKNOWN"
        
        if symbol not in company_groups:
            company_groups[symbol] = []
        
        # 1. Safe Float Conversion for Strike
        raw_strike = data.get('extracted_strike', 0.0)
        try:
            leg_strike = float(raw_strike) if raw_strike is not None else 0.0
        except:
            leg_strike = 0.0

        # 2. Safe Type Extraction
        leg_type = data.get('extracted_type', 'CE')
        
        # 3. Safe Quantity Extraction
        raw_qty = data.get('net_quantity', 0)
        try:
            leg_qty = abs(int(raw_qty)) if raw_qty is not None else 0
        except:
            leg_qty = 0
            
        leg_side = 'buy' if (raw_qty is not None and raw_qty > 0) else 'sell'
        
        # 4. CRITICAL FIX: Safe Price Extraction (Prevent NoneType error)
        try:
            if leg_side == 'buy':
                raw_price = data.get('avg_buy_price')
            else:
                raw_price = data.get('avg_sell_price')
                
            # If price is None, default to 0.0
            leg_price = float(raw_price) if raw_price is not None else 0.0
        except:
            leg_price = 0.0
        
        # 5. Safe Expiry Handling
        expiry_date_str = data.get('extracted_expiry', '2026-01-26')
        if isinstance(expiry_date_str, (datetime, pd.Timestamp)):
            expiry_date_str = expiry_date_str.strftime('%Y-%m-%d')
        elif hasattr(expiry_date_str, 'strftime'):
            expiry_date_str = expiry_date_str.strftime('%Y-%m-%d')
        
        company_groups[symbol].append({
            'strike': leg_strike,
            'type': 'call' if str(leg_type).upper() == 'CE' else 'put',
            'position': leg_side,
            'entry_premium': leg_price,
            'qty': leg_qty,
            'symbol_root': symbol,
            'expiry_str': str(expiry_date_str)
        })
    
    return company_groups

# MAIN UI
def app():
    st.markdown("<h3 style='text-align: center;'>💰 Multi-Leg Options Strategy Tracker</h3>", unsafe_allow_html=True)
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

    scrip_dict, scrip_df, options_df = load_scrip_master()
    if not scrip_dict or options_df.empty:
        st.error("⚠️ CSV not loaded!")
        st.stop()

    position_map = load_db_positions(st.session_state.selected_account_id)

    # Session state initialization
    if 'auto_generated' not in st.session_state:
        st.session_state.auto_generated = False

    # AUTO-GENERATE GRAPHS ON STARTUP
    if DB_AVAILABLE and position_map:
        company_groups = group_positions_by_company(position_map)
        
        st.info(f"🔄 Loading {len(company_groups)} company strategies...")
        
        # Display all graphs automatically
        for company, legs in company_groups.items():
            if legs:
                ref_expiry = legs[0]['expiry_str']
                
                st.markdown(f"## 📊 {company} Strategy")
                st.markdown(f"**Legs:** {len(legs)} | **Expiry:** {ref_expiry}")
                
                # Show leg details
                with st.expander("📋 View Legs", expanded=False):
                    for i, leg in enumerate(legs, 1):
                        st.caption(f"{i}. {leg['position'].upper()} {leg['type'].upper()} {leg['strike']:,.0f} @ ₹{leg['entry_premium']:,.2f} × {leg['qty']:,}")
                
                # Calculate breakevens
                breakevens = calculate_breakevens_mathematically(legs)
                
                # Get current spot price
                spot_price = None
                if company in scrip_dict:
                    spot_data = fetch_spot_price(scrip_dict[company], company)
                    if spot_data:
                        spot_price = spot_data['spot_price']
                
                # TWO GRAPHS SIDE BY SIDE
                with st.spinner(f'Fetching {company} data...'):
                    hist_df = calculate_historical_strategy_pl_with_api(legs, company, ref_expiry, options_df, days=30)
                    
                    if hist_df is not None and not hist_df.empty:
                        # Create two columns for side-by-side graphs
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            # LEFT: Historical P/L Chart
                            fig_hist = create_strategy_pl_chart(hist_df, company, 30, breakevens, spot_price)
                            st.plotly_chart(fig_hist, use_container_width=True)
                        
                        with col2:
                            # RIGHT: Enhanced Payoff Diagram
                            if spot_price:
                                fig_payoff = create_payoff_diagram(legs, spot_price, breakevens)
                                st.plotly_chart(fig_payoff, use_container_width=True)
                            else:
                                st.warning("⚠️ Spot price unavailable for payoff diagram")
                        
                        # Metrics row below graphs
                        cols = st.columns(5)
                        cols[0].metric("Current P/L", f"₹{hist_df['pl'].iloc[-1]:,.2f}")
                        cols[1].metric("Max Profit (30d)", f"₹{hist_df['pl'].max():,.2f}")
                        cols[2].metric("Max Loss (30d)", f"₹{hist_df['pl'].min():,.2f}")
                        if spot_price:
                            cols[3].metric("Spot Price", f"₹{spot_price:,.2f}")
                        if breakevens:
                            be_text = " | ".join([f"{be_type}: ₹{be_val:,.0f}" for be_type, be_val in breakevens])
                            cols[4].metric("Breakevens", be_text)
                    else:
                        st.warning(f"⚠️ No historical data available for {company}")
                
                st.markdown("---")
        
        st.session_state.auto_generated = True
        st.success(f"✅ Loaded {len(company_groups)} strategies!")
    
    elif not DB_AVAILABLE:
        st.error("❌ Database not available!")
    elif not position_map:
        st.info("📂 No positions found in database")
    else:
        st.success("✅ All strategies loaded!")

    if __name__ == "__main__":
        app()