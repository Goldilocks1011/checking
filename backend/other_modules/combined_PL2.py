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

# HELPER FUNCTIONS
@st.cache_data
def load_scrip_master():
    try:
        df = pd.read_csv(CSV_PATH)
        df_stocks = df[df['Series'] == 'EQ'].copy()
        df_options = df[((df['ExchType'] == 'D') | (df['ExchType'] == 'N')) & (df['Expiry'].notna())].copy()
        scrip_dict = dict(zip(df_stocks['Name'], df_stocks['ScripCode']))
        return scrip_dict, df_stocks, df_options
    except FileNotFoundError:
        st.error(f"❌ CSV not found: {CSV_PATH}")
        return {}, pd.DataFrame(), pd.DataFrame()

@st.cache_data(ttl=300)
def load_db_positions():
    if not DB_AVAILABLE: return []
    try:
        return get_formatted_open_positions()
    except:
        return []

def get_5paisa_client():
    try: return get_client()
    except: return None

def fetch_spot_price(scrip_code, scrip_name):
    try:
        client = get_5paisa_client()
        if not client: return None
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        df = client.historical_data('N', 'C', int(scrip_code), '1d', start, end)
        if df is None or df.empty: return None
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        latest = df.sort_values('Datetime').iloc[-1]
        return {'spot_price': latest['Close'], 'date': latest['Datetime'].strftime('%Y-%m-%d')}
    except: return None

def get_option_scrip_code(symbol, strike, option_type, expiry_date, options_df):
    try:
        symbol_root = symbol.split()[0].strip().upper()
        mask = ((options_df['Name'].str.contains(symbol_root, case=False, na=False)) &
                (options_df['StrikeRate'] == strike) &
                (options_df['Name'].str.contains(option_type.upper(), case=False, na=False)) &
                (options_df['Expiry'] == expiry_date))
        result = options_df[mask]
        if not result.empty: return result.iloc[0]['ScripCode']
        if 'SymbolRoot' in options_df.columns:
            mask = ((options_df['SymbolRoot'] == symbol_root) &
                    (options_df['StrikeRate'] == strike) &
                    (options_df['ScripType'] == option_type.upper()) &
                    (options_df['Expiry'] == expiry_date))
            result = options_df[mask]
            if not result.empty: return result.iloc[0]['ScripCode']
        return None
    except: return None

def fetch_option_ltp(scrip_code):
    try:
        client = get_5paisa_client()
        if not client: return None
        date = datetime.now().strftime('%Y-%m-%d')
        df = client.historical_data('N', 'D', int(scrip_code), '1m', date, date)
        if df is None or df.empty:
            start = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
            df = client.historical_data('N', 'D', int(scrip_code), '1d', start, date)
        if df is None or df.empty: return None
        return df.sort_values('Datetime').iloc[-1]['Close']
    except: return None

def fetch_historical_option_price(scrip_code, days=30):
    try:
        client = get_5paisa_client()
        if not client: return None
        end = datetime.now().strftime('%Y-%m-%d')
        buffer = min(days + 30, 180)
        start = (datetime.now() - timedelta(days=buffer)).strftime('%Y-%m-%d')
        df = client.historical_data('N', 'D', int(scrip_code), '1d', start, end)
        if df is None or df.empty: return None
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime')
        cutoff = datetime.now() - timedelta(days=days)
        return df[df['Datetime'] >= cutoff]
    except: return None

def calculate_strategy_pl_with_api(legs, company_symbol, expiry_date, options_df):
    total_pl = 0
    leg_details = []
    with st.spinner('🔄 Fetching prices...'):
        for idx, leg in enumerate(legs):
            strike, option_type = leg['strike'], 'CE' if leg['type'] == 'call' else 'PE'
            position, entry_premium, qty = leg['position'], leg['entry_premium'], leg['qty']
            
            option_scrip = get_option_scrip_code(company_symbol, strike, option_type, expiry_date, options_df)
            current_premium = fetch_option_ltp(option_scrip) if option_scrip else None
            
            if current_premium is None:
                current_premium = entry_premium
                api_status = "Failed" if option_scrip else "Not Found"
            else:
                api_status = "Success"
            
            pl = (current_premium - entry_premium) * qty if position == 'buy' else (entry_premium - current_premium) * qty
            total_pl += pl
            
            leg_details.append({
                'strike': strike, 'type': leg['type'], 'position': position,
                'entry_premium': entry_premium, 'current_premium': current_premium,
                'qty': qty, 'pl': pl, 'api_status': api_status
            })
    return total_pl, leg_details

