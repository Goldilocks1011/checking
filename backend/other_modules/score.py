import streamlit as st
import pandas as pd
import mysql.connector
from datetime import datetime, timedelta
import sys
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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

# ==================== EMAIL CONFIGURATION ====================
EMAIL_SENDER = "nidhiiyadav2k@gmail.com"
EMAIL_PASSWORD = "trjw dgwk iikq aoqt"
EMAIL_RECEIVER = "nidhipdf@gmail.com"   #  sarjugarg@gmail.com

# ==================== DATABASE CONNECTION ====================
def get_db_connection():
    """Create database connection - returns NEW connection each time"""
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="Root",
            database="stocks",
            port=3306,
            autocommit=False,
            connect_timeout=10
        )
        return conn
    except mysql.connector.Error as e:
        st.error(f"❌ Database connection failed: {e}")
        return None

# ==================== EMAIL ALERT FUNCTION ====================
def send_email_alert(subject, body):
    """Send email alert"""
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECEIVER
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        text = msg.as_string()
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, text)
        server.quit()
        return True
    except Exception as e:
        st.warning(f"⚠️ Email Failed: {str(e)}")
        return False

# ==================== SAVE NOTIFICATION FUNCTION ====================
def save_notification(symbol, trigger_type, price, message, link):
    """Save notification to database"""
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        if not conn:
            print(f"❌ DB connection failed for {symbol}")
            return False
        
        conn.ping(reconnect=True, attempts=3, delay=1)
            
        cur = conn.cursor()
        query = """
            INSERT INTO notifications (stock_symbol, trigger_type, trigger_price, message, action_link, is_sent)
            VALUES (%s, %s, %s, %s, %s, 1)
        """
        cur.execute(query, (symbol, trigger_type, float(price), message, link))
        conn.commit()
        print(f"✅ Saved to DB: {symbol} - {trigger_type}")
        return True
    except mysql.connector.Error as e:
        print(f"❌ MySQL Error for {symbol}: {e}")
        if conn and conn.is_connected():
            try:
                conn.rollback()
            except:
                pass
        return False
    except Exception as e:
        print(f"❌ DB Save Failed for {symbol}: {e}")
        if conn and conn.is_connected():
            try:
                conn.rollback()
            except:
                pass
        return False
    finally:
        try:
            if cur:
                cur.close()
            if conn and conn.is_connected():
                conn.close()
        except:
            pass

# ==================== CHECK IF TRIGGER ALREADY SENT ====================
def is_trigger_already_sent(symbol, trigger_type):
    """Check if same trigger was sent in last 24 hours to avoid spam"""
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        if not conn:
            return False
        
        conn.ping(reconnect=True, attempts=3, delay=1)
            
        cur = conn.cursor()
        query = """
            SELECT COUNT(*) FROM notifications 
            WHERE stock_symbol = %s 
            AND trigger_type = %s 
            AND created_at > NOW() - INTERVAL 24 HOUR
        """
        cur.execute(query, (symbol, trigger_type))
        count = cur.fetchone()[0]
        
        if count > 0:
            print(f"⏭️ Skipping {symbol} - Already sent in last 24h")
        
        return count > 0
    except Exception as e:
        print(f"⚠️ Error checking duplicate: {e}")
        return False
    finally:
        try:
            if cur:
                cur.close()
            if conn and conn.is_connected():
                conn.close()
        except:
            pass

