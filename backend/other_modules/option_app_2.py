import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from scipy.stats import norm
from scipy.optimize import minimize_scalar
import sys
import os

# ==================== PATH SETUP (CRITICAL FIX) ====================
# Get the absolute path of the current file
current_file_path = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file_path)

# Get the parent directory (project root)
# Assuming file is in a subfolder like project/option/option_app2.py
PROJECT_ROOT = os.path.dirname(current_dir)

# Add project root to sys.path to allow imports from parent directory
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# Construct absolute path for CSV
CSV_PATH = os.path.join(PROJECT_ROOT, 'ScripMaster_all.csv')

# ==================== IMPORTS FROM PARENT ====================
try:
    from auth_manager import get_client
except ImportError:
    st.error("❌ Critical Error: 'auth_manager.py' not found in project root.")

# Database imports
try:
    # UPDATED THIS LINE to include get_available_option_symbols
    from db_helper import get_all_stocks, get_available_option_symbols, search_stocks as db_search_stocks
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

# st.set_page_config(
#     page_title="5Paisa Options Tracker",
#     page_icon="📊",
#     layout="wide"
# )

# ==================== HELPER FUNCTIONS ====================
@st.cache_data
def load_scrip_master():
    try:
        # Using the absolute path defined above
        df = pd.read_csv(CSV_PATH)
        df_options = df[df['Expiry'].notna()].copy()

        df_stocks = df[df['Series'] == 'EQ'].copy()
        stock_dict_by_name = dict(zip(df_stocks['Name'], df_stocks['ScripCode']))
        stock_dict_by_symbol = {}

        for name, code in stock_dict_by_name.items():
            symbol = name.split()[0].strip().upper()
            stock_dict_by_symbol[symbol] = code

        return df, df_options, stock_dict_by_name, stock_dict_by_symbol, df_stocks
    except FileNotFoundError:
        st.error(f"❌ ScripMaster_all.csv not found at: {CSV_PATH}")
        return pd.DataFrame(), pd.DataFrame(), {}, {}, pd.DataFrame()

@st.cache_data(ttl=300)
@st.cache_data(ttl=300)
def load_database_symbols():
    if not DB_AVAILABLE:
        return []
    try:
        # CHANGED: Now fetching only symbols present in options_master
        symbols = get_available_option_symbols()
        
        # Clean and sort the list
        clean_symbols = [str(s).strip().upper() for s in symbols if s]
        return sorted(list(set(clean_symbols)))
    except Exception as e:
        st.error(f"❌ Database error: {str(e)}")
        return []

def get_scrip_code_from_csv(symbol_or_name, csv_by_name, csv_by_symbol, csv_df):
    if symbol_or_name in csv_by_symbol:
        return csv_by_symbol[symbol_or_name], symbol_or_name
    if symbol_or_name in csv_by_name:
        return csv_by_name[symbol_or_name], symbol_or_name
    matched = csv_df[csv_df['Name'].str.contains(symbol_or_name, case=False, na=False)]
    if not matched.empty:
        return int(matched.iloc[0]['ScripCode']), matched.iloc[0]['Name']
    if 'ScripName' in csv_df.columns:
        matched = csv_df[csv_df['ScripName'].str.contains(symbol_or_name, case=False, na=False)]
        if not matched.empty:
            return int(matched.iloc[0]['ScripCode']), matched.iloc[0]['Name']
    return None, None

def get_5paisa_client():
    try:
        return get_client()
    except Exception as e:
        st.error(f"❌ Client error: {str(e)}")
        return None

def get_risk_free_rate():
    return 6.5

# ==================== GREEKS CALCULATION ====================
def calculate_historical_volatility(scrip_code, days=30):
    try:
        client = get_5paisa_client()
        if client is None:
            return 20.0

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=max(days * 2, 60))).strftime('%Y-%m-%d')
        df = client.historical_data('N', 'C', int(scrip_code), '1d', start_date, end_date)

        if df is None or df.empty or len(df) < 10:
            return 20.0

        df['returns'] = np.log(df['Close'] / df['Close'].shift(1))
        volatility = df['returns'].std() * np.sqrt(252) * 100
        return round(volatility, 2)
    except Exception as e:
        return 20.0

