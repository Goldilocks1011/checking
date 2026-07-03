import streamlit as st
import pandas as pd
import mysql.connector
from datetime import datetime, timedelta
import sys
import os
import plotly.graph_objects as go

# ==================== PATH SETUP ====================
current_file_path = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file_path)
PROJECT_ROOT = os.path.dirname(current_dir)

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# ==================== IMPORTS FROM PARENT ====================
try:
    from auth_manager import get_client
except ImportError:
    st.error("❌ Critical Error: 'auth_manager.py' not found in project root.")
    st.stop()

# ==================== CONSTANTS ====================
MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# ==================== DATABASE ====================
def get_db_connection():
    try:
        return mysql.connector.connect(
            host="localhost", user="root", password="Root",
            database="stocks", port=3306,
            autocommit=False, connect_timeout=10
        )
    except mysql.connector.Error as e:
        st.error(f"❌ Database connection failed: {e}")
        return None

def get_accounts():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT account_id, holder_name, client_code, broker,
                   capital, available_balance, margin_used
            FROM accounts WHERE is_active = TRUE ORDER BY account_id
        """)
        return cursor.fetchall()
    except Exception as e:
        st.error(f"❌ Error fetching accounts: {e}")
        return []
    finally:
        conn.close()

def get_equity_holdings(account_id):
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.symbol, sm.company_name, p.net_quantity,
                   p.avg_buy_price, p.current_price, a.broker
            FROM positions p
            JOIN accounts a ON p.account_id = a.account_id
            LEFT JOIN stocks_master sm ON p.stock_id = sm.stock_id
            WHERE p.account_id = %s AND p.instrument_type = 'EQUITY'
              AND p.is_open = TRUE AND p.net_quantity > 0
            ORDER BY p.symbol
        """, (account_id,))
        return cursor.fetchall()
    except Exception as e:
        st.error(f"❌ Error fetching holdings: {e}")
        return []
    finally:
        conn.close()

