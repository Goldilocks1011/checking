import smtplib
import mysql.connector
import pandas as pd
import sys
import os
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==================== 1. PATH SETUP ====================
current_file_path = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file_path)
PROJECT_ROOT = os.path.dirname(current_dir)

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

try:
    from auth_manager import get_client
except ImportError:
    print("❌ Critical Error: 'auth_manager.py' not found.")
    sys.exit()

# ==================== 2. CONFIGURATION ====================
DB_CONFIG = {
    "database": "stocks",      
    "user": "root",            
    "password": "Root",   
    "host": "localhost",       
    "port": 3306
}

EMAIL_SENDER = "nidhiiyadav2k@gmail.com"        
EMAIL_PASSWORD = "trjw dgwk iikq aoqt"       
EMAIL_RECEIVER = "nidhipdf@gmail.com"       

# ==================== 3. HELPER FUNCTIONS ====================

def send_email_alert(subject, body):
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
        print(f"📧 Email Sent: {subject}")
    except Exception as e:
        print(f"❌ Email Failed: {str(e)}")

def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

def save_notification(symbol, trigger_type, price, message, link):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Query matches your new 'notifications' table schema
        query = """
            INSERT INTO notifications (stock_symbol, trigger_type, trigger_price, message, action_link, is_sent)
            VALUES (%s, %s, %s, %s, %s, 1)
        """
        cur.execute(query, (symbol, trigger_type, float(price), message, link))
        conn.commit()
        cur.close()
        conn.close()
        print(f"💾 Saved to DB: {symbol}")
    except Exception as e:
        print(f"❌ DB Save Failed: {e}")

# ==================== 4. MAIN LOGIC (UPDATED TABLES) ====================

def check_stocks():
    print("🚀 Starting Stock Check...")
    
    client = get_client()
    if not client:
        print("❌ Login Failed")
        return

    # Fetch Stock List from NEW DB SCHEMA (Stocks Master + Mapping)
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        query = """
            SELECT s.symbol, m.scrip_code 
            FROM stocks_master s
            JOIN broker_scrip_mapping m ON s.stock_id = m.stock_id
            WHERE s.is_active = 1
        """
        cur.execute(query)
        stock_list = cur.fetchall()
        conn.close()
    except Exception as e:
        print(f"❌ Could not fetch stocks from DB: {e}")
        return

    for stock in stock_list:
        symbol = stock[0]          # e.g., RELIANCE
        scripcode_str = stock[1]   # e.g., "1330"
        
        # Convert String Scripcode to Integer
        try:
            scripcode = int(scripcode_str)
        except (ValueError, TypeError):
            continue
        
        print(f"🔍 Checking {symbol}...", end="\r")

        try:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_52w = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            
            # Fetch Data
            df = client.historical_data('N', 'C', scripcode, '1d', start_52w, end_date)
            
            if df is None or df.empty:
                continue

            df['High'] = pd.to_numeric(df['High'])
            df['Low'] = pd.to_numeric(df['Low'])
            df['Close'] = pd.to_numeric(df['Close'])
            
            # Logic: Compare Today's Price vs Previous 364 days High/Low
            data_excluding_today = df.iloc[:-1]
            current_candle = df.iloc[-1]
            
            if data_excluding_today.empty:
                continue

            ltp = current_candle['Close']
            prev_52_high = data_excluding_today['High'].max()
            prev_52_low = data_excluding_today['Low'].min()
            
            trigger_occured = False
            msg = ""
            t_type = ""

            # Check High Breakout
            if ltp > prev_52_high and prev_52_high > 0:
                t_type = "52W_HIGH_CROSS"
                msg = f"🚀 ALERT: {symbol} crossed 52-Week High! \nPrice: ₹{ltp} \nPrevious High: ₹{prev_52_high}"
                trigger_occured = True
            
            # Check Low Breakdown
            elif ltp < prev_52_low and prev_52_low > 0:
                t_type = "52W_LOW_CROSS"
                msg = f"⚠️ ALERT: {symbol} fell below 52-Week Low! \nPrice: ₹{ltp} \nPrevious Low: ₹{prev_52_low}"
                trigger_occured = True

            # Save ONLY if Trigger Happened
            if trigger_occured:
                print(f"\n🚨 TRIGGER DETECTED: {symbol}")
                
                link = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}"
                
                save_notification(symbol, t_type, ltp, msg, link)
                send_email_alert(f"Stock Alert: {symbol}", msg + f"\n\nView Chart: {link}")

        except Exception as e:
            continue

    print("\n✅ Check Complete.")

# ==================== 5. SCHEDULED RUN ====================
if __name__ == "__main__":
    print("🕒 Stock Bot initialized.")
    print("✅ Target Schedule: 01:00 AM to 11:00 PM (23:00)")

    while True:
        now = datetime.now()
        current_hour = now.hour

        if 1 <= current_hour < 23:
            print(f"\n⚡ Active Time ({now.strftime('%H:%M')}). Checking stocks...")
            check_stocks()
            print("💤 Sleeping for 15 minutes...")
            time.sleep(900) 

        elif current_hour >= 23:
            print(f"\n🌙 Time to sleep. Stopping script.")
            break 

        else:
            print(f"⏳ Waiting for 1 AM start time...", end="\r")
            time.sleep(60)