def calculate_pl_at_expiry(legs, spot_price):
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
    breakevens, calculations = [], []
    
    if calls:
        calc = ["📈 UPPER BREAKEVEN", "=" * 60, "Formula: Σ[Qty × (X - Strike)] = Total Premium"]
        qty = sum(c['qty'] for c in calls)
        w_strike = sum(c['qty'] * c['strike'] for c in calls)
        calc.append(f"Equation: {qty:,}X - {w_strike:,.2f} = {total_premium:,.2f}")
        upper_be = (w_strike + total_premium) / qty
        calc.append(f"✅ Upper BE = ₹{upper_be:,.2f}")
        breakevens.append(('Upper', upper_be))
        calculations.append('\n'.join(calc))
    
    if puts:
        calc = ["📉 LOWER BREAKEVEN", "=" * 60, "Formula: Σ[Qty × (Strike - Y)] = Total Premium"]
        qty = sum(p['qty'] for p in puts)
        w_strike = sum(p['qty'] * p['strike'] for p in puts)
        calc.append(f"Equation: {w_strike:,.2f} - {qty:,}Y = {total_premium:,.2f}")
        lower_be = (w_strike - total_premium) / qty
        calc.append(f"✅ Lower BE = ₹{lower_be:,.2f}")
        breakevens.append(('Lower', lower_be))
        calculations.append('\n'.join(calc))
    
    return breakevens, total_premium, calculations

def verify_breakeven(legs, be_value, be_type, total_premium):
    verification = [f"🔍 Verification at {be_type} BE = ₹{be_value:,.2f}", "=" * 60, f"Premium: ₹{total_premium:,.2f}", "Losses:"]
    total_loss = 0
    for i, leg in enumerate(legs, 1):
        if leg['type'] == 'call' and be_value > leg['strike']:
            loss = leg['qty'] * (be_value - leg['strike'])
            verification.append(f" {i}. CALL {leg['strike']:,.0f}: ₹{loss:,.2f}")
            total_loss += loss
        elif leg['type'] == 'put' and be_value < leg['strike']:
            loss = leg['qty'] * (leg['strike'] - be_value)
            verification.append(f" {i}. PUT {leg['strike']:,.0f}: ₹{loss:,.2f}")
            total_loss += loss
    net_pl = total_premium - total_loss
    verification.append(f"Net P/L = ₹{net_pl:,.2f}")
    verification.append("✅ VERIFIED" if abs(net_pl) < 1 else f"⚠️ Diff: ₹{net_pl:,.2f}")
    return '\n'.join(verification)

def check_unlimited_risk(legs):
    short = sum(l['qty'] for l in legs if l['position'] == 'sell' and l['type'] == 'call')
    long = sum(l['qty'] for l in legs if l['position'] == 'buy' and l['type'] == 'call')
    return short > long

def calculate_max_profit_loss(legs):
    strikes = [l['strike'] for l in legs]
    min_s, max_s = min(strikes), max(strikes)
    diff = max_s - min_s
    spots = np.linspace(max(min_s - diff, 1), max_s + diff * 2, 1000)
    pls = [calculate_pl_at_expiry(legs, s) for s in spots]
    return {
        'max_profit': max(pls), 'max_loss': min(pls),
        'max_profit_spot': spots[pls.index(max(pls))],
        'max_loss_spot': spots[pls.index(min(pls))],
        'has_unlimited_risk': check_unlimited_risk(legs)
    }

def calculate_historical_strategy_pl_with_api(legs, company_symbol, expiry_date, options_df, days=30):
    try:
        leg_scrips = []
        for leg in legs:
            scrip = get_option_scrip_code(company_symbol, leg['strike'], 'CE' if leg['type'] == 'call' else 'PE', expiry_date, options_df)
            leg_scrips.append({'leg': leg, 'scrip_code': scrip})
        
        historical_dfs = []
        with st.spinner(f'🔄 Fetching {len(legs)} legs × {days} days...'):
            for ls in leg_scrips:
                if ls['scrip_code']:
                    df = fetch_historical_option_price(ls['scrip_code'], days)
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
    except: return None

