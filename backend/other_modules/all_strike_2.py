import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
import pandas as pd
import sys
import os

# ==================== PATH SETUP (CRITICAL FIX) ====================
# Get the absolute path of the current file (project/all strike/all_strike_2.py)
current_file_path = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file_path) # .../project/all strike

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
    from db_helper import get_all_stocks
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

# st.set_page_config(page_title="Strike Price Tracker", page_icon="📊", layout="wide")

# ==================== HELPER FUNCTIONS ====================

@st.cache_data
def load_scrip_master():
    """Load all data from CSV using absolute path"""
    try:
        # Using the absolute path defined above
        df = pd.read_csv(CSV_PATH)
        
        # Create lookups for smart matching
        df_stocks = df[df['Series'] == 'EQ'].copy()
        scrip_dict_by_name = dict(zip(df_stocks['Name'], df_stocks['ScripCode']))
        scrip_dict_by_symbol = {}
        
        # Extract symbols from Name column
        for name, code in scrip_dict_by_name.items():
            symbol = name.split()[0].strip().upper()
            scrip_dict_by_symbol[symbol] = code

        st.sidebar.success(f"✅ Loaded {len(df)} records from Master")
        return df, scrip_dict_by_name, scrip_dict_by_symbol
    except FileNotFoundError:
        st.error(f"❌ ScripMaster_all.csv not found at: {CSV_PATH}")
        return pd.DataFrame(), {}, {}

@st.cache_data(ttl=300)
def load_database_symbols():
    """Load ONLY symbols from database"""
    if not DB_AVAILABLE:
        return []
    
    try:
        stocks = get_all_stocks()
        symbols = [str(stock.get('symbol', '')).strip().upper() 
                  for stock in stocks if stock.get('symbol')]
        return sorted(list(set(symbols)))
    except Exception as e:
        st.error(f"❌ Database error: {str(e)}")
        return []

def get_scrip_code_from_csv(symbol_or_name, csv_by_name, csv_by_symbol, csv_df):
    """Smart lookup: Try to find scrip_code and Name from CSV using symbol/name"""
    # Try 1: Direct symbol match
    if symbol_or_name in csv_by_symbol:
        return csv_by_symbol[symbol_or_name], symbol_or_name
    
    # Try 2: Direct name match
    if symbol_or_name in csv_by_name:
        return csv_by_name[symbol_or_name], symbol_or_name
    
    # Try 3: Partial match in Name column
    matched = csv_df[csv_df['Name'].str.contains(symbol_or_name, case=False, na=False)]
    if not matched.empty:
        # Prefer EQ series if available
        eq_matched = matched[matched['Series'] == 'EQ']
        if not eq_matched.empty:
            return int(eq_matched.iloc[0]['ScripCode']), eq_matched.iloc[0]['Name']
        return int(matched.iloc[0]['ScripCode']), matched.iloc[0]['Name']
    
    return None, None

def get_5paisa_client():
    """Get authenticated 5Paisa client"""
    try:
        return get_client()
    except Exception as e:
        st.error(f"❌ Client error: {str(e)}")
        return None

def get_stock_scrip_code(df_master, stock_name):
    """Get scrip code for spot stock"""
    try:
        stock_data = df_master[
            (df_master['Name'] == stock_name) & 
            (df_master['Series'] == 'EQ')
        ]
        if not stock_data.empty:
            return int(stock_data.iloc[0]['ScripCode'])
            
        stock_data = df_master[df_master['Name'] == stock_name]
        if not stock_data.empty:
            return int(stock_data.iloc[0]['ScripCode'])
            
        return None
    except Exception as e:
        st.error(f"Error getting stock scrip: {e}")
        return None

def get_available_expiry_dates(df_master, stock_name):
    """Get available expiry dates for a stock's options"""
    try:
        # Use simple string contains to match Name
        options_data = df_master[
            (df_master['Name'] == stock_name) &
            ((df_master['ExchType'] == 'D') | (df_master['ExchType'] == 'N')) &
            (df_master['Expiry'].notna())
        ]
        
        # Fallback
        if options_data.empty:
            root_name = stock_name.split()[0]
            options_data = df_master[
                (df_master['Name'].str.contains(root_name, case=False, na=False)) &
                ((df_master['ExchType'] == 'D') | (df_master['ExchType'] == 'N')) &
                (df_master['Expiry'].notna())
            ]

        if options_data.empty:
            return []
        
        expiry_dates = options_data['Expiry'].unique()
        expiry_dates = pd.to_datetime(expiry_dates).sort_values()
        
        today = pd.Timestamp.now().normalize()
        future_expiries = [exp for exp in expiry_dates if pd.Timestamp(exp) >= today]
        
        return future_expiries
        
    except Exception as e:
        st.error(f"Error getting expiry dates: {e}")
        return []