def calculate_black_scholes_greeks(spot_price, strike_price, days_to_expiry, volatility, risk_free_rate, option_type):
    try:
        if days_to_expiry <= 0:
            return {'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0, 'method': 'Expired'}

        T = days_to_expiry / 365.0
        r = risk_free_rate / 100
        sigma = volatility / 100

        d1 = (np.log(spot_price / strike_price) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        if option_type == 'CE':
            delta = norm.cdf(d1)
            theta = (-spot_price * norm.pdf(d1) * sigma / (2 * np.sqrt(T))
                    - r * strike_price * np.exp(-r * T) * norm.cdf(d2)) / 365
        else:
            delta = norm.cdf(d1) - 1
            theta = (-spot_price * norm.pdf(d1) * sigma / (2 * np.sqrt(T))
                    + r * strike_price * np.exp(-r * T) * norm.cdf(-d2)) / 365

        gamma = norm.pdf(d1) / (spot_price * sigma * np.sqrt(T))
        vega = spot_price * norm.pdf(d1) * np.sqrt(T) / 100

        return {
            'delta': round(delta, 4),
            'gamma': round(gamma, 6),
            'theta': round(theta, 4),
            'vega': round(vega, 4),
            'volatility': volatility,
            'method': 'Historical Vol'
        }
    except Exception as e:
        return {'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0, 'volatility': volatility, 'method': 'Error'}

def calculate_implied_volatility(ltp, spot_price, strike_price, days_to_expiry, risk_free_rate, option_type):
    try:
        if days_to_expiry <= 0 or ltp <= 0:
            return None

        T = days_to_expiry / 365.0
        r = risk_free_rate / 100

        def black_scholes_price(vol):
            if vol <= 0:
                return 1e10
            sigma = vol / 100
            d1 = (np.log(spot_price / strike_price) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
            d2 = d1 - sigma * np.sqrt(T)

            if option_type == 'CE':
                price = spot_price * norm.cdf(d1) - strike_price * np.exp(-r * T) * norm.cdf(d2)
            else:
                price = strike_price * np.exp(-r * T) * norm.cdf(-d2) - spot_price * norm.cdf(-d1)
            return abs(price - ltp)

        result = minimize_scalar(black_scholes_price, bounds=(1, 200), method='bounded')
        if result.success and result.fun < 0.01:
            return round(result.x, 2)
        return None
    except Exception as e:
        return None

def calculate_greeks_with_iv(ltp, spot_price, strike_price, days_to_expiry, risk_free_rate, option_type):
    try:
        iv = calculate_implied_volatility(ltp, spot_price, strike_price, days_to_expiry, risk_free_rate, option_type)
        if iv is None:
            return None

        T = days_to_expiry / 365.0
        r = risk_free_rate / 100
        sigma = iv / 100

        d1 = (np.log(spot_price / strike_price) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        if option_type == 'CE':
            delta = norm.cdf(d1)
            theta = (-spot_price * norm.pdf(d1) * sigma / (2 * np.sqrt(T))
                    - r * strike_price * np.exp(-r * T) * norm.cdf(d2)) / 365
        else:
            delta = norm.cdf(d1) - 1
            theta = (-spot_price * norm.pdf(d1) * sigma / (2 * np.sqrt(T))
                    + r * strike_price * np.exp(-r * T) * norm.cdf(-d2)) / 365

        gamma = norm.pdf(d1) / (spot_price * sigma * np.sqrt(T))
        vega = spot_price * norm.pdf(d1) * np.sqrt(T) / 100

        return {
            'delta': round(delta, 4),
            'gamma': round(gamma, 6),
            'theta': round(theta, 4),
            'vega': round(vega, 4),
            'volatility': iv,
            'method': 'Implied Vol'
        }
    except Exception as e:
        return None

# ==================== OPTION DATA FUNCTIONS ====================
def get_option_scrip_code(symbol, strike, option_type, expiry_date, scrip_master_df):
    try:
        mask = (
            (scrip_master_df['SymbolRoot'] == symbol.upper()) &
            (scrip_master_df['StrikeRate'] == strike) &
            (scrip_master_df['ScripType'] == option_type.upper()) &
            (scrip_master_df['Expiry'] == expiry_date)
        )
        result = scrip_master_df[mask]
        return result.iloc[0]['ScripCode'] if not result.empty else None
    except Exception as e:
        return None

def fetch_option_ltp(scrip_code):
    try:
        client = get_5paisa_client()
        if client is None:
            return None

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = datetime.now().strftime('%Y-%m-%d')
        df = client.historical_data('N', 'D', int(scrip_code), '1m', start_date, end_date)

        if df is None or df.empty:
            start_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
            df = client.historical_data('N', 'D', int(scrip_code), '1d', start_date, end_date)

        if df is None or df.empty:
            return None

        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime')
        today = datetime.now().date()
        today_data = df[df['Datetime'].dt.date == today]

        if not today_data.empty:
            return {
                'ltp': today_data.iloc[-1]['Close'],
                'open': today_data.iloc[0]['Open'],
                'high': today_data['High'].max(),
                'low': today_data['Low'].min(),
                'close': today_data.iloc[-1]['Close'],
                'volume': int(today_data['Volume'].sum()) if 'Volume' in today_data.columns else 0,
                'last_update': today_data.iloc[-1]['Datetime']
            }
        else:
            latest = df.iloc[-1]
            return {
                'ltp': latest['Close'],
                'open': latest['Open'],
                'high': latest['High'],
                'low': latest['Low'],
                'close': latest['Close'],
                'volume': int(latest.get('Volume', 0)) if pd.notna(latest.get('Volume', 0)) else 0,
                'last_update': latest['Datetime']
            }
    except Exception as e:
        return None

def fetch_spot_price(scrip_code, scrip_name):
    try:
        client = get_5paisa_client()
        if client is None:
            return None

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        df = client.historical_data('N', 'C', int(scrip_code), '1d', start_date, end_date)

        if df is None or df.empty:
            return None

        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime')
        latest = df.iloc[-1]

        return {
            'spot_price': latest['Close'],
            'date': latest['Datetime'].strftime('%Y-%m-%d')
        }
    except Exception as e:
        return None

def fetch_historical_option_prices(option_scrip_code, days=30):
    try:
        client = get_5paisa_client()
        if client is None:
            return None

        end_date = datetime.now().strftime('%Y-%m-%d')
        buffer_days = max(int(days * 2.5), 90)
        start_date = (datetime.now() - timedelta(days=buffer_days)).strftime('%Y-%m-%d')
        df = client.historical_data('N', 'D', int(option_scrip_code), '1d', start_date, end_date)

        if df is None or df.empty:
            return None

        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime')
        df = df.tail(days)

        if len(df) < 1:
            return None

        df['option_price'] = df['Close']
        return df
    except Exception as e:
        return None

def create_option_chart(df, symbol, strike, option_type, days):
    fig = go.Figure()

    color = '#4caf50' if option_type == 'CE' else '#f44336'

    fig.add_trace(go.Scatter(
        x=df['Datetime'],
        y=df['option_price'],
        mode='lines',
        name='Premium',
        line=dict(color=color, width=2),
        fill='tozeroy',
        fillcolor=f'rgba(76, 175, 80, 0.3)' if option_type == 'CE' else 'rgba(244, 67, 54, 0.3)'
    ))

    fig.update_layout(
        title={
            'text': f'{symbol} - {option_type} Option Premium (Strike: ₹{strike:.2f}) - Last {days} Days',
            'x': 0.5,
            'xanchor': 'center',
            'font': {'size': 16}
        },
        xaxis_title='Date',
        yaxis_title='Premium (₹)',
        template='plotly_dark',
        height=500,
        hovermode='x unified',
        paper_bgcolor='#1e1e1e',
        plot_bgcolor='#2d2d2d',
        font=dict(size=11),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.2,
            xanchor="center",
            x=0.5
        ),
        xaxis=dict(showgrid=True, gridcolor='#3a3a3a'),
        yaxis=dict(showgrid=True, gridcolor='#3a3a3a'),
        margin=dict(l=50, r=50, t=60, b=80)
    )

    return fig

# ==================== MAIN UI ====================
def app():
    st.title("📊 Options Tracker")

    # Initialize session state for graphs
    if 'graphs' not in st.session_state:
        st.session_state.graphs = []

    scrip_master, scrip_options, stock_by_name, stock_by_symbol, stock_df = load_scrip_master()
    db_symbols = load_database_symbols()

    if scrip_master.empty:
        st.error("❌ ScripMaster not loaded!")
        st.stop()

    with st.sidebar:
        st.header("Select Options")

        if db_symbols:
            selected_symbol = st.selectbox("Symbol", options=db_symbols)
        else:
            available_symbols = sorted(scrip_options['SymbolRoot'].unique())
            selected_symbol = st.selectbox("Symbol", options=available_symbols)

        spot_scrip_code, matched_name = get_scrip_code_from_csv(selected_symbol, stock_by_name, stock_by_symbol, stock_df)

        if spot_scrip_code is None:
            st.error(f"❌ {selected_symbol} not found")
            st.stop()

        expiry_dates = scrip_options[scrip_options['SymbolRoot'] == selected_symbol]['Expiry'].unique()
        expiry_dates = sorted([pd.to_datetime(d).strftime('%Y-%m-%d') for d in expiry_dates if pd.notna(d)])

        if not expiry_dates:
            st.error(f"❌ No expiry for {selected_symbol}")
            st.stop()

        selected_expiry = st.selectbox("Expiry Date", expiry_dates)

        strikes = scrip_options[
            (scrip_options['SymbolRoot'] == selected_symbol) &
            (scrip_options['Expiry'] == selected_expiry)
        ]['StrikeRate'].unique()
        strikes = sorted([float(s) for s in strikes if pd.notna(s)])

        if not strikes:
            st.error(f"❌ No strikes found")
            st.stop()

        selected_strike = st.number_input("Strike Price", min_value=min(strikes), max_value=max(strikes), value=strikes[len(strikes)//2], step=0.5)

        st.subheader("Option Type")
        option_type = st.radio("", ['CALL', 'PUT'], horizontal=True)
        option_type = 'CE' if option_type == 'CALL' else 'PE'

        st.markdown("---")

        # Greeks Calculation Section
        st.subheader("📐 Greeks Calculation")
        st.markdown("**Select Method**")

        greeks_method = st.radio(
            "",
            ['Historical Volatility (Fast)', 'Implied Volatility (Accurate)'],
            key='greeks_method'
        )

        if st.button("📊 Fetch Live Data", use_container_width=True):
            st.session_state.fetch_live = True

        if st.button("🔄 Refresh Data", use_container_width=True, type="primary"):
            st.cache_data.clear()
            st.rerun()

        st.markdown("---")

        # Add Graphs Section
        st.subheader("📊 Add Graphs")
        st.markdown("**Duration (days)**")

        duration_days = st.number_input("", min_value=1, max_value=365, value=28, step=1, label_visibility="collapsed")

        if st.button("➕ Add Graph", use_container_width=True):
            graph_info = {
                'id': len(st.session_state.graphs),  # Unique ID for each graph
                'symbol': selected_symbol,
                'strike': selected_strike,
                'option_type': option_type,
                'expiry': selected_expiry,
                'days': duration_days
            }
            st.session_state.graphs.append(graph_info)
            st.success(f"Graph added for {duration_days} days")
            st.rerun()

        # Delete All Graphs button
        if st.session_state.graphs:
            st.markdown("---")
            if st.button("🗑️ Delete All Graphs", use_container_width=True, type="secondary"):
                st.session_state.graphs = []
                st.success("All graphs deleted!")
                st.rerun()

    # Main content area
    risk_free_rate = get_risk_free_rate()

    spot_data = fetch_spot_price(spot_scrip_code, matched_name)
    if spot_data is None:
        st.error(f"❌ Unable to fetch spot price")
        st.stop()

    spot_price = spot_data['spot_price']
    expiry_dt = datetime.strptime(selected_expiry, '%Y-%m-%d')
    days_to_expiry = (expiry_dt - datetime.now()).days

    # Display metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Spot Price", f"₹{spot_price:.2f}")
    with col2:
        st.metric("Days to Expiry", f"{days_to_expiry}")
    with col3:
        hist_vol = calculate_historical_volatility(spot_scrip_code, days=30)
        st.metric("Historical Vol", f"{hist_vol:.1f}%")
    with col4:
        st.metric("Risk Free Rate", f"{risk_free_rate}%")

    option_scrip_code = get_option_scrip_code(selected_symbol, selected_strike, option_type, selected_expiry, scrip_options)
    if option_scrip_code is None:
        st.error(f"❌ Option contract not found")
        st.stop()

    option_data = fetch_option_ltp(option_scrip_code)

    if option_data:
        st.subheader("Option Details")
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric("LTP", f"₹{option_data['ltp']:.2f}")
        with c2:
            st.metric("Open", f"₹{option_data['open']:.2f}")
        with c3:
            st.metric("High", f"₹{option_data['high']:.2f}")
        with c4:
            st.metric("Low", f"₹{option_data['low']:.2f}")
        with c5:
            st.metric("Volume", f"{option_data['volume']}")

    # Greeks Display
    st.subheader("Greeks")

    if greeks_method == 'Historical Volatility (Fast)':
        greeks = calculate_black_scholes_greeks(spot_price, selected_strike, days_to_expiry, hist_vol, risk_free_rate, option_type)

        g1, g2, g3, g4 = st.columns(4)
        with g1:
            st.metric("Delta (Δ)", f"{greeks['delta']:.4f}")
        with g2:
            st.metric("Gamma (Γ)", f"{greeks['gamma']:.6f}")
        with g3:
            st.metric("Theta (Θ)", f"{greeks['theta']:.4f}")
        with g4:
            st.metric("Vega (ν)", f"{greeks['vega']:.4f}")

        st.caption(f"Method: {greeks['method']} | Volatility: {greeks['volatility']:.2f}%")

    else:  # Implied Volatility
        if option_data:
            greeks = calculate_greeks_with_iv(option_data['ltp'], spot_price, selected_strike, days_to_expiry, risk_free_rate, option_type)

            if greeks:
                g1, g2, g3, g4, g5 = st.columns(5)
                with g1:
                    st.metric("Delta (Δ)", f"{greeks['delta']:.4f}")
                with g2:
                    st.metric("Gamma (Γ)", f"{greeks['gamma']:.6f}")
                with g3:
                    st.metric("Theta (Θ)", f"{greeks['theta']:.4f}")
                with g4:
                    st.metric("Vega (ν)", f"{greeks['vega']:.4f}")
                with g5:
                    st.metric("IV", f"{greeks['volatility']:.2f}%")

                st.caption(f"Method: {greeks['method']}")
            else:
                st.warning("⚠️ Unable to calculate Implied Volatility")
        else:
            st.warning("⚠️ Option LTP required for IV calculation")

    # Display Graphs with individual delete buttons
    if st.session_state.graphs:
        st.markdown("---")
        st.subheader("📈 Option Premium Charts")

        for idx, graph in enumerate(st.session_state.graphs):
            # Create a container for each graph with delete button
            col_chart, col_delete = st.columns([0.95, 0.05])

            with col_chart:
                with st.spinner(f"Loading {graph['days']} days data..."):
                    hist_df = fetch_historical_option_prices(option_scrip_code, days=graph['days'])

                    if hist_df is not None and not hist_df.empty:
                        actual_days = len(hist_df)
                        chart = create_option_chart(hist_df, graph['symbol'], graph['strike'], graph['option_type'], actual_days)
                        # Use unique key for each chart
                        st.plotly_chart(chart, use_container_width=True, key=f"chart_{graph['id']}_{idx}")
                    else:
                        st.warning(f"⚠️ No data available for {graph['days']} days")

            with col_delete:
                # Delete button for individual graph
                if st.button("🗑️", key=f"delete_{graph['id']}_{idx}", help="Delete this graph"):
                    st.session_state.graphs.pop(idx)
                    st.rerun()

            st.markdown("---")