def create_payoff_diagram(legs, current_spot, breakevens=None):
    strikes = [l['strike'] for l in legs]
    min_s, max_s = min(strikes), max(strikes)
    diff = max_s - min_s
    spots = np.linspace(max(min_s - diff * 0.3, 1), max_s + diff * 0.3, 200)
    pls = [calculate_pl_at_expiry(legs, s) for s in spots]
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=spots, y=pls, mode='lines', name='P/L', line=dict(color='#2196f3', width=3),
                            fill='tozeroy', fillcolor='rgba(33,150,243,0.2)', hovertemplate='₹%{x:,.2f}<br>P/L: ₹%{y:,.2f}<extra></extra>'))
    fig.add_hline(y=0, line_dash="dash", line_color="#ffc107")
    fig.add_vline(x=current_spot, line_dash="dot", line_color="#4caf50", annotation_text=f"Spot: ₹{current_spot:,.2f}")
    
    if breakevens:
        for be_type, be_val in breakevens:
            fig.add_vline(x=be_val, line_dash="dash", line_color="red", annotation_text=f"{be_type} BE: ₹{be_val:,.2f}")
            fig.add_trace(go.Scatter(x=[be_val], y=[0], mode='markers', marker=dict(color='red', size=12), name=f'{be_type} BE'))
    
    fig.update_layout(title="Payoff at Expiry", xaxis_title="Spot (₹)", yaxis_title="P/L (₹)",
                     template='plotly_dark', height=500, hovermode='x unified', margin=dict(b=100))
    return fig

def create_strategy_pl_chart(df, scrip_name, strategy_name, days, breakevens=None):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['pl'], mode='lines', name='P/L',
                            line=dict(color='#2196f3', width=3), fill='tozeroy', hovertemplate='P/L: ₹%{y:,.2f}'))
    fig.add_hline(y=0, line_dash="dash", line_color="#ffc107")
    
    if breakevens:
        for be_type, be_val in breakevens:
            fig.add_trace(go.Scatter(x=[None], y=[None], mode='markers', marker=dict(color='red', size=12, symbol='diamond'),
                                    name=f'🎯 {be_type} BE: ₹{be_val:,.2f}', hoverinfo='skip'))
    
    fig.update_layout(title=f'{scrip_name} - {strategy_name} - Last {days} Days',
                     xaxis_title='Date', yaxis_title='P/L (₹)', template='plotly_dark', height=550, margin=dict(b=120))
    return fig