def get_available_strikes(df_master, stock_name, expiry_date):
    """Get all strike prices for a specific stock and expiry date"""
    try:
        if isinstance(expiry_date, pd.Timestamp):
            expiry_str = expiry_date.strftime('%Y-%m-%d')
        else:
            expiry_str = str(expiry_date)
        
        # Filter strictly by expiry first
        expiry_data = df_master[df_master['Expiry'] == expiry_str]
        
        # Filter by name
        options_data = expiry_data[
            (expiry_data['Name'].str.contains(stock_name.split()[0], case=False, na=False)) &
            ((expiry_data['ExchType'] == 'D') | (expiry_data['ExchType'] == 'N'))
        ]
        
        if options_data.empty:
            return []
        
        strikes = []
        
        # Group by StrikeRate
        grouped = options_data.groupby('StrikeRate')
        
        for strike_rate, group in grouped:
            if strike_rate > 0:
                ce_row = group[group['Name'].str.contains('CE', case=False, na=False)]
                pe_row = group[group['Name'].str.contains('PE', case=False, na=False)]
                
                if not ce_row.empty or not pe_row.empty:
                    strikes.append({
                        'strike': strike_rate,
                        'ce_scrip': int(ce_row.iloc[0]['ScripCode']) if not ce_row.empty else None,
                        'pe_scrip': int(pe_row.iloc[0]['ScripCode']) if not pe_row.empty else None,
                        'ce_name': ce_row.iloc[0]['Name'] if not ce_row.empty else None,
                        'pe_name': pe_row.iloc[0]['Name'] if not pe_row.empty else None,
                        'expiry': expiry_str
                    })
        
        return sorted(strikes, key=lambda x: x['strike'])
        
    except Exception as e:
        st.error(f"❌ Error getting strikes: {str(e)}")
        return []

def fetch_historical_data_spot(scrip_code, days=30):
    """Fetch historical spot data"""
    try:
        if scrip_code is None:
            return None
            
        client = get_5paisa_client()
        if client is None:
            return None
        
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days+10)).strftime('%Y-%m-%d')
        
        df = client.historical_data('N', 'C', int(scrip_code), '1d', start_date, end_date)
        
        if df is None or df.empty:
            df = client.historical_data('N', 'N', int(scrip_code), '1d', start_date, end_date)
        
        if df is None or df.empty:
            df = client.historical_data('B', 'C', int(scrip_code), '1d', start_date, end_date)
        
        if df is None or df.empty:
            return None
        
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime')
        
        cutoff_date = datetime.now() - timedelta(days=days)
        df = df[df['Datetime'] >= cutoff_date]
        
        if len(df) < 1:
            return None
        
        return df
        
    except Exception as e:
        st.error(f"❌ Error fetching spot data: {str(e)}")
        return None

def fetch_historical_data_options(scrip_code, scrip_name, days=30):
    """Fetch historical option data"""
    try:
        if scrip_code is None:
            return None
            
        client = get_5paisa_client()
        if client is None:
            return None
        
        end_date = datetime.now().strftime('%Y-%m-%d')
        buffer_days = min(days + 30, 180)
        start_date = (datetime.now() - timedelta(days=buffer_days)).strftime('%Y-%m-%d')
        
        df = client.historical_data('N', 'D', int(scrip_code), '1d', start_date, end_date)
        
        if df is None or df.empty:
            return None
        
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime')
        
        cutoff_date = datetime.now() - timedelta(days=days)
        df = df[df['Datetime'] >= cutoff_date]
        
        if len(df) < 1:
            return None
        
        df['option_price'] = df['Close']
        
        return df
        
    except Exception as e:
        st.error(f"❌ Error fetching option data for {scrip_name}: {str(e)}")
        return None

