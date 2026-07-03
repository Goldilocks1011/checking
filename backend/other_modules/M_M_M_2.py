import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
# from scipy import stats
import sys
import os

# other_modules/mmm.py
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from auth_manager import get_client

def compute_mmm(symbol: str, scrip_code: int, days: int = 90) -> dict:
    """
    Compute Mean, Median, Mode (range-based), Std Dev for a stock.
    Returns a dict with stats.
    """
    try:
        client = get_client()
        if not client:
            return {"error": "No client available"}

        end_date = datetime.now().strftime('%Y-%m-%d')
        buffer_days = max(int(days * 1.5), days + 30)
        start_date = (datetime.now() - timedelta(days=buffer_days)).strftime('%Y-%m-%d')

        df = client.historical_data('N', 'C', int(scrip_code), '1d', start_date, end_date)
        if df is None or df.empty:
            return {"error": "No data returned"}

        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime')
        df = df.tail(days)

        close_prices = df['Close'].values
        mean_price = np.mean(close_prices)
        median_price = np.median(close_prices)
        std_dev = np.std(close_prices, ddof=1)

        # Range-based Mode
        mode_range_width = 100
        price_min = np.floor(close_prices.min() / mode_range_width) * mode_range_width
        price_max = np.ceil(close_prices.max() / mode_range_width) * mode_range_width
        bins = np.arange(price_min, price_max + mode_range_width, mode_range_width)
        counts, edges = np.histogram(close_prices, bins=bins)
        best_idx = np.argmax(counts)
        mode_low = edges[best_idx]
        mode_high = edges[best_idx + 1]
        mode_count = int(counts[best_idx])

        return {
            'mean': round(mean_price, 2),
            'median': round(median_price, 2),
            'mode_low': round(mode_low, 2),
            'mode_high': round(mode_high, 2),
            'mode_count': mode_count,
            'std_dev': round(std_dev, 2),
        }
    except Exception as e:
        return {"error": str(e)}

# ==================== PATH SETUP ====================
current_file_path = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file_path)
PROJECT_ROOT = os.path.dirname(current_dir)

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

CSV_PATH = os.path.join(PROJECT_ROOT, 'ScripMaster_all.csv')

# ==================== IMPORTS FROM PARENT ====================
try:
    from auth_manager import get_client
except ImportError:
    pass

try:
    from db_helper import get_all_stocks, search_stocks as db_search_stocks
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

# ==================== HELPER FUNCTIONS ====================
# (Kept outside app() so they are cached globally)

@st.cache_data
def load_scrip_master():
    try:
        df = pd.read_csv(CSV_PATH)
        df_stocks = df[df['Series'] == 'EQ'].copy()
        scrip_dict_by_name = dict(zip(df_stocks['Name'], df_stocks['ScripCode']))
        scrip_dict_by_symbol = {}
        for name, code in scrip_dict_by_name.items():
            symbol = name.split()[0].strip().upper()
            scrip_dict_by_symbol[symbol] = code
        return scrip_dict_by_name, scrip_dict_by_symbol, df_stocks
    except FileNotFoundError:
        st.error(f"❌ ScripMaster_all.csv not found at: {CSV_PATH}")
        return {}, {}, pd.DataFrame()

@st.cache_data(ttl=300)
def load_database_symbols():
    if not DB_AVAILABLE:
        return []
    try:
        stocks = get_all_stocks()
        symbols = [str(stock.get('symbol', '')).strip().upper() for stock in stocks if stock.get('symbol')]
        return sorted(list(set(symbols)))
    except Exception as e:
        return []

def get_scrip_code_from_csv(symbol_or_name, csv_by_name, csv_by_symbol, csv_df):
    if symbol_or_name in csv_by_symbol:
        return csv_by_symbol[symbol_or_name], symbol_or_name
    if symbol_or_name in csv_by_name:
        return csv_by_name[symbol_or_name], symbol_or_name
    matched = csv_df[csv_df['Name'].str.contains(symbol_or_name, case=False, na=False)]
    if not matched.empty:
        return int(matched.iloc[0]['ScripCode']), matched.iloc[0]['Name']
    return None, None

def get_5paisa_client():
    try:
        from auth_manager import get_client
        return get_client()
    except Exception as e:
        st.error(f"❌ Client error: {str(e)}")
        return None