def get_all_equity_holdings():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.symbol, sm.company_name, p.net_quantity,
                   p.avg_buy_price, p.current_price, a.broker, a.client_code
            FROM positions p
            JOIN accounts a ON p.account_id = a.account_id
            LEFT JOIN stocks_master sm ON p.stock_id = sm.stock_id
            WHERE a.is_active = TRUE AND p.instrument_type = 'EQUITY'
              AND p.is_open = TRUE AND p.net_quantity > 0
            ORDER BY p.symbol
        """)
        return cursor.fetchall()
    except Exception as e:
        st.error(f"❌ Error fetching all holdings: {e}")
        return []
    finally:
        conn.close()

# ==================== SCRIP MASTER ====================
@st.cache_data
def load_scrip_master():
    try:
        csv_path = os.path.join(PROJECT_ROOT, 'ScripMaster_all.csv')
        df = pd.read_csv(csv_path)
        df_stocks = df[df['Series'] == 'EQ'].copy()
        by_name   = dict(zip(df_stocks['Name'], df_stocks['ScripCode']))
        by_symbol = {n.split()[0].strip().upper(): c for n, c in by_name.items()}
        return by_name, by_symbol, df_stocks
    except FileNotFoundError:
        st.error("❌ ScripMaster_all.csv not found")
        return {}, {}, pd.DataFrame()

def get_scrip_info(symbol, by_name, by_symbol, df_stocks):
    """Returns (scrip_code, matched_name, exch, exch_type) or (None,None,None,None)"""
    def _extract(row):
        return (int(row['ScripCode']), row['Name'],
                str(row.get('Exch', 'N')).strip().upper(),
                str(row.get('ExchType', 'C')).strip().upper())

    if symbol in by_symbol:
        code = by_symbol[symbol]
        row  = df_stocks[df_stocks['ScripCode'] == code].iloc[0]
        return _extract(row)
    if symbol in by_name:
        code = by_name[symbol]
        row  = df_stocks[df_stocks['ScripCode'] == code].iloc[0]
        return _extract(row)
    matched = df_stocks[df_stocks['Name'].str.contains(symbol, case=False, na=False)]
    if not matched.empty:
        return _extract(matched.iloc[0])
    if 'ScripName' in df_stocks.columns:
        matched = df_stocks[df_stocks['ScripName'].str.contains(symbol, case=False, na=False)]
        if not matched.empty:
            return _extract(matched.iloc[0])
    return None, None, None, None

# ==================== DATA FETCH ====================
@st.cache_data(ttl=3600)
def fetch_history(scrip_code, symbol, exch, exch_type, years=10):
    """Fetch N years of daily OHLC via 5paisa API."""
    try:
        client = get_client()
        if client is None:
            return None
        end   = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=years * 365)).strftime('%Y-%m-%d')
        df = client.historical_data(exch, exch_type, int(scrip_code), '1d', start, end)
        if df is None or df.empty:
            return None
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime').set_index('Datetime')
        df = df[['Open', 'High', 'Low', 'Close']].dropna()
        return df
    except Exception:
        return None

# ==================== ANALYSIS ====================
def analyze_52w_months(df):
    """
    Per calendar year → find month where absolute High and Low occurred.
    Returns: records_df, high_counts (series 1-12), low_counts (series 1-12)
    """
    records = []
    for year, grp in df.groupby(df.index.year):
        if len(grp) < 20:
            continue
        idx_high = grp['High'].idxmax()
        idx_low  = grp['Low'].idxmin()
        records.append({
            'Year':        year,
            'High_Month':  idx_high.month,
            'Low_Month':   idx_low.month,
            'Yearly_High': round(grp['High'].max(), 2),
            'Yearly_Low':  round(grp['Low'].min(), 2),
        })
    if not records:
        return None, None, None
    rec_df      = pd.DataFrame(records)
    high_counts = rec_df['High_Month'].value_counts().reindex(range(1, 13), fill_value=0)
    low_counts  = rec_df['Low_Month'].value_counts().reindex(range(1, 13), fill_value=0)
    return rec_df, high_counts, low_counts

def analyze_monthly_returns(df):
    """
    Resample to monthly → avg return + win rate per calendar month.
    """
    monthly = df['Close'].resample('ME').agg(['first', 'last'])
    monthly.columns = ['Open_M', 'Close_M']
    monthly['Return_Pct'] = ((monthly['Close_M'] - monthly['Open_M']) / monthly['Open_M']) * 100
    monthly['Month'] = monthly.index.month

    rows = []
    for m in range(1, 13):
        grp = monthly[monthly['Month'] == m]
        if grp.empty:
            continue
        green = int((grp['Return_Pct'] > 0).sum())
        total = len(grp)
        rows.append({
            'Month':    m,
            'Name':     MONTH_NAMES[m - 1],
            'Avg_Ret':  round(grp['Return_Pct'].mean(), 2),
            'Win_Rate': round((green / total) * 100, 1),
            'Green':    green,
            'Red':      int(total - green),
            'Total':    int(total),
        })
    return pd.DataFrame(rows)

# ==================== CHARTS ====================
def chart_monthly_returns(ms):
    colors = ['#26a641' if v > 0 else '#e05252' for v in ms['Avg_Ret']]
    # Label on top of each bar = "7/10 | +10.5%"
    bar_labels = [f"{row['Green']}/{row['Total']}  {row['Avg_Ret']:+.1f}%" for _, row in ms.iterrows()]
    fig = go.Figure(go.Bar(
        x=ms['Name'], y=ms['Avg_Ret'],
        marker_color=colors,
        text=bar_labels,
        textposition='outside',
        textfont=dict(size=12, color='white'),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Avg Return: %{y:.1f}%<br>"
            "Up years / Total: %{text}<extra></extra>"
        ),
    ))
    fig.update_layout(
        title="📊 Avg Monthly Return  —  label = up years / total years",
        xaxis_title="Month", yaxis_title="Avg Return (%)",
        height=360, margin=dict(t=55, b=10, l=10, r=10),
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
    )
    return fig

def chart_high_low_freq(high_counts, low_counts, total_years):
    months = MONTH_NAMES
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name='Yearly HIGH occurs here', x=months,
        y=[high_counts.get(i + 1, 0) for i in range(12)],
        marker_color='#e05252',
        text=[high_counts.get(i + 1, 0) for i in range(12)],
        textposition='outside',
    ))
    fig.add_trace(go.Bar(
        name='Yearly LOW occurs here', x=months,
        y=[low_counts.get(i + 1, 0) for i in range(12)],
        marker_color='#26a641',
        text=[low_counts.get(i + 1, 0) for i in range(12)],
        textposition='outside',
    ))
    fig.update_layout(
        barmode='group',
        title=f"📌 Month of Yearly High/Low (out of {total_years} yrs)",
        xaxis_title="Month", yaxis_title="No. of Years",
        height=320, margin=dict(t=45, b=10, l=10, r=10),
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
    )
    return fig

def chart_yearby_year(rec_df, total_years):
    """
    Scatter/dot chart: X = year, two dots per year —
    red dot at the month where yearly HIGH occurred,
    green dot at the month where yearly LOW occurred.
    """
    rec = rec_df.sort_values('Year')
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=rec['Year'],
        y=[MONTH_NAMES[m - 1] for m in rec['High_Month']],
        mode='markers+text',
        name='Yearly HIGH month',
        marker=dict(color='#e05252', size=14, symbol='circle'),
        text=[f"₹{v:,.0f}" for v in rec['Yearly_High']],
        textposition='top center',
        textfont=dict(size=9),
        hovertemplate="<b>%{x}</b><br>HIGH in %{y}<br>Price: %{text}<extra></extra>",
    ))

    fig.add_trace(go.Scatter(
        x=rec['Year'],
        y=[MONTH_NAMES[m - 1] for m in rec['Low_Month']],
        mode='markers+text',
        name='Yearly LOW month',
        marker=dict(color='#26a641', size=14, symbol='circle'),
        text=[f"₹{v:,.0f}" for v in rec['Yearly_Low']],
        textposition='bottom center',
        textfont=dict(size=9),
        hovertemplate="<b>%{x}</b><br>LOW in %{y}<br>Price: %{text}<extra></extra>",
    ))

    fig.update_layout(
        title=f"📅 Year-by-Year — which month had the Yearly High 🔴 and Low 🟢",
        xaxis_title="Year",
        yaxis=dict(
            title="Month",
            categoryorder='array',
            categoryarray=MONTH_NAMES[::-1],   # Jan at top, Dec at bottom
        ),
        height=420, margin=dict(t=55, b=10, l=10, r=10),
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
    )
    return fig

# ==================== INSIGHTS ROW ====================
def show_insights(ms, high_counts, low_counts, rec_df, symbol):
    total_years = rec_df['Year'].nunique()
    best        = ms.loc[ms['Avg_Ret'].idxmax()]
    worst       = ms.loc[ms['Avg_Ret'].idxmin()]
    top_high_m  = high_counts.idxmax()
    top_low_m   = low_counts.idxmax()

    # Consistent bull = months where stock went up more often than not (green >= 70%)
    bullish_ms  = ms[ms['Green'] / ms['Total'] >= 0.70]['Name'].tolist()
    bearish_ms  = ms[ms['Green'] / ms['Total'] <= 0.30]['Name'].tolist()

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown("#### 📈 Best Month")
        st.markdown(f"### {best['Name']}  `{best['Avg_Ret']:+.1f}%`")
        st.caption(
            f"Up {best['Green']}/{best['Total']} years  |  "
            f"Yearly HIGH here {high_counts.get(int(best['Month']), 0)}/{total_years} yrs"
        )

    with c2:
        st.markdown("#### 📉 Worst Month")
        st.markdown(f"### {worst['Name']}  `{worst['Avg_Ret']:+.1f}%`")
        st.caption(
            f"Down {worst['Red']}/{worst['Total']} years  |  "
            f"Yearly LOW here {low_counts.get(int(worst['Month']), 0)}/{total_years} yrs"
        )

    with c3:
        st.markdown("#### 🟢 Consistent Bull months")
        if bullish_ms:
            st.markdown(f"### {', '.join(bullish_ms)}")
            st.caption(f"Up in ≥ 7 out of every 10 years")
        else:
            st.markdown("### —")
            st.caption("No month is consistently up")

    with c4:
        st.markdown("#### 🔴 Consistent Bear months")
        if bearish_ms:
            st.markdown(f"### {', '.join(bearish_ms)}")
            st.caption(f"Down in ≥ 7 out of every 10 years")
        else:
            st.markdown("### —")
            st.caption("No month is consistently down")

# ==================== PER-STOCK BLOCK ====================
def render_stock_seasonal(holding, idx, by_name, by_symbol, df_stocks, years):
    symbol       = holding['symbol']
    company_name = holding.get('company_name') or symbol
    badge        = f"  |  🏦 {holding['client_code']}" if 'client_code' in holding else ""

    with st.expander(f"🌡️  {symbol}  —  {company_name}{badge}", expanded=False):

        scrip_code, matched_name, exch, exch_type = get_scrip_info(
            symbol, by_name, by_symbol, df_stocks
        )
        if not scrip_code:
            st.warning(f"⚠️ '{symbol}' not found in ScripMaster CSV — skipping.")
            return

        with st.spinner(f"Fetching {years}y data for {symbol}..."):
            df = fetch_history(scrip_code, symbol, exch, exch_type, years=years)

        if df is None or df.empty:
            st.error(f"❌ No historical data returned for {symbol}.")
            return

        actual_years = df.index.year.nunique()
        st.caption(
            f"✅ {len(df):,} trading days  |  "
            f"{df.index[0].strftime('%b %Y')} → {df.index[-1].strftime('%b %Y')}  |  "
            f"{actual_years} complete years"
        )

        rec_df, high_counts, low_counts = analyze_52w_months(df)
        if rec_df is None:
            st.error("❌ Not enough yearly data to analyse.")
            return

        ms = analyze_monthly_returns(df)

        # ---- Key insights ----
        show_insights(ms, high_counts, low_counts, rec_df, symbol)

        st.markdown("")

        # ---- Monthly return chart (full width) ----
        st.plotly_chart(chart_monthly_returns(ms),
                        use_container_width=True, key=f"ret_{idx}_{symbol}")

        # ---- Year-by-Year table ----
        st.markdown("##### 📋 Year-by-Year — Yearly High & Low month")
        yr = rec_df.copy()
        yr['High_Month'] = yr['High_Month'].map(lambda m: MONTH_NAMES[m - 1])
        yr['Low_Month']  = yr['Low_Month'].map(lambda m: MONTH_NAMES[m - 1])
        yr = yr.rename(columns={
            'Year':        'Year',
            'High_Month':  '🔴 High in Month',
            'Low_Month':   '🟢 Low in Month',
            'Yearly_High': 'Yearly High ₹',
            'Yearly_Low':  'Yearly Low ₹',
        }).sort_values('Year', ascending=False)
        st.dataframe(yr, use_container_width=True, hide_index=True)


# ==================== MAIN APP ====================
def app():
    st.set_page_config(
        page_title="Seasonal Analysis",
        page_icon="🌡️",
        layout="wide"
    )

    st.title("🌡️ Seasonal Analysis — All Portfolio Stocks")
    st.markdown(
        "Every stock in your portfolio is analysed for **monthly bullish/bearish patterns** "
        "and **which month the yearly 52W High/Low typically falls in** — "
        "based on years of daily price data from 5paisa."
    )

    # ---- Accounts ----
    accounts = get_accounts()
    if not accounts:
        st.error("❌ No accounts found in database")
        st.stop()

    # ---- Sidebar controls ----
    st.sidebar.header("⚙️ Settings")

    ALL_LABEL = "🌐 All Accounts"
    account_options = {
        f"{acc['client_code']} ({acc['broker']})": acc['account_id']
        for acc in accounts
    }
    selected_label = st.sidebar.selectbox(
        "Account", options=[ALL_LABEL] + list(account_options.keys()), index=0
    )
    is_all = (selected_label == ALL_LABEL)

    years = st.sidebar.slider(
        "Years of History", min_value=5, max_value=20, value=10, step=1,
        help="More years = more reliable patterns, slower first load"
    )

    if st.sidebar.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        "**How to read the charts:**\n\n"
        "🟢 **Win Rate ≥ 70%** → consistently bullish in that month\n\n"
        "🔴 **Win Rate ≤ 30%** → consistently bearish in that month\n\n"
        "📌 **High/Low Freq chart** → which month stock usually peaks or bottoms across years"
    )

    # ---- Load holdings ----
    with st.spinner("Loading portfolio holdings..."):
        if is_all:
            holdings = get_all_equity_holdings()
        else:
            acct_id  = account_options[selected_label]
            holdings = get_equity_holdings(acct_id)

    if not holdings:
        st.info("ℹ️ No equity holdings found.")
        st.stop()

    # Deduplicate by symbol (in case same stock appears across multiple accounts)
    seen, unique_holdings = set(), []
    for h in holdings:
        if h['symbol'] not in seen:
            seen.add(h['symbol'])
            unique_holdings.append(h)

    st.success(
        f"✅ **{len(unique_holdings)} unique stocks** found "
        f"({'all accounts' if is_all else selected_label})  —  "
        f"click any stock below to expand its seasonal analysis ↓"
    )

    # ---- Load ScripMaster ----
    by_name, by_symbol, df_stocks = load_scrip_master()
    if not by_name:
        st.stop()

    st.markdown("---")

    # ---- Render each stock (scrollable list, collapsed by default) ----
    for idx, holding in enumerate(unique_holdings):
        render_stock_seasonal(holding, idx, by_name, by_symbol, df_stocks, years)


if __name__ == "__main__":
    app()