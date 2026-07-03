import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
import pandas as pd
import sys
import os

# ==================== PATH SETUP (CRITICAL FIX) ====================
# Get the absolute path of the current file (project/spot/spot_app2.py)
current_file_path = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file_path) # .../project/spot

# Get the parent directory (project root) where auth_manager and csv exist
PROJECT_ROOT = os.path.dirname(current_dir)      # .../project

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
    st.stop()

# Database imports
try:
    from db_helper import get_all_stocks, search_stocks as db_search_stocks
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

# st.set_page_config(
#     page_title="5Paisa Stock Tracker",
#     page_icon="📈",
#     layout="wide"
# )

@st.cache_data
def load_scrip_master():
    """Load NSE stocks from CSV - returns both dict and dataframe"""
    try:
        # Using the absolute path defined above
        df = pd.read_csv(CSV_PATH)
        df_stocks = df[df['Series'] == 'EQ'].copy()
        
        # Create multiple lookup dicts for flexible matching
        scrip_dict_by_name = dict(zip(df_stocks['Name'], df_stocks['ScripCode']))
        scrip_dict_by_symbol = {}
        
        # Extract symbols from Name column (e.g., "RELIANCE LIMITED" -> "RELIANCE")
        for name, code in scrip_dict_by_name.items():
            # Take first word as symbol (usually ticker)
            symbol = name.split()[0].strip().upper()
            scrip_dict_by_symbol[symbol] = code
        
        return scrip_dict_by_name, scrip_dict_by_symbol, df_stocks
    except FileNotFoundError:
        st.error(f"❌ ScripMaster_all.csv not found at: {CSV_PATH}")
        return {}, {}, pd.DataFrame()

@st.cache_data(ttl=300)
def load_database_symbols():
    """Load ONLY symbols from database (no scrip_code)"""
    if not DB_AVAILABLE:
        return []
    
    try:
        stocks = get_all_stocks()
        # Only return symbols, ignore scrip_code from DB
        symbols = [str(stock.get('symbol', '')).strip().upper() 
                  for stock in stocks if stock.get('symbol')]
        return sorted(list(set(symbols)))  # unique + sorted
    except Exception as e:
        st.error(f"❌ Database error: {str(e)}")
        return []

def get_scrip_code_from_csv(symbol_or_name, csv_by_name, csv_by_symbol, csv_df):
    """
    Smart lookup: Try to find scrip_code from CSV using symbol/name
    Returns: (scrip_code, matched_name) or (None, None)
    """
    # Try 1: Direct symbol match
    if symbol_or_name in csv_by_symbol:
        return csv_by_symbol[symbol_or_name], symbol_or_name
    
    # Try 2: Direct name match
    if symbol_or_name in csv_by_name:
        return csv_by_name[symbol_or_name], symbol_or_name
    
    # Try 3: Partial match in Name column (case-insensitive)
    matched = csv_df[csv_df['Name'].str.contains(symbol_or_name, case=False, na=False)]
    if not matched.empty:
        return int(matched.iloc[0]['ScripCode']), matched.iloc[0]['Name']
    
    # Try 4: Check ScripName column (some CSVs have this)
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