def fetch_stock_data_with_ranges(scrip_code, scrip_name, days):
    try:
        client = get_5paisa_client()
        if client is None:
            return None, None
        
        end_date = datetime.now().strftime('%Y-%m-%d')
        buffer_days = max(int(days * 1.5), days + 30)
        start_date = (datetime.now() - timedelta(days=buffer_days)).strftime('%Y-%m-%d')
        
        df_main = client.historical_data('N', 'C', int(scrip_code), '1d', start_date, end_date)
        
        if df_main is None or not isinstance(df_main, pd.DataFrame) or df_main.empty:
            return None, None
        
        df_main['Datetime'] = pd.to_datetime(df_main['Datetime'])
        df_main = df_main.sort_values('Datetime')
        df_main = df_main.tail(days)
        
        start_1m = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        start_3m = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        start_52w = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        
        df_1m = client.historical_data('N', 'C', int(scrip_code), '1d', start_1m, end_date)
        df_3m = client.historical_data('N', 'C', int(scrip_code), '1d', start_3m, end_date)
        df_52w = client.historical_data('N', 'C', int(scrip_code), '1d', start_52w, end_date)
        
        ranges = {
            'month_1_high': df_1m['High'].max() if df_1m is not None and not df_1m.empty else 0,
            'month_1_low': df_1m['Low'].min() if df_1m is not None and not df_1m.empty else 0,
            'month_3_high': df_3m['High'].max() if df_3m is not None and not df_3m.empty else 0,
            'month_3_low': df_3m['Low'].min() if df_3m is not None and not df_3m.empty else 0,
            'week_52_high': df_52w['High'].max() if df_52w is not None and not df_52w.empty else 0,
            'week_52_low': df_52w['Low'].min() if df_52w is not None and not df_52w.empty else 0,
            'current_price': df_main.iloc[-1]['Close'] if not df_main.empty else 0
        }
        return df_main, ranges
    except Exception as e:
        st.error(f"❌ Error fetching data: {str(e)}")
        return None, None

def calculate_statistics(df, mode_range_width=100):
    close_prices = df['Close'].values
    mean_price = np.mean(close_prices)
    median_price = np.median(close_prices)
    std_dev = np.std(close_prices, ddof=1)

    # --- Range-based Mode ---
    price_min = np.floor(close_prices.min() / mode_range_width) * mode_range_width
    price_max = np.ceil(close_prices.max() / mode_range_width) * mode_range_width
    bins = np.arange(price_min, price_max + mode_range_width, mode_range_width)
    counts, edges = np.histogram(close_prices, bins=bins)

    best_idx = np.argmax(counts)
    mode_low  = edges[best_idx]
    mode_high = edges[best_idx + 1]
    mode_count = int(counts[best_idx])

    # Actual close prices that fell inside the winning bucket
    mask = (close_prices >= mode_low) & (close_prices < mode_high)
    mode_prices = np.sort(close_prices[mask])
    # Dates of those prices (aligned by position in df)
    mode_dates  = df['Datetime'].values[mask]

    return {
        'mean':       mean_price,
        'median':     median_price,
        'mode_low':   mode_low,
        'mode_high':  mode_high,
        'mode_count': mode_count,
        'mode_prices': mode_prices,
        'mode_dates':  mode_dates,
        'std_dev':    std_dev
    }