# ==================== EXISTING FUNCTIONS ====================
def get_accounts():
    """Fetch all active accounts from database"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
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
        return accounts
    except Exception as e:
        st.error(f"❌ Error fetching accounts: {e}")
        return []

def get_equity_holdings(account_id):
    """Fetch equity holdings for selected account"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT 
                p.position_id,
                p.symbol,
                sm.company_name,
                p.net_quantity,
                p.avg_buy_price,
                p.current_price,
                p.total_pnl,
                p.pnl_percent,
                p.total_buy_value,
                a.broker
            FROM positions p
            JOIN accounts a ON p.account_id = a.account_id
            LEFT JOIN stocks_master sm ON p.stock_id = sm.stock_id
            WHERE p.account_id = %s 
                AND p.instrument_type = 'EQUITY'
                AND p.is_open = TRUE
                AND p.net_quantity > 0
            ORDER BY p.symbol
        """
        cursor.execute(query, (account_id,))
        holdings = cursor.fetchall()
        cursor.close()
        return holdings
    except Exception as e:
        st.error(f"❌ Error fetching holdings: {e}")
        return []

def get_all_equity_holdings():
    """Fetch equity holdings for ALL active accounts combined"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT 
                p.position_id,
                p.symbol,
                sm.company_name,
                p.net_quantity,
                p.avg_buy_price,
                p.current_price,
                p.total_pnl,
                p.pnl_percent,
                p.total_buy_value,
                a.broker,
                a.client_code,
                a.account_id
            FROM positions p
            JOIN accounts a ON p.account_id = a.account_id
            LEFT JOIN stocks_master sm ON p.stock_id = sm.stock_id
            WHERE a.is_active = TRUE
                AND p.instrument_type = 'EQUITY'
                AND p.is_open = TRUE
                AND p.net_quantity > 0
            ORDER BY p.symbol, a.account_id
        """
        cursor.execute(query)
        holdings = cursor.fetchall()
        cursor.close()
        return holdings
    except Exception as e:
        st.error(f"❌ Error fetching all holdings: {e}")
        return []

@st.cache_data
def load_scrip_master():
    """Load NSE stocks from CSV - SAME AS total_PL.py"""
    try:
        csv_path = os.path.join(PROJECT_ROOT, 'ScripMaster_all.csv')
        df = pd.read_csv(csv_path)
        df_stocks = df[df['Series'] == 'EQ'].copy()
        
        scrip_dict_by_name = dict(zip(df_stocks['Name'], df_stocks['ScripCode']))
        scrip_dict_by_symbol = {}
        
        for name, code in scrip_dict_by_name.items():
            symbol = name.split()[0].strip().upper()
            scrip_dict_by_symbol[symbol] = code
        
        # Return full dataframe for exchange type lookup
        return scrip_dict_by_name, scrip_dict_by_symbol, df_stocks
    except FileNotFoundError:
        st.error(f"❌ ScripMaster_all.csv not found at: {csv_path}")
        return {}, {}, pd.DataFrame()

def get_scrip_code_from_csv(symbol_or_name, csv_by_name, csv_by_symbol, csv_df):
    """
    Smart lookup: returns (scrip_code, matched_name, exch, exch_type)
    ENHANCED to return exchange info like total_PL.py
    """
    # Try 1: Direct symbol match
    if symbol_or_name in csv_by_symbol:
        scrip_code = csv_by_symbol[symbol_or_name]
        scrip_row = csv_df[csv_df['ScripCode'] == scrip_code].iloc[0]
        return (scrip_code, 
                symbol_or_name, 
                str(scrip_row.get('Exch', 'N')).strip().upper(),
                str(scrip_row.get('ExchType', 'C')).strip().upper())
    
    # Try 2: Direct name match
    if symbol_or_name in csv_by_name:
        scrip_code = csv_by_name[symbol_or_name]
        scrip_row = csv_df[csv_df['ScripCode'] == scrip_code].iloc[0]
        return (scrip_code, 
                symbol_or_name,
                str(scrip_row.get('Exch', 'N')).strip().upper(),
                str(scrip_row.get('ExchType', 'C')).strip().upper())
    
    # Try 3: Partial match in Name column
    matched = csv_df[csv_df['Name'].str.contains(symbol_or_name, case=False, na=False)]
    if not matched.empty:
        scrip_row = matched.iloc[0]
        return (int(scrip_row['ScripCode']), 
                scrip_row['Name'],
                str(scrip_row.get('Exch', 'N')).strip().upper(),
                str(scrip_row.get('ExchType', 'C')).strip().upper())
    
    # Try 4: ScripName column
    if 'ScripName' in csv_df.columns:
        matched = csv_df[csv_df['ScripName'].str.contains(symbol_or_name, case=False, na=False)]
        if not matched.empty:
            scrip_row = matched.iloc[0]
            return (int(scrip_row['ScripCode']), 
                    scrip_row['Name'],
                    str(scrip_row.get('Exch', 'N')).strip().upper(),
                    str(scrip_row.get('ExchType', 'C')).strip().upper())
    
    return None, None, None, None

@st.cache_data(ttl=600)
def fetch_52_week_data(scrip_code, symbol, exch, exch_type):
    """
    Fetch 52-week data - FIXED to handle BSE like total_PL.py
    Now accepts exchange and exchange type parameters
    """
    try:
        client = get_client()
        if client is None:
            return None
        
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_52w = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        
        # Use the provided exchange and type from CSV (like total_PL.py does)
        df_52w = client.historical_data(exch, exch_type, int(scrip_code), '1d', start_52w, end_date)
        
        if df_52w is None or df_52w.empty:
            return None
        
        df_52w['Datetime'] = pd.to_datetime(df_52w['Datetime'])
        df_52w = df_52w.sort_values('Datetime')
        
        week_52_high = df_52w['High'].max()
        week_52_low = df_52w['Low'].min()
        current_price = df_52w.iloc[-1]['Close']
        
        return {
            'week_52_high': round(week_52_high, 2),
            'week_52_low': round(week_52_low, 2),
            'current_price': round(current_price, 2)
        }
        
    except Exception as e:
        # Silent fail for cleaner UI
        return None

# ==================== 90% TRIGGER CHECK FUNCTION ====================
def check_90_percent_trigger(symbol, week_52_low, week_52_high, current_price):
    """
    Check if current price crosses 90% threshold from either end
    Returns: (triggered, trigger_type, message) or (False, None, None)
    """
    price_range = week_52_high - week_52_low
    
    if price_range <= 0:
        return False, None, None
    
    # Calculate 90% thresholds
    upper_90_threshold = week_52_low + (price_range * 0.90)
    lower_10_threshold = week_52_low + (price_range * 0.10)
    
    # Check upper threshold
    if current_price >= upper_90_threshold:
        trigger_type = "90%_NEAR_52W_HIGH"
        msg = f"🚀 ALERT: {symbol} is at 90% of 52-Week range (Near HIGH)!\n"
        msg += f"Current Price: ₹{current_price}\n"
        msg += f"52W High: ₹{week_52_high}\n"
        msg += f"52W Low: ₹{week_52_low}\n"
        msg += f"Distance to High: ₹{week_52_high - current_price:.2f}"
        return True, trigger_type, msg
    
    # Check lower threshold
    elif current_price <= lower_10_threshold:
        trigger_type = "90%_NEAR_52W_LOW"
        msg = f"⚠️ ALERT: {symbol} is at 10% of 52-Week range (Near LOW)!\n"
        msg += f"Current Price: ₹{current_price}\n"
        msg += f"52W Low: ₹{week_52_low}\n"
        msg += f"52W High: ₹{week_52_high}\n"
        msg += f"Distance to Low: ₹{current_price - week_52_low:.2f}"
        return True, trigger_type, msg
    
    return False, None, None

# ==================== MAIN APP ====================
def app():
    st.set_page_config(
        page_title="Portfolio Holdings Tracker",
        page_icon="💼",
        layout="wide"
    )
    
    st.title("💼 Portfolio Holdings Tracker")
    st.markdown("### Track your equity holdings with 52-week price ranges + 90% Triggers")
    
    # Fetch accounts
    accounts = get_accounts()
    
    if not accounts:
        st.error("❌ No accounts found in database")
        st.stop()
    
    # Account selection
    st.sidebar.header("🏦 Select Account")
    
    ALL_ACCOUNTS_LABEL = "🌐 All Accounts (Scheduled / Default)"
    
    account_options = {
        f"Account {acc['account_id']}: {acc['client_code']} ({acc['broker']})": acc['account_id']
        for acc in accounts
    }
    
    # "All Accounts" is always first (default for scheduled runs)
    all_options = [ALL_ACCOUNTS_LABEL] + list(account_options.keys())
    
    selected_account_label = st.sidebar.selectbox(
        "Choose Account",
        options=all_options,
        index=0  # Default = All Accounts
    )
    
    is_all_accounts = (selected_account_label == ALL_ACCOUNTS_LABEL)
    
    # Display account info in sidebar
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📊 Account Summary")
    
    if is_all_accounts:
        st.sidebar.info(f"📋 Processing **all {len(accounts)} active accounts**")
        st.sidebar.markdown("| Account | Broker |")
        st.sidebar.markdown("|---------|--------|")
        for acc in accounts:
            st.sidebar.markdown(f"| {acc['client_code']} | {acc['broker']} |")
    else:
        selected_account_id = account_options[selected_account_label]
        selected_account = next(acc for acc in accounts if acc['account_id'] == selected_account_id)
        st.sidebar.metric("Holder", selected_account['holder_name'])
        st.sidebar.metric("Client Code", selected_account['client_code'])
        st.sidebar.metric("Broker", selected_account['broker'])
        st.sidebar.metric("Capital", f"₹{selected_account['capital']:,.2f}")
        st.sidebar.metric("Available Balance", f"₹{selected_account['available_balance']:,.2f}")
        st.sidebar.metric("Margin Used", f"₹{selected_account['margin_used']:,.2f}")
    
    # Refresh button
    if st.sidebar.button("🔄 Refresh Holdings", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    # Fetch holdings
    st.markdown("---")
    
    # Load CSV data
    csv_by_name, csv_by_symbol, csv_df = load_scrip_master()
    
    if not csv_by_name:
        st.error("❌ ScripMaster CSV not available.")
        st.stop()
    
    if is_all_accounts:
        with st.spinner("📊 Fetching equity holdings for ALL accounts..."):
            holdings = get_all_equity_holdings()
        spinner_label = "All Accounts"
    else:
        with st.spinner(f"📊 Fetching equity holdings for {selected_account['client_code']}..."):
            holdings = get_equity_holdings(selected_account_id)
        spinner_label = selected_account['client_code']
    
    if not holdings:
        st.info(f"ℹ️ No equity holdings found")
        st.stop()
    
    st.success(f"✅ Found {len(holdings)} equity holdings")
    
    # Counter for triggers
    triggers_sent = 0
    
    # Display holdings with sliders
    st.markdown("### 📈 Your Equity Holdings")
    
    for idx, holding in enumerate(holdings):
        symbol = holding['symbol']
        company_name = holding['company_name'] or symbol
        net_qty = holding['net_quantity']
        avg_price = holding['avg_buy_price']
        current_price = holding['current_price']
        total_pnl = holding['total_pnl']
        pnl_percent = holding['pnl_percent']
        broker = holding['broker']
        
        # Show account badge in all-accounts mode
        account_badge = ""
        if is_all_accounts and 'client_code' in holding:
            account_badge = f" | 🏦 {holding['client_code']} ({holding['broker']})"
        
        # Create expander for each stock
        with st.expander(f"📊 {symbol} - {company_name}{account_badge}", expanded=True):
            col1, col2, col3 = st.columns([2, 3, 2])
            
            # Column 1: Stock details
            with col1:
                st.markdown("#### Stock Details")
                st.metric("Quantity", f"{net_qty:,}")
            
            # Column 2: 52-Week Slider + TRIGGER CHECK
            with col2:
                st.markdown("#### 52-Week Price Range")
                
                # ✅ ENHANCED lookup - now returns exchange info
                scrip_code, matched_name, exch, exch_type = get_scrip_code_from_csv(
                    symbol, 
                    csv_by_name, 
                    csv_by_symbol, 
                    csv_df
                )
                
                if scrip_code:
                    st.caption(f"✅ Matched: {matched_name[:50]}... → Code: {scrip_code} | Exch: {exch} | Type: {exch_type}")
                    
                    # ✅ FIXED: Pass exchange info to API call
                    week_data = fetch_52_week_data(scrip_code, symbol, exch, exch_type)
                    
                    if week_data:
                        week_52_low = week_data['week_52_low']
                        week_52_high = week_data['week_52_high']
                        live_price = week_data['current_price']
                        
                        # CHECK 90% TRIGGER
                        triggered, trigger_type, trigger_msg = check_90_percent_trigger(
                            symbol, week_52_low, week_52_high, live_price
                        )
                        
                        if triggered:
                            st.warning(f"🚨 TRIGGER DETECTED: {trigger_type}")
                            
                            # Check if already sent in last 24h
                            already_sent = is_trigger_already_sent(symbol, trigger_type)
                            
                            if not already_sent:
                                # Save to database
                                link = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}"
                                save_success = save_notification(symbol, trigger_type, live_price, trigger_msg, link)
                                
                                if save_success:
                                    st.success("💾 Trigger saved to database")
                                    triggers_sent += 1
                                    
                                    # Send email
                                    email_success = send_email_alert(f"Portfolio Alert: {symbol}", trigger_msg + f"\n\nView Chart: {link}")
                                    if email_success:
                                        st.success("📧 Email alert sent!")
                                    else:
                                        st.warning("⚠️ Email sending failed")
                                else:
                                    st.error("❌ Failed to save trigger to database")
                            else:
                                st.info("ℹ️ Trigger already sent in last 24 hours")
                        
                        # Display metrics
                        col_low, col_curr, col_high = st.columns(3)
                        with col_low:
                            st.metric("52W Low", f"₹{week_52_low:,.2f}")
                        with col_curr:
                            st.metric("Current", f"₹{live_price:,.2f}")
                        with col_high:
                            st.metric("52W High", f"₹{week_52_high:,.2f}")
                        
                        # Non-editable slider
                        st.slider(
                            "Price Position",
                            min_value=float(week_52_low),
                            max_value=float(week_52_high),
                            value=float(live_price),
                            disabled=True,
                            key=f"slider_{idx}_{symbol}",
                            label_visibility="collapsed"
                        )
                        
                        # Calculate position percentage
                        price_range = week_52_high - week_52_low
                        if price_range > 0:
                            position_pct = ((live_price - week_52_low) / price_range) * 100
                            
                            # Color code based on position
                            if position_pct >= 90:
                                color = "🔴"
                                zone = "DANGER ZONE (Near High)"
                            elif position_pct <= 10:
                                color = "🟡"
                                zone = "CAUTION ZONE (Near Low)"
                            else:
                                color = "🟢"
                                zone = "Safe Zone"
                            
                            st.caption(f"{color} Position: {position_pct:.1f}% from 52W low | {zone}")
                        
                    else:
                        st.warning(f"⚠️ API returned no data for {exch}:{exch_type}")
                        st.caption(f"Using DB current price: ₹{current_price:,.2f}")
                else:
                    st.warning(f"⚠️ '{symbol}' not found in ScripMaster CSV")
                    st.caption(f"Current price from DB: ₹{current_price:,.2f}")
        
        st.markdown("---")
    
    # Summary
    summary_label = "All Accounts" if is_all_accounts else selected_account['client_code']
    st.markdown(f"### 💰 Portfolio Summary — {summary_label}")
    total_investment = sum(h['total_buy_value'] for h in holdings)
    total_current_value = sum(h['net_quantity'] * h['current_price'] for h in holdings)
    total_pnl = sum(h['total_pnl'] for h in holdings)
    total_pnl_pct = (total_pnl / total_investment * 100) if total_investment > 0 else 0
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Investment", f"₹{total_investment:,.2f}")
    with col2:
        st.metric("Current Value", f"₹{total_current_value:,.2f}")
    with col3:
        st.metric("Total P&L", f"₹{total_pnl:,.2f}", delta=f"{total_pnl_pct:+.2f}%")
    with col4:
        st.metric("No. of Holdings", len(holdings))
    
    # Show trigger summary
    if triggers_sent > 0:
        st.success(f"🚨 {triggers_sent} new trigger(s) detected and alerts sent!")

if __name__ == "__main__":
    app()