def fetch_latest_price(scrip_code, scrip_name):
    """Fetch real-time intraday price data"""
    try:
        client = get_5paisa_client()
        if client is None:
            st.error(f"❌ Client not initialized")
            return None

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')

        # Try intraday data
        df_intraday = client.historical_data('N', 'C', int(scrip_code), '1m', start_date, end_date)
        
        if df_intraday is not None and not df_intraday.empty:
            df_intraday['Datetime'] = pd.to_datetime(df_intraday['Datetime'])
            df_intraday = df_intraday.sort_values('Datetime')
            
            latest_ltp = df_intraday.iloc[-1]['Close']
            today_open = df_intraday.iloc[0]['Open']
            today_high = df_intraday['High'].max()
            today_low = df_intraday['Low'].min()
            today_volume = df_intraday['Volume'].sum()
            last_update = df_intraday.iloc[-1]['Datetime']
        else:
            # Fallback to daily
            start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            df_daily = client.historical_data('N', 'C', int(scrip_code), '1d', start_date, end_date)
            
            if df_daily is None or df_daily.empty:
                st.error(f"❌ No data from API for {scrip_name} (Code: {scrip_code})")
                return None
                
            df_daily['Datetime'] = pd.to_datetime(df_daily['Datetime'])
            df_daily = df_daily.sort_values('Datetime')
            latest = df_daily.iloc[-1]
            
            latest_ltp = latest['Close']
            today_open = latest['Open']
            today_high = latest['High']
            today_low = latest['Low']
            today_volume = latest.get('Volume', 0)
            last_update = latest['Datetime']

        # Get data for different periods
        start_1m = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        start_3m = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        start_52w = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        
        df_1m = client.historical_data('N', 'C', int(scrip_code), '1d', start_1m, end_date)
        df_3m = client.historical_data('N', 'C', int(scrip_code), '1d', start_3m, end_date)
        df_52w = client.historical_data('N', 'C', int(scrip_code), '1d', start_52w, end_date)

        # Previous close
        prev_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        prev_start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        df_prev = client.historical_data('N', 'C', int(scrip_code), '1d', prev_start, prev_date)
        
        if df_prev is not None and not df_prev.empty:
            df_prev = df_prev.sort_values('Datetime')
            prev_close = df_prev.iloc[-1]['Close']
        else:
            prev_close = today_open
        
        change = latest_ltp - prev_close
        change_pct = (change / prev_close) * 100 if prev_close != 0 else 0

        # Calculate highs and lows
        month_1_high = df_1m['High'].max() if df_1m is not None and not df_1m.empty else 0
        month_1_low = df_1m['Low'].min() if df_1m is not None and not df_1m.empty else 0
        month_3_high = df_3m['High'].max() if df_3m is not None and not df_3m.empty else 0
        month_3_low = df_3m['Low'].min() if df_3m is not None and not df_3m.empty else 0
        week_52_high = df_52w['High'].max() if df_52w is not None and not df_52w.empty else 0
        week_52_low = df_52w['Low'].min() if df_52w is not None and not df_52w.empty else 0

        return {
            'ltp': latest_ltp,
            'high': today_high,
            'low': today_low,
            'open': today_open,
            'prev_close': prev_close,
            'change': change,
            'change_pct': change_pct,
            'date': end_date,
            'volume': today_volume,
            'month_1_high': month_1_high,
            'month_1_low': month_1_low,
            'month_3_high': month_3_high,
            'month_3_low': month_3_low,
            'week_52_high': week_52_high,
            'week_52_low': week_52_low,
            'last_update': last_update
        }
        
    except Exception as e:
        st.error(f"❌ Error: {str(e)}")
        return None

def fetch_stock_data(scrip_code, scrip_name, days=30):
    """Fetch historical stock data for charts"""
    try:
        client = get_5paisa_client()
        if client is None:
            return None
        
        end_date = datetime.now().strftime('%Y-%m-%d')
        buffer_days = max(int(days * 2.5), 90)
        start_date = (datetime.now() - timedelta(days=buffer_days)).strftime('%Y-%m-%d')
        
        df = client.historical_data('N', 'C', int(scrip_code), '1d', start_date, end_date)
        
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return None
        
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime')
        df = df.tail(days)
        
        if len(df) < 1:
            st.warning(f"⚠️ {scrip_name} ke liye {days} din ka data nahi mila")
            return None
        
        return df
        
    except Exception as e:
        st.error(f"❌ Error: {str(e)}")
        return None

def create_dual_chart(df, scrip_name, days, 
                     month_1_high=None, month_1_low=None,
                     month_3_high=None, month_3_low=None, 
                     week_52_high=None, week_52_low=None):
    """Creates candlestick chart with proper legends"""
    fig = go.Figure()

    fig.add_trace(go.Candlestick(
        x=df['Datetime'],
        open=df['Open'],
        high=df['High'],
        low=df['Low'],
        close=df['Close'],
        name='OHLC',
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350',
        increasing_fillcolor='#26a69a',
        decreasing_fillcolor='#ef5350',
        opacity=0.8,
        showlegend=True
    ))

    fig.add_trace(go.Scatter(
        x=df['Datetime'],
        y=df['Close'],
        mode='lines',
        name='Close Price',
        line=dict(color='#1976d2', width=2),
        showlegend=True
    ))

    fig.add_trace(go.Bar(
        x=df['Datetime'],
        y=df['Volume'],
        name='Volume',
        marker_color='rgba(100, 100, 100, 0.3)',
        yaxis='y2',
        showlegend=True
    ))

    def add_line(val, name, color, dash):
        if val and val > 0:
            fig.add_trace(go.Scatter(
                x=[df['Datetime'].iloc[0], df['Datetime'].iloc[-1]],
                y=[val, val],
                mode='lines',
                name=name,
                line=dict(color=color, dash=dash, width=2),
                showlegend=True
            ))
    
    add_line(month_1_high, '1M High', '#ffa726', 'dash')
    add_line(month_1_low, '1M Low', '#ef5350', 'dash')
    add_line(month_3_high, '3M High', '#66bb6a', 'dashdot')
    add_line(month_3_low, '3M Low', '#ff7043', 'dashdot')
    add_line(week_52_high, '52W High', '#42a5f5', 'longdash')
    add_line(week_52_low, '52W Low', '#ec407a', 'longdash')

    fig.update_layout(
        title={
            'text': f"{scrip_name} - Last {days} Days",
            'x': 0.5,
            'xanchor': 'center',
            'font': {'size': 20, 'color': 'white'}
        },
        xaxis_title="Date",
        yaxis_title="Price ₹",
        yaxis2=dict(
            title="Volume",
            overlaying='y',
            side='right',
            showgrid=False
        ),
        template="plotly_dark",
        height=600,
        hovermode='x unified',
        paper_bgcolor='#1e1e1e',
        plot_bgcolor='#2d2d2d',
        font=dict(color='white'),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.3,
            xanchor="center",
            x=0.5,
            bgcolor='rgba(30, 30, 30, 0.9)',
            bordercolor='#3a3a3a',
            borderwidth=1,
            font=dict(size=10)
        ),
        xaxis=dict(
            rangeslider=dict(visible=False),
            showgrid=True,
            gridcolor='#3a3a3a'
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor='#3a3a3a'
        ),
        margin=dict(b=120)
    )
    
    return fig