def create_statistics_chart(df, scrip_name, stats, ranges, time_range):
    fig = go.Figure()
    
    # Close price line
    fig.add_trace(go.Scatter(
        x=df['Datetime'], y=df['Close'], mode='lines', name='Close Price',
        line=dict(color='#1976d2', width=2.5),
        hovertemplate='<b>%{x}</b><br>Price: ₹%{y:,.2f}<extra></extra>'
    ))
    
    # Mean line
    fig.add_trace(go.Scatter(
        x=df['Datetime'], y=[stats['mean']] * len(df), mode='lines',
        name=f'Mean: ₹{stats["mean"]:,.2f}',
        line=dict(color='#4CAF50', width=2, dash='dash')
    ))
    
    # Median line
    fig.add_trace(go.Scatter(
        x=df['Datetime'], y=[stats['median']] * len(df), mode='lines',
        name=f'Median: ₹{stats["median"]:,.2f}',
        line=dict(color='#2196F3', width=2, dash='dot')
    ))

    # Mode band — highlighted horizontal range
    fig.add_shape(
        type='rect',
        xref='paper', yref='y',
        x0=0, x1=1,
        y0=stats['mode_low'], y1=stats['mode_high'],
        fillcolor='rgba(255,152,0,0.15)',
        line=dict(color='rgba(255,152,0,0.6)', width=1, dash='dot'),
        layer='below'
    )

    # Mode band border lines (top & bottom) in legend
    fig.add_trace(go.Scatter(
        x=df['Datetime'],
        y=[stats['mode_high']] * len(df),
        mode='lines',
        name=f'Mode Zone: ₹{stats["mode_low"]:,.0f}–₹{stats["mode_high"]:,.0f} ({stats["mode_count"]} hits)',
        line=dict(color='#FF9800', width=1.5, dash='dashdot'),
        showlegend=True
    ))
    fig.add_trace(go.Scatter(
        x=df['Datetime'],
        y=[stats['mode_low']] * len(df),
        mode='lines',
        name='Mode Zone Lower',
        line=dict(color='#FF9800', width=1.5, dash='dashdot'),
        showlegend=False
    ))

    # Mode scatter points — actual prices inside the bucket
    if len(stats['mode_dates']) > 0:
        fig.add_trace(go.Scatter(
            x=stats['mode_dates'],
            y=stats['mode_prices'],
            mode='markers',
            name=f'Mode Hits ({stats["mode_count"]})',
            marker=dict(
                color='#FF9800',
                size=9,
                symbol='circle',
                line=dict(color='#fff', width=1.5)
            ),
            hovertemplate='<b>%{x}</b><br>Price: ₹%{y:,.2f}<br><i>Inside mode zone</i><extra></extra>'
        ))
    
    # Current Price
    fig.add_trace(go.Scatter(
        x=df['Datetime'], y=[ranges['current_price']] * len(df), mode='lines',
        name=f'Current: ₹{ranges["current_price"]:,.2f}',
        line=dict(color='#FFEB3B', width=2, dash='solid')
    ))
    
    # Ranges (1M, 3M, 52W)
    fig.add_trace(go.Scatter(x=df['Datetime'], y=[ranges['month_1_high']] * len(df), mode='lines', name='1M High', line=dict(color='#00E676', width=1.5, dash='dash')))
    fig.add_trace(go.Scatter(x=df['Datetime'], y=[ranges['month_1_low']] * len(df), mode='lines', name='1M Low', line=dict(color='#FF1744', width=1.5, dash='dash')))
    fig.add_trace(go.Scatter(x=df['Datetime'], y=[ranges['week_52_high']] * len(df), mode='lines', name='52W High', line=dict(color='#00BFA5', width=1.5, dash='dashdot')))
    fig.add_trace(go.Scatter(x=df['Datetime'], y=[ranges['week_52_low']] * len(df), mode='lines', name='52W Low', line=dict(color='#D500F9', width=1.5, dash='dashdot')))

    fig.update_layout(
        title=f'📊 {scrip_name} - Price Statistics & Ranges ({time_range})',
        xaxis_title='Date', yaxis_title='Price (₹)',
        hovermode='x unified', template='plotly_dark', height=700,
        legend=dict(orientation="v", yanchor="top", y=0.99, xanchor="right", x=1.15)
    )
    return fig



# ==================== MAIN APP ====================
# def compute_mmm(symbol: str, scrip_code: int, days: int = 90) -> dict:
#     st.markdown("<h2 style='text-align:center;'>📊 Stock Price Statistics Calculator</h2>", unsafe_allow_html=True)
#     st.markdown("<p style='text-align:center; color:#bbbbbb;'>Calculate <b>Mean, Median, Mode</b> for stock prices</p>", unsafe_allow_html=True)

#     # Initialize variables for scope safety
#     scrip_code = None
#     matched_name = None
#     calculate_clicked = False

#     # ==================== SIDEBAR ====================
#     with st.sidebar:
#         st.markdown("## 🔍 Step 1: Select Stock")
        
#         csv_by_name, csv_by_symbol, df_stocks = load_scrip_master()
#         db_symbols = load_database_symbols()
        
#         if not csv_by_name:
#             st.error("❌ ScripMaster CSV not available.")
#         else:
#             # UNIQUE KEYS ADDED TO PREVENT DUPLICATE ID ERROR
#             search_mode = st.radio(
#                 "Select Mode",
#                 options=["Database Stocks", "All NSE Stocks"],
#                 label_visibility="collapsed",
#                 key="mmm_mode_radio"
#             )
            