def create_price_chart(df, title, color='#1f77b4', is_option=False):
    """Create line chart with CURRENT PRICE line"""
    if df is None or df.empty:
        return None, None
    
    fig = go.Figure()
    
    price_col = 'option_price' if is_option else 'Close'
    current_price = df.iloc[-1][price_col]
    min_price = df[price_col].min()
    max_price = df[price_col].max()
    avg_price = df[price_col].mean()
    
    hex_color = color.lstrip('#')
    rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    
    # Main price line with area fill
    fig.add_trace(go.Scatter(
        x=df['Datetime'],
        y=df[price_col],
        mode='lines',
        name='Price',
        line=dict(color=color, width=3),
        fill='tozeroy',
        fillcolor=f'rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, 0.2)',
        hovertemplate='<b>Date:</b> %{x|%b %d}<br><b>Price:</b> ₹%{y:.2f}<extra></extra>'
    ))
    
    # Yellow line shows CURRENT PRICE
    fig.add_hline(
        y=current_price, 
        line_dash="dot", 
        line_color="yellow", 
        annotation_text=f"Current: ₹{current_price:.2f}", 
        annotation_position="right"
    )
    
    fig.update_layout(
        title={
            'text': title, 
            'x': 0.5, 
            'xanchor': 'center', 
            'font': {'size': 16, 'color': 'white'}
        },
        xaxis_title='Date',
        yaxis_title='Price (₹)' if not is_option else 'Premium (₹)',
        template='plotly_dark',
        height=400,
        hovermode='x unified',
        paper_bgcolor='#1e1e1e',
        plot_bgcolor='#2d2d2d',
        font=dict(color='white'),
        showlegend=False,
        xaxis=dict(showgrid=True, gridcolor='#3a3a3a', showline=True, linecolor='#3a3a3a'),
        yaxis=dict(showgrid=True, gridcolor='#3a3a3a', showline=True, linecolor='#3a3a3a'),
        margin=dict(b=80)
    )
    
    return fig, {
        'current': current_price,
        'min': min_price,
        'max': max_price,
        'avg': avg_price
    }