def calculate_monthly_momentum(scrip_code):
    """
    Calculates:
    1. Max single-day % gain (and value) in last 30 days
    2. Date of that max gain
    3. Average % gain on 'Green' days
    """
    try:
        # Reuse your existing helper to get 30 days data
        # We fetch 40 days to ensure we have enough previous closes for the first few days of the month
        df = fetch_stock_data(scrip_code, "Unknown", days=40)
        
        if df is None or df.empty:
            return None

        # Calculate daily changes
        df['Prev_Close'] = df['Close'].shift(1)
        df['Change_Val'] = df['Close'] - df['Prev_Close']
        df['Change_Pct'] = (df['Change_Val'] / df['Prev_Close']) * 100
        
        # Filter for the last 30 days only
        cutoff_date = pd.to_datetime(datetime.now() - timedelta(days=30)).tz_localize(None)
        
        # Ensure Datetime is timezone-naive for comparison
        if df['Datetime'].dt.tz is not None:
             df['Datetime'] = df['Datetime'].dt.tz_localize(None)
             
        df_30 = df[df['Datetime'] >= cutoff_date].copy()
        
        if df_30.empty:
            return None

        # 1. Find Max Single Day Gain
        # We look for the row with the highest positive Percentage Change
        max_gain_row = df_30.loc[df_30['Change_Pct'].idxmax()]
        
        # 2. Calculate Average Up Move (Average of all positive days)
        positive_days = df_30[df_30['Change_Pct'] > 0]
        avg_up_move = positive_days['Change_Pct'].mean() if not positive_days.empty else 0.0

        return {
            'max_gain_pct': max_gain_row['Change_Pct'],
            'max_gain_val': max_gain_row['Change_Val'],
            'max_gain_date': max_gain_row['Datetime'],
            'max_close_price': max_gain_row['Close'],
            'avg_up_move': avg_up_move,
            'max_price_jump': max_gain_row['Change_Val']
        }
        
    except Exception as e:
        # st.error(f"Momentum calc error: {str(e)}") # Optional debugging
        return None
    