#             selected_symbol = None
            
#             if search_mode == "Database Stocks":
#                 if DB_AVAILABLE and db_symbols:
#                     selected_symbol = st.selectbox(
#                         "Choose from database", 
#                         options=db_symbols,
#                         index=0,
#                         key="mmm_db_select"
#                     )
#                     scrip_code, matched_name = get_scrip_code_from_csv(selected_symbol, csv_by_name, csv_by_symbol, df_stocks)
#                     if not scrip_code:
#                         st.warning(f"⚠️ '{selected_symbol}' not found in ScripMaster CSV")
#                 else:
#                     st.warning("⚠️ Database not available.")
            
#             if search_mode == "All NSE Stocks":
#                 search_term = st.text_input("Type to search", "", placeholder="e.g., RELIANCE", key="mmm_search_text")
#                 if search_term:
#                     filtered = [k for k in csv_by_name.keys() if search_term.upper() in k.upper()][:100]
#                 else:
#                     filtered = sorted(list(csv_by_name.keys()))[:50]
                
#                 selected_symbol = st.selectbox(
#                     "Select from results",
#                     options=filtered,
#                     index=0,
#                     label_visibility="collapsed",
#                     key="mmm_all_select"
#                 )
#                 if selected_symbol:
#                     scrip_code = csv_by_name.get(selected_symbol)
#                     matched_name = selected_symbol
            
#             st.markdown("---")
#             st.markdown("## 📅 Step 2: Select Range")
            
#             time_range = st.selectbox(
#                 "Choose time range",
#                 options=['30 Days', '90 Days', '180 Days', '365 Days'],
#                 label_visibility="collapsed",
#                 key="mmm_range_select"
#             )
            
#             st.markdown("---")
#             st.markdown("## 🎯 Step 3: Mode Range Width")
#             mode_range_width = st.number_input(
#                 "Price bucket size (₹)",
#                 min_value=10,
#                 max_value=1000,
#                 value=100,
#                 step=10,
#                 help="Prices within this ₹ range are grouped together to find the most frequent zone.",
#                 key="mmm_mode_range_width"
#             )

#             st.markdown("---")
#             calculate_clicked = st.button("📊 Calculate Statistics", type="primary", use_container_width=True, key="mmm_calc_btn")

#     # ==================== MAIN CONTENT ====================
#     if scrip_code and matched_name and calculate_clicked:
#         days_map = {'30 Days': 30, '90 Days': 90, '180 Days': 180, '365 Days': 365}
#         days = days_map[time_range]
        
#         with st.spinner(f'Fetching {time_range} data for {matched_name}...'):
#             df, ranges = fetch_stock_data_with_ranges(scrip_code, matched_name, days)
        
#         if df is not None and not df.empty and ranges is not None:
#             stats_data = calculate_statistics(df, mode_range_width)
            
#             # --- RESTORED STATS CARDS UI ---
#             st.markdown("## 📈 Statistical Summary")
#             col1, col2, col3, col4 = st.columns(4)
            
#             with col1:
#                 st.markdown(f"""
#                 <div style='background-color: #1e1e1e; padding: 12px; border-radius: 8px; text-align: center;'>
#                     <p style='color: #888; margin: 0; font-size: 12px;'>Mean (Average)</p>
#                     <h3 style='color: #4CAF50; margin: 5px 0;'>₹{stats_data['mean']:,.2f}</h3>
#                 </div>
#                 """, unsafe_allow_html=True)
            
#             with col2:
#                 st.markdown(f"""
#                 <div style='background-color: #1e1e1e; padding: 12px; border-radius: 8px; text-align: center;'>
#                     <p style='color: #888; margin: 0; font-size: 12px;'>Median (Middle)</p>
#                     <h3 style='color: #2196F3; margin: 5px 0;'>₹{stats_data['median']:,.2f}</h3>
#                 </div>
#                 """, unsafe_allow_html=True)
            