# ==================== MAIN UI ====================
def app():
        
    st.markdown("<h3 style='text-align: center; margin-bottom: 10px;'>📊 Stock & Options Strike Price Tracker (30 Days)</h3>", unsafe_allow_html=True)

    df_master, csv_by_name, csv_by_symbol = load_scrip_master()
    db_symbols = load_database_symbols()

    if df_master.empty:
        st.stop()

    # Initialize session state
    if 'selected_strike' not in st.session_state:
        st.session_state.selected_strike = None
    if 'available_strikes' not in st.session_state:
        st.session_state.available_strikes = []
    if 'selected_stock' not in st.session_state:
        st.session_state.selected_stock = None
    if 'available_expiries' not in st.session_state:
        st.session_state.available_expiries = []
    if 'selected_expiry' not in st.session_state:
        st.session_state.selected_expiry = None

    # ==================== SIDEBAR ====================
    with st.sidebar:
        st.header("🔍 Step 1: Select Stock")
        
        # Toggle between Database and All Stocks
        search_mode = st.radio(
            "Select Mode",
            options=["Database Stocks", "All NSE Stocks"],
            label_visibility="collapsed"
        )
        
        selected_stock_name = None
        
        if search_mode == "Database Stocks":
            if DB_AVAILABLE and db_symbols:
                selected_symbol = st.selectbox(
                    "Choose from database", 
                    options=db_symbols,
                    index=0
                )
                # Smart Lookup to find correct CSV Name
                _, matched_name = get_scrip_code_from_csv(
                    selected_symbol, csv_by_name, csv_by_symbol, df_master
                )
                
                if matched_name:
                    selected_stock_name = matched_name
                    st.caption(f"Mapped to: {matched_name}")
                else:
                    st.warning(f"⚠️ Could not map '{selected_symbol}' to Master CSV")
            else:
                st.warning("⚠️ Database not available. Switch to 'All NSE Stocks'")
        
        else: # All NSE Stocks
            # Existing logic for favorites/search
            stock_names = df_master[df_master['Series'] == 'EQ']['Name'].unique()
            favorite_stocks = [
                "HUDCO", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
                "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK", "HINDUNILVR",
                "BAJFINANCE", "MARUTI", "ASIANPAINT", "WIPRO", "TITAN"
            ]
            available_favorites = [s for s in favorite_stocks if s in stock_names]
            
            user_input = st.selectbox(
                "Choose stock",
                options=[""] + available_favorites,
                index=0
            )
            if user_input:
                selected_stock_name = user_input

        # Action Button
        if st.button("📋 Get Strike Prices", use_container_width=True):
            if selected_stock_name:
                with st.spinner(f"🔄 Loading expiry dates for {selected_stock_name}..."):
                    expiries = get_available_expiry_dates(df_master, selected_stock_name)
                    
                    if expiries:
                        st.session_state.available_expiries = expiries
                        st.session_state.selected_stock = selected_stock_name
                        st.session_state.selected_expiry = None
                        st.session_state.available_strikes = []
                        st.session_state.selected_strike = None
                        st.success(f"✅ Found {len(expiries)} expiry dates")
                        st.rerun()
                    else:
                        st.error("❌ No expiry dates found for this stock")
            else:
                st.warning("⚠️ Please select a stock first")
        
        st.markdown("---")
        
        # Step 2: Select Expiry Date
        if st.session_state.available_expiries:
            st.header("📅 Step 2: Select Expiry")
            
            expiry_options = {
                exp.strftime('%d-%b-%Y'): exp.strftime('%Y-%m-%d') 
                for exp in st.session_state.available_expiries
            }
            
            selected_expiry_display = st.selectbox(
                "Choose expiry date",
                options=list(expiry_options.keys()),
                index=0
            )
            
            selected_expiry_value = expiry_options[selected_expiry_display]
            
            if st.button("🔍 Get Strikes for Expiry", use_container_width=True):
                with st.spinner(f"🔄 Loading strikes for {selected_expiry_display}..."):
                    strikes = get_available_strikes(
                        df_master, 
                        st.session_state.selected_stock, 
                        selected_expiry_value
                    )
                    
                    if strikes:
                        st.session_state.available_strikes = strikes
                        st.session_state.selected_expiry = selected_expiry_value
                        st.session_state.selected_strike = None
                        st.success(f"✅ Found {len(strikes)} strikes for {selected_expiry_display}")
                        st.rerun()
                    else:
                        st.error(f"❌ No strikes found for {selected_expiry_display}")
            
            st.markdown("---")
        
        # Step 3: Select Strike
        if st.session_state.available_strikes:
            st.header("📊 Step 3: Select Strike")
            
            strikes_list = [s['strike'] for s in st.session_state.available_strikes]
            
            selected_strike_value = st.selectbox(
                "Choose strike price",
                options=strikes_list,
                format_func=lambda x: f"₹{x}"
            )
            
            if st.button("📈 Generate Graphs", use_container_width=True, type="primary"):
                matching_strike = [s for s in st.session_state.available_strikes if s['strike'] == selected_strike_value]
                if matching_strike:
                    st.session_state.selected_strike = matching_strike[0]
                    st.rerun()

    # ==================== MAIN AREA ====================
    if st.session_state.get('selected_strike') and st.session_state.get('selected_stock'):
        stock_name = st.session_state.selected_stock
        strike_data = st.session_state.selected_strike
        expiry_date = strike_data.get('expiry', 'N/A')
        
        try:
            expiry_dt = pd.to_datetime(expiry_date)
            expiry_display = expiry_dt.strftime('%d-%b-%Y')
            days_to_expiry = (expiry_dt - pd.Timestamp.now()).days
        except:
            expiry_display = expiry_date
            days_to_expiry = "N/A"
        
        st.header(f"📊 Analysis: {stock_name} | Strike: ₹{strike_data['strike']} | Expiry: {expiry_display}")
        
        col_info1, col_info2, col_info3 = st.columns(3)
        with col_info1:
            st.info(f"📅 **Expiry:** {expiry_display}")
        with col_info2:
            st.info(f"⏳ **Days to Expiry:** {days_to_expiry}")
        with col_info3:
            st.info(f"🎯 **Strike:** ₹{strike_data['strike']}")
        
        st.markdown("---")
        
        with st.spinner("🔄 Fetching 30 days historical data from 5paisa..."):
            spot_scrip = get_stock_scrip_code(df_master, stock_name)
            
            spot_df = fetch_historical_data_spot(spot_scrip, 30) if spot_scrip else None
            ce_df = fetch_historical_data_options(strike_data['ce_scrip'], strike_data.get('ce_name', 'CE'), 30) if strike_data['ce_scrip'] else None
            pe_df = fetch_historical_data_options(strike_data['pe_scrip'], strike_data.get('pe_name', 'PE'), 30) if strike_data['pe_scrip'] else None
        
        # Display graphs
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.subheader("📈 Spot Price")
            if spot_df is not None and not spot_df.empty:
                fig_spot, stats_spot = create_price_chart(
                    spot_df, 
                    f"{stock_name} Spot (30 Days)", 
                    '#1f77b4',
                    is_option=False
                )
                st.plotly_chart(fig_spot, use_container_width=True)
                
                st.metric("Current Price", f"₹{stats_spot['current']:,.2f}")
                st.caption(f"Min: ₹{stats_spot['min']:,.2f} | Max: ₹{stats_spot['max']:,.2f}")
            else:
                st.error("❌ No spot data available")
                st.caption(f"Scrip Code: {spot_scrip}")
        
        with col2:
            st.subheader("📉 PUT Option")
            if pe_df is not None and not pe_df.empty:
                fig_pe, stats_pe = create_price_chart(
                    pe_df,
                    f"PUT ₹{strike_data['strike']} (30 Days)",
                    '#ef5350',
                    is_option=True
                )
                st.plotly_chart(fig_pe, use_container_width=True)
                
                st.metric("Current Premium", f"₹{stats_pe['current']:,.2f}")
                st.caption(f"Min: ₹{stats_pe['min']:,.2f} | Max: ₹{stats_pe['max']:,.2f}")
            else:
                st.error("❌ No PUT data available")
                st.caption(f"Scrip: {strike_data.get('pe_name', 'N/A')}")
                st.caption(f"Code: {strike_data['pe_scrip']}")
        
        with col3:
            st.subheader("📈 CALL Option")
            if ce_df is not None and not ce_df.empty:
                fig_ce, stats_ce = create_price_chart(
                    ce_df,
                    f"CALL ₹{strike_data['strike']} (30 Days)",
                    '#26a69a',
                    is_option=True
                )
                st.plotly_chart(fig_ce, use_container_width=True)
                
                st.metric("Current Premium", f"₹{stats_ce['current']:,.2f}")
                st.caption(f"Min: ₹{stats_ce['min']:,.2f} | Max: ₹{stats_ce['max']:,.2f}")
            else:
                st.error("❌ No CALL data available")
                st.caption(f"Scrip: {strike_data.get('ce_name', 'N/A')}")
                st.caption(f"Code: {strike_data['ce_scrip']}")
        
        # Summary Statistics
        st.markdown("---")
        st.subheader("📊 Summary Statistics")
        
        summary_data = []
        
        if spot_df is not None and not spot_df.empty:
            summary_data.append({
                'Type': '🔵 Spot Price',
                'Current': f"₹{stats_spot['current']:,.2f}",
                'Min (30D)': f"₹{stats_spot['min']:,.2f}",
                'Max (30D)': f"₹{stats_spot['max']:,.2f}",
                'Average': f"₹{stats_spot['avg']:,.2f}"
            })
        
        if pe_df is not None and not pe_df.empty:
            summary_data.append({
                'Type': f'🔴 PUT ₹{strike_data["strike"]}',
                'Current': f"₹{stats_pe['current']:,.2f}",
                'Min (30D)': f"₹{stats_pe['min']:,.2f}",
                'Max (30D)': f"₹{stats_pe['max']:,.2f}",
                'Average': f"₹{stats_pe['avg']:,.2f}"
            })
        
        if ce_df is not None and not ce_df.empty:
            summary_data.append({
                'Type': f'🟢 CALL ₹{strike_data["strike"]}',
                'Current': f"₹{stats_ce['current']:,.2f}",
                'Min (30D)': f"₹{stats_ce['min']:,.2f}",
                'Max (30D)': f"₹{stats_ce['max']:,.2f}",
                'Average': f"₹{stats_ce['avg']:,.2f}"
            })
        
        if summary_data:
            st.table(pd.DataFrame(summary_data))
        else:
            st.warning("⚠️ No data available to display summary")
        
        st.markdown("---")
        st.caption("📌 **Chart Explanation:**")
        st.caption("• **Line:** Actual price movement over 30 days")
        st.caption("• **Area Fill:** Visual emphasis of price range")
        st.caption("• **Yellow Dotted Line:** Current/Latest price")

    else:
        st.info("""
        👈 **Follow these steps:**
        
        1. **Select a stock** (From Database or All Stocks)
        2. Click **'Get Strike Prices'** to load expiry dates
        3. **Choose an expiry date** (30-Dec-2025, 27-Jan-2026, etc.)
        4. Click **'Get Strikes for Expiry'**
        5. **Select a strike price**
        6. Click **'Generate Graphs'**
        """)
        
        st.warning("⚠️ **Note:** If spot data is not showing, the stock might be illiquid or not available in 5paisa.")

    st.markdown("---")
    st.caption("⚡ **Powered by 5paisa API** | 30 Days Historical Data | Yellow Line = Current Price")