# ==================== MAIN UI ====================
def app():
    st.markdown(
        '<h3 style="text-align:center; margin-bottom:10px;">📈 5Paisa Stock Tracker - Live Data</h3>',
        unsafe_allow_html=True
    )

    # Load data
    csv_by_name, csv_by_symbol, csv_df = load_scrip_master()
    db_symbols = load_database_symbols()

    if not csv_by_name:
        st.error("❌ ScripMaster CSV not available.")
        st.stop()

    # Session state
    if 'graphs' not in st.session_state:
        st.session_state.graphs = []
    if 'current_stock_data' not in st.session_state:
        st.session_state.current_stock_data = None

    # ==================== SIDEBAR ====================
    with st.sidebar:
        st.header("🔍 Search Stock")
        
        search_mode = st.radio(
            "Select Mode",
            options=["Database Stocks", "All NSE Stocks"],
            label_visibility="collapsed"
        )
        
        st.markdown("---")
        
        selected_symbol = None
        scrip_code = None
        matched_name = None
        
        if search_mode == "Database Stocks":
            if DB_AVAILABLE and db_symbols:
                selected_symbol = st.selectbox(
                    "Choose from database", 
                    options=db_symbols,
                    index=0
                )
                
                # ✅ RUNTIME LOOKUP: CSV se scrip_code nikalo
                scrip_code, matched_name = get_scrip_code_from_csv(
                    selected_symbol, 
                    csv_by_name, 
                    csv_by_symbol, 
                    csv_df
                )
                
                if not scrip_code:
                    st.warning(f"⚠️ '{selected_symbol}' not found in ScripMaster CSV")
                else:
                    st.info(f"✅ Matched: {matched_name[:40]}... → Code: {scrip_code}")
                    
            else:
                st.warning("⚠️ Database not available. Switch to 'All NSE Stocks'")
                search_mode = "All NSE Stocks"
        
        if search_mode == "All NSE Stocks":
            search_term = st.text_input("Type to search", "", placeholder="e.g., RELIANCE, TCS")
            
            if search_term:
                # Search in CSV names
                filtered = [k for k in csv_by_name.keys() if search_term.upper() in k.upper()][:100]
            else:
                filtered = sorted(list(csv_by_name.keys()))[:50]
            
            selected_symbol = st.selectbox(
                "Select from results",
                options=filtered,
                index=0,
                label_visibility="collapsed"
            )
            scrip_code = csv_by_name.get(selected_symbol)
            matched_name = selected_symbol
        
        # Buttons
        fetch_btn = st.button("📊 Fetch Live Data", use_container_width=True)
        
        if st.session_state.current_stock_data:
            refresh_btn = st.button("🔄 Refresh Data", use_container_width=True, type="primary")
        else:
            refresh_btn = False
        
        st.markdown("---")
        st.header("📈 Add Graphs")
        duration = st.number_input("Duration (days)", min_value=1, max_value=365, value=30, step=1)
        add_graph_btn = st.button("➕ Add Graph", use_container_width=True)

    # ==================== FETCH DATA ====================
    if fetch_btn and selected_symbol and scrip_code:
        with st.spinner(f'🔄 Fetching live data for {selected_symbol}...'):
            stock_data = fetch_latest_price(scrip_code, matched_name or selected_symbol)
            if stock_data:
                st.session_state.current_stock_data = {
                    'company': selected_symbol,
                    'display_name': matched_name or selected_symbol,
                    'scripcode': scrip_code,
                    'data': stock_data
                }
                st.success(f"✅ Data fetched for {selected_symbol}")
                st.rerun()
            else:
                st.error("❌ Could not fetch data")

    # Refresh
    if refresh_btn and st.session_state.current_stock_data:
        data = st.session_state.current_stock_data
        with st.spinner(f'🔄 Refreshing data for {data["company"]}...'):
            stock_data = fetch_latest_price(data['scripcode'], data['display_name'])
            if stock_data:
                st.session_state.current_stock_data['data'] = stock_data
                st.success(f"✅ Data refreshed at {datetime.now().strftime('%H:%M:%S')}")
                st.rerun()
            else:
                st.error("❌ Could not refresh data")

    # Add graph
    if add_graph_btn and st.session_state.current_stock_data:
        data = st.session_state.current_stock_data
        st.session_state.graphs.append({
            'company': data['company'],
            'display_name': data['display_name'],
            'scripcode': data['scripcode'],
            'days': int(duration)
        })
        st.rerun()

    # ==================== DISPLAY STOCK DETAILS ====================
    if st.session_state.current_stock_data:
        data = st.session_state.current_stock_data['data']
        company = st.session_state.current_stock_data['company']
        display_name = st.session_state.current_stock_data['display_name']
        
        with st.expander(f"📊 {company} - Stock Details", expanded=True):
            st.markdown("""
                <style>
                div[data-testid="stMetricValue"] {font-size: 20px !important;}
                div[data-testid="stMetricLabel"] {font-size: 12px !important;}
                div[data-testid="stMetricDelta"] {font-size: 13px !important;}
                </style>
            """, unsafe_allow_html=True)
            
            st.caption(f"📌 {display_name}")
            
            # Row 1
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            with col1:
                st.metric("LTP", f"₹{data['ltp']:,.2f}", 
                        delta=f"₹{data['change']:,.2f} ({data['change_pct']:.2f}%)")
            with col2:
                st.metric("Open", f"₹{data['open']:,.2f}")
            with col3:
                st.metric("High", f"₹{data['high']:,.2f}")
            with col4:
                st.metric("Low", f"₹{data['low']:,.2f}")
            with col5:
                vol = data.get('volume', 0) or 0
                st.metric("Volume", f"{vol:,.0f}" if vol < 1000000 else f"{vol/1000000:.2f}M")
            with col6:
                st.metric("Prev Close", f"₹{data['prev_close']:,.2f}")
            
            # Row 2
            col7, col8, col9, col10, col11, col12 = st.columns(6)
            with col7:
                st.metric("1M High", f"₹{data['month_1_high']:,.2f}")
            with col8:
                st.metric("1M Low", f"₹{data['month_1_low']:,.2f}")
            with col9:
                st.metric("3M High", f"₹{data['month_3_high']:,.2f}")
            with col10:
                st.metric("3M Low", f"₹{data['month_3_low']:,.2f}")
            with col11:
                st.metric("52W High", f"₹{data['week_52_high']:,.2f}")
            with col12:
                st.metric("52W Low", f"₹{data['week_52_low']:,.2f}")
            # === INSERT THIS NEW BLOCK HERE ===
            st.markdown("---")
            st.markdown("**🚀 30-Day Momentum Analysis**")
            
            # We need to get 'scripcode' from the main session state, not the 'data' dictionary
            mom_stats = calculate_monthly_momentum(st.session_state.current_stock_data['scripcode']) # ✅ Correct
            
            if mom_stats:
                m1, m2, m3, m4 = st.columns(4)  # ✅ CHANGED: 3 → 4 columns
                
                # Format the date nicely (e.g., "28 Jan")
                date_str = mom_stats['max_gain_date'].strftime('%d %b')
                
                # ✅ NEW: Calculate projection
                current_ltp = data['ltp']
                max_price_jump = mom_stats['max_price_jump']
                projected_price = current_ltp + max_price_jump
                
                with m1:
                    st.metric(
                        "Max 1-Day Spike", 
                        f"{mom_stats['max_gain_pct']:.2f}%",
                        delta=f"₹{mom_stats['max_gain_val']:.2f} Gain"
                    )
                    st.caption(f"Happened on: {date_str}")
                    
                with m2:
                    st.metric(
                        "Avg. Up Move", 
                        f"{mom_stats['avg_up_move']:.2f}%",
                        help="Average percentage increase on green days only"
                    )
                with m3:
                    st.metric(
                        "Price on Spike Day",
                        f"₹{mom_stats['max_close_price']:.2f}"
                    )
                
                # ✅ NEW COLUMN
                with m4:
                    st.metric(
                        "If Today Spikes Same",
                        f"₹{projected_price:.2f}",
                        delta=f"+₹{max_price_jump:.2f}",
                        help=f"Projection: Current LTP (₹{current_ltp:.2f}) + Max spike amount (₹{max_price_jump:.2f})"
                    )
            else:
                st.info("Not enough historical data for momentum analysis")
            # ==================================
            lu = data.get('last_update')
            st.caption(f"📅 {data['date']} | Last Update: {lu.strftime('%H:%M:%S') if hasattr(lu, 'strftime') else lu}")

    # ==================== DISPLAY GRAPHS ====================
    if st.session_state.graphs:
        st.header("📊 Your Graphs")
        for idx, graph in enumerate(st.session_state.graphs):
            with st.container():
                col1, col2 = st.columns([10, 1])
                
                with col1:
                    hist_df = fetch_stock_data(graph['scripcode'], graph['display_name'], graph['days'])
                    if hist_df is not None and not hist_df.empty:
                        stockinfo = st.session_state.current_stock_data['data'] if st.session_state.current_stock_data else {}
                        fig = create_dual_chart(
                            hist_df, 
                            graph['company'], 
                            graph['days'],
                            stockinfo.get('month_1_high'),
                            stockinfo.get('month_1_low'),
                            stockinfo.get('month_3_high'),
                            stockinfo.get('month_3_low'),
                            stockinfo.get('week_52_high'),
                            stockinfo.get('week_52_low'),
                        )
                        st.plotly_chart(fig, use_container_width=True, key=f"chart_{idx}")
                    else:
                        st.warning(f"⚠️ No data for {graph['company']}")
                
                with col2:
                    if st.button("❌", key=f"del_{idx}"):
                        st.session_state.graphs.pop(idx)
                        st.rerun()
        
        if st.button("🗑️ Clear All Graphs"):
            st.session_state.graphs = []
            st.rerun()

    if not st.session_state.current_stock_data:
        st.info("👈 Select a stock and click Fetch Live Data to begin")

    st.markdown("---")
    st.caption(f"⚡ Powered by 5paisa API | {'Database Symbols + CSV Codes' if DB_AVAILABLE else 'CSV only'}")