#             with col3:
#                 st.markdown(f"""
#                 <div style='background-color: #1e1e1e; padding: 12px; border-radius: 8px; text-align: center;'>
#                     <p style='color: #888; margin: 0; font-size: 12px;'>Mode Zone (±₹{mode_range_width} bucket)</p>
#                     <h3 style='color: #FF9800; margin: 5px 0;'>₹{stats_data['mode_low']:,.0f} – ₹{stats_data['mode_high']:,.0f}</h3>
#                     <p style='color: #4CAF50; margin: 0; font-size: 10px;'>{stats_data['mode_count']} strikes in this range</p>
#                 </div>
#                 """, unsafe_allow_html=True)
            
#             with col4:
#                 st.markdown(f"""
#                 <div style='background-color: #1e1e1e; padding: 12px; border-radius: 8px; text-align: center;'>
#                     <p style='color: #888; margin: 0; font-size: 12px;'>Std Deviation</p>
#                     <h3 style='color: #9C27B0; margin: 5px 0;'>₹{stats_data['std_dev']:,.2f}</h3>
#                 </div>
#                 """, unsafe_allow_html=True)
            
#             st.markdown("<br>", unsafe_allow_html=True)

#             # --- MODE ZONE DETAIL EXPANDER ---
#             with st.expander(f"🎯 Mode Zone Detail — ₹{stats_data['mode_low']:,.0f} to ₹{stats_data['mode_high']:,.0f}  ({stats_data['mode_count']} strikes)", expanded=False):
#                 st.markdown(
#                     f"<p style='color:#FF9800; font-size:14px;'>The <b>most frequent price zone</b> using a ₹{mode_range_width} bucket width. "
#                     f"<b>{stats_data['mode_count']}</b> closing prices occurred between "
#                     f"<b>₹{stats_data['mode_low']:,.0f}</b> and <b>₹{stats_data['mode_high']:,.0f}</b>.</p>",
#                     unsafe_allow_html=True
#                 )
#                 if len(stats_data['mode_prices']) > 0:
#                     strike_df = pd.DataFrame({
#                         'Date': pd.to_datetime(stats_data['mode_dates']).strftime('%Y-%m-%d'),
#                         'Close Price (₹)': [f"₹{p:,.2f}" for p in stats_data['mode_prices']]
#                     })
#                     st.dataframe(strike_df, use_container_width=True, hide_index=True)
#                 else:
#                     st.info("No prices found in this bucket.")
            
#             # --- RESTORED PRICE LEVELS EXPANDER ---
#             with st.expander("📊 Price Levels", expanded=True):
#                 st.markdown("""
#                     <style>
#                     [data-testid="stMetricValue"] { font-size: 20px !important; }
#                     [data-testid="stMetricLabel"] { font-size: 12px !important; }
#                     </style>
#                 """, unsafe_allow_html=True)

#                 col1, col2, col3 = st.columns(3)
#                 col1.metric("Current Price", f"₹{ranges['current_price']:,.2f}")
#                 col2.metric("1M High", f"₹{ranges['month_1_high']:,.2f}")
#                 col3.metric("1M Low", f"₹{ranges['month_1_low']:,.2f}")

#                 col4, col5, col6, col7 = st.columns(4)
#                 col4.metric("3M High", f"₹{ranges['month_3_high']:,.2f}")
#                 col5.metric("3M Low", f"₹{ranges['month_3_low']:,.2f}")
#                 col6.metric("52W High", f"₹{ranges['week_52_high']:,.2f}")
#                 col7.metric("52W Low", f"₹{ranges['week_52_low']:,.2f}")

#             st.markdown("---")
            
#             # Chart
#             st.markdown("## 📉 Price Chart with Statistics & Ranges")
#             fig = create_statistics_chart(df, matched_name, stats_data, ranges, time_range)
#             st.plotly_chart(fig, use_container_width=True)
            
#             # --- RESTORED RAW DATA EXPANDER ---
#             with st.expander("📋 View Raw Data"):
#                 display_df = df[['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume']].copy()
#                 display_df['Datetime'] = display_df['Datetime'].dt.strftime('%Y-%m-%d')
#                 st.dataframe(display_df, use_container_width=True, height=400)
        
#         else:
#             st.error(f"❌ Unable to fetch data for {matched_name}")

#     elif not calculate_clicked:
#         st.info("👈 Please select a stock and time range from the sidebar, then click 'Calculate Statistics'")
        
        
        
#     return {
#         'mean': mean,
#         'median': median,
#         'mode_low': mode_low,
#         'mode_high': mode_high,
#         'std_dev': std_dev,
#         # ... etc
#     }