# MAIN UI
def app():
        
    st.markdown("<h3 style='text-align: center;'>💰 Multi-Leg Options Strategy Tracker</h3>", unsafe_allow_html=True)
    st.markdown("---")

    scrip_dict, scrip_df, options_df = load_scrip_master()
    if not scrip_dict or options_df.empty:
        st.error("⚠️ CSV not loaded!")
        st.stop()

    position_map = load_db_positions()

    if 'strategy_legs' not in st.session_state: st.session_state.strategy_legs = []
    if 'strategy_graphs' not in st.session_state: st.session_state.strategy_graphs = []
    if 'current_strategy_data' not in st.session_state: st.session_state.current_strategy_data = None

    with st.sidebar:
        st.header("🔎 Select Stock")
        
        if DB_AVAILABLE and position_map:
            input_mode = st.radio("Source:", ["📂 My Positions", "🌐 NSE Stocks"], label_visibility="collapsed")
        else:
            input_mode = "🌐 NSE Stocks"
        
        st.markdown("---")
        
        company_name = None
        expiry_date_str = None
        
        # MODE 1: DB POSITIONS
        if input_mode == "📂 My Positions":
            st.subheader("Select Position")
            selected_label = st.selectbox("Available:", options=list(position_map.keys()))
            
            if selected_label:
                data = position_map[selected_label]
                leg_symbol = data.get('extracted_symbol', data['symbol'])
                leg_strike = data.get('extracted_strike', 0.0)
                leg_type = data.get('extracted_type', 'CE')
                leg_qty = abs(data['net_quantity'])
                leg_side = 'buy' if data['net_quantity'] > 0 else 'sell'
                leg_price = float(data['avg_buy_price']) if leg_side == 'buy' else float(data['avg_sell_price'])
                
                # Get expiry and ensure it's a string
                expiry_date_str = data.get('extracted_expiry', '2026-01-26')
                if isinstance(expiry_date_str, (datetime, pd.Timestamp)):
                    expiry_date_str = expiry_date_str.strftime('%Y-%m-%d')
                elif hasattr(expiry_date_str, 'strftime'):
                    expiry_date_str = expiry_date_str.strftime('%Y-%m-%d')
                
                st.info(f"**{leg_symbol}**\n{leg_side.upper()} {leg_qty}x {leg_strike} {leg_type} @ ₹{leg_price}")
                company_name = leg_symbol
                
                if st.button("⬇️ Add Leg", use_container_width=True):
                    st.session_state.strategy_legs.append({
                        'strike': float(leg_strike), 'type': 'call' if leg_type.upper() == 'CE' else 'put',
                        'position': leg_side, 'entry_premium': leg_price, 'qty': int(leg_qty),
                        'symbol_root': leg_symbol, 'expiry_str': expiry_date_str
                    })
                    st.success("✅ Added!")
                    st.rerun()
        
        # MODE 2: MANUAL
        else:
            st.subheader("Search")
            search = st.text_input("Search", "", placeholder="RELIANCE, TCS")
            filtered = [k for k in scrip_dict.keys() if search.upper() in k.upper()] if search else sorted(list(scrip_dict.keys()))[:50]
            company_name = st.selectbox("Select:", options=[""] + filtered, index=0)
            
            if company_name and company_name in scrip_dict:
                spot = fetch_spot_price(scrip_dict[company_name], company_name)
                if spot: st.success(f"✅ Spot: ₹{spot['spot_price']:,.2f}")
            
            st.markdown("---")
            st.header("📊 Builder")
            strategy_name = st.text_input("Strategy", placeholder="Iron Condor")
            
            expiry_opts = {"30-Dec-25": "2025-12-30", "27-Jan-26": "2026-01-27", "24-Feb-26": "2026-02-24"}
            expiry_date_str = expiry_opts[st.selectbox("Expiry", list(expiry_opts.keys()))]
            
            st.markdown("---")
            st.subheader("➕ Add Leg")
            strike = st.number_input("Strike", min_value=1.0, value=225.0, step=0.5)
            opt_type = st.radio("Type", ['call', 'put'], format_func=str.upper)
            position = st.radio("Side", ['buy', 'sell'])
            premium = st.number_input("Premium", min_value=0.0, value=10.0, step=0.5)
            qty = st.number_input("Qty", min_value=1, value=75, step=1)
            
            if st.button("➕ Add", use_container_width=True):
                st.session_state.strategy_legs.append({
                    'strike': strike, 'type': opt_type, 'position': position,
                    'entry_premium': premium, 'qty': qty,
                    'symbol_root': company_name, 'expiry_str': expiry_date_str
                })
                st.success("✅ Added!")
                st.rerun()
        
        st.markdown("---")
        
        # DISPLAY LEGS
        if st.session_state.strategy_legs:
            st.subheader("📋 Legs")
            for i, leg in enumerate(st.session_state.strategy_legs):
                c1, c2 = st.columns([5, 1])
                with c1: st.caption(f"{leg['type'].upper()} {leg['strike']:,.0f} | {leg['position'].upper()} | ₹{leg['entry_premium']:,.2f} × {leg['qty']:,}")
                with c2:
                    if st.button("🗑️", key=f"del_{i}"):
                        st.session_state.strategy_legs.pop(i)
                        st.rerun()
            
            st.markdown("---")
            
            if st.button("🧮 Calculate", use_container_width=True, type="primary"):
                ref_leg = st.session_state.strategy_legs[0]
                ref_symbol = ref_leg.get('symbol_root', company_name or 'UNK')
                ref_expiry = ref_leg.get('expiry_str', expiry_date_str or '2026-01-26')
                
                # Convert expiry to string if it's a datetime/date object
                if isinstance(ref_expiry, (datetime, pd.Timestamp)):
                    ref_expiry = ref_expiry.strftime('%Y-%m-%d')
                elif hasattr(ref_expiry, 'strftime'):
                    ref_expiry = ref_expiry.strftime('%Y-%m-%d')
                
                if ref_symbol and ref_symbol in scrip_dict:
                    spot_data = fetch_spot_price(scrip_dict[ref_symbol], ref_symbol)
                    if spot_data:
                        total_pl, leg_details = calculate_strategy_pl_with_api(st.session_state.strategy_legs, ref_symbol, ref_expiry, options_df)
                        breakevens, total_premium, be_calcs = calculate_breakevens_mathematically(st.session_state.strategy_legs)
                        max_data = calculate_max_profit_loss(st.session_state.strategy_legs)
                        
                        # Calculate days to expiry
                        if isinstance(ref_expiry, str):
                            expiry_date_obj = datetime.strptime(ref_expiry, '%Y-%m-%d').date()
                        else:
                            expiry_date_obj = ref_expiry
                        days_to_expiry = (expiry_date_obj - datetime.now().date()).days
                        
                        st.session_state.current_strategy_data = {
                            'company': ref_symbol, 'spot_price': spot_data['spot_price'], 'expiry_date': ref_expiry,
                            'days_to_expiry': days_to_expiry,
                            'legs': st.session_state.strategy_legs.copy(), 'leg_details': leg_details, 'total_pl': total_pl,
                            'breakevens': breakevens, 'total_premium': total_premium, 'be_calculations': be_calcs,
                            'max_profit': max_data['max_profit'], 'max_loss': max_data['max_loss'],
                            'max_profit_spot': max_data['max_profit_spot'], 'max_loss_spot': max_data['max_loss_spot'],
                            'has_unlimited_risk': max_data['has_unlimited_risk'], 'strategy_name': ref_symbol + ' Strategy'
                        }
                        st.success("✅ Calculated!")
                        st.rerun()
            
            if st.button("🗑️ Clear All", use_container_width=True):
                st.session_state.strategy_legs = []
                st.rerun()

    # DISPLAY RESULTS
    if st.session_state.current_strategy_data:
        data = st.session_state.current_strategy_data
        
        st.markdown(f"## 📊 {data['strategy_name']}")
        
        with st.expander("💰 Details", expanded=True):
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("Spot", f"₹{data['spot_price']:,.2f}")
            c2.metric("Days", data['days_to_expiry'])
            c3.metric("Total P/L", f"₹{data['total_pl']:,.2f}")
            c4.metric("# Legs", len(data['legs']))
            c5.metric("Max Profit", f"₹{data['max_profit']:,.2f}")
            c6.metric("Max Loss", "Unlimited" if data['has_unlimited_risk'] else f"₹{data['max_loss']:,.2f}")
            
            st.markdown("---")
            
            st.subheader("📋 Leg Details")
            leg_df = pd.DataFrame(data['leg_details'])
            leg_df['Display'] = leg_df.apply(lambda x: f"{x['position'].upper()} {x['type'].upper()} {x['strike']:,.0f} @ ₹{x['entry_premium']:,.2f} → ₹{x['current_premium']:,.2f} | Qty: {x['qty']:,} | P/L: ₹{x['pl']:,.2f} | {x['api_status']}", axis=1)
            for i, row in leg_df.iterrows():
                st.caption(f"{i+1}. {row['Display']}")
            
            st.markdown("---")
            st.plotly_chart(create_payoff_diagram(data['legs'], data['spot_price'], data.get('breakevens')), use_container_width=True)
            
            if data.get('breakevens'):
                with st.expander("🔢 Breakeven Calculations"):
                    for calc in data['be_calculations']:
                        st.text(calc)
                        st.markdown("---")
                    for be_type, be_val in data['breakevens']:
                        st.text(verify_breakeven(data['legs'], be_val, be_type, data['total_premium']))
            
            st.markdown("---")
            c1, c2 = st.columns([3, 1])
            with c1:
                days = st.number_input("Historical Days", min_value=1, max_value=180, value=90, step=1)
            with c2:
                if st.button("➕ Add Graph", use_container_width=True):
                    st.session_state.strategy_graphs.append({
                        'company': data['company'], 'strategy_name': data['strategy_name'],
                        'legs': data['legs'], 'expiry_date': data['expiry_date'], 'days': days
                    })
                    st.rerun()

    if st.session_state.strategy_graphs:
        st.markdown("---")
        st.header("📊 Historical P/L")
        
        for i, graph in enumerate(st.session_state.strategy_graphs):
            c1, c2 = st.columns([10, 1])
            with c1:
                hist_df = calculate_historical_strategy_pl_with_api(graph['legs'], graph['company'], graph['expiry_date'], options_df, graph['days'])
                
                if hist_df is not None and not hist_df.empty:
                    bes = st.session_state.current_strategy_data.get('breakevens') if st.session_state.current_strategy_data else None
                    fig = create_strategy_pl_chart(hist_df, graph['company'], graph['strategy_name'], graph['days'], bes)
                    st.plotly_chart(fig, use_container_width=True)
                    
                    st.metric(f"Max Profit ({graph['days']}d)", f"₹{hist_df['pl'].max():,.2f}")
                    st.metric(f"Max Loss ({graph['days']}d)", f"₹{hist_df['pl'].min():,.2f}")
                else:
                    st.warning(f"⚠️ No data for {graph['company']}")
            
            with c2:
                if st.button("🗑️", key=f"del_g_{i}"):
                    st.session_state.strategy_graphs.pop(i)
                    st.rerun()
        
        if st.button("🗑️ Clear Graphs"):
            st.session_state.strategy_graphs = []
            st.rerun()

    if not st.session_state.current_strategy_data and not st.session_state.strategy_graphs:
        st.info("👈 Select stock, add legs, and calculate")