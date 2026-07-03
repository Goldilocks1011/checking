"""
backend_alerts.py
-----------------
Headless backend script for 52-week high/low stock alerts.
Run directly with Python — no browser/Streamlit needed.

Usage:
    python backend_alerts.py

Crontab example (every hour):
    0 * * * * /path/to/python /path/to/backend_alerts.py >> /path/to/cron_log.txt 2>&1
"""

import pandas as pd
import mysql.connector
from datetime import datetime, timedelta
import sys
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

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
    log.critical("❌ 'auth_manager.py' not found in project root: %s", PROJECT_ROOT)
    sys.exit(1)

# ==================== EMAIL CONFIGURATION ====================
EMAIL_SENDER   = "nidhiiyadav2k@gmail.com"
EMAIL_PASSWORD = "trjw dgwk iikq aoqt"
EMAIL_RECEIVER = "nidhipdf@gmail.com"   # add more: "sarjugarg@gmail.com"

# ==================== DATABASE CONNECTION ====================
def get_db_connection():
    """Create a fresh database connection each time."""
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
        log.error("❌ Database connection failed: %s", e)
        return None

# ==================== EMAIL ====================
def send_email_alert(subject, body_html):
    """Send an HTML email alert. Returns True on success."""
    try:
        msg = MIMEMultipart('alternative')
        msg['From']    = EMAIL_SENDER
        msg['To']      = EMAIL_RECEIVER
        msg['Subject'] = subject
        msg.attach(MIMEText(body_html, 'html'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.quit()
        log.info("📧 Email sent: %s", subject)
        return True
    except Exception as e:
        log.error("⚠️ Email failed: %s", e)
        return False


def build_alert_email_html(alerts, run_time):
    """Build a styled HTML email with one table row per triggered stock."""
    high_icon = "🚀"
    low_icon  = "⚠️"

    rows = ""
    for alert in alerts:
        is_high   = "HIGH" in alert['trigger_type']
        icon      = high_icon if is_high else low_icon
        reason    = f"{icon} Near 52W {'HIGH' if is_high else 'LOW'}"
        dist_label = "Distance to High" if is_high else "Distance to Low"
        dist_val   = f"₹{alert['distance']:.2f}"
        row_color  = "#fff8f0" if is_high else "#f0f8ff"

        rows += f"""
        <tr style="background:{row_color};">
            <td style="padding:10px 12px; font-weight:600;">{alert['client_code']}</td>
            <td style="padding:10px 12px; font-weight:700;">{alert['symbol']}</td>
            <td style="padding:10px 12px;">{reason}</td>
            <td style="padding:10px 12px; text-align:right;">₹{alert['current_price']}</td>
            <td style="padding:10px 12px; text-align:right;">₹{alert['week_52_low']}</td>
            <td style="padding:10px 12px; text-align:right;">₹{alert['week_52_high']}</td>
            <td style="padding:10px 12px; text-align:right;">{dist_val}</td>
            <td style="padding:10px 12px; text-align:center;">
                <a href="{alert['link']}" style="color:#1a73e8; text-decoration:none; font-weight:600;">
                    📈 Chart
                </a>
            </td>
        </tr>"""

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
    </head>
    <body style="margin:0; padding:20px; font-family:Arial, sans-serif; background:#f5f5f5;">
      <div style="max-width:900px; margin:auto; background:#fff; border-radius:8px;
                  box-shadow:0 2px 8px rgba(0,0,0,0.1); overflow:hidden;">

        <!-- Header -->
        <div style="background:#1a1a2e; padding:20px 24px;">
          <h2 style="margin:0; color:#fff; font-size:18px;">
            📊 Portfolio Alert — {len(alerts)} stock(s) triggered
          </h2>
          <p style="margin:4px 0 0; color:#aaa; font-size:13px;">{run_time}</p>
        </div>

        <!-- Table -->
        <div style="padding:16px; overflow-x:auto;">
          <table style="width:100%; border-collapse:collapse; font-size:14px;">
            <thead>
              <tr style="background:#1a1a2e; color:#fff;">
                <th style="padding:10px 12px; text-align:left;">Account</th>
                <th style="padding:10px 12px; text-align:left;">Symbol</th>
                <th style="padding:10px 12px; text-align:left;">Alert Reason</th>
                <th style="padding:10px 12px; text-align:right;">Current Price</th>
                <th style="padding:10px 12px; text-align:right;">52W Low</th>
                <th style="padding:10px 12px; text-align:right;">52W High</th>
                <th style="padding:10px 12px; text-align:right;">Distance</th>
                <th style="padding:10px 12px; text-align:center;">Chart</th>
              </tr>
            </thead>
            <tbody>
              {rows}
            </tbody>
          </table>
        </div>

        <!-- Footer -->
        <div style="padding:12px 24px; background:#f9f9f9; border-top:1px solid #eee;
                    font-size:12px; color:#999; text-align:center;">
          Alerts based on 52-week range thresholds (≤10% from Low / ≥90% from Low).
          Duplicate alerts suppressed for 24 hours.
        </div>
      </div>
    </body>
    </html>"""

# ==================== SAVE NOTIFICATION ====================
def save_notification(symbol, trigger_type, price, message, link):
    """Persist a triggered notification to the DB. Returns True on success."""
    conn = cur = None
    try:
        conn = get_db_connection()
        if not conn:
            log.error("❌ DB connection failed — cannot save notification for %s", symbol)
            return False

        conn.ping(reconnect=True, attempts=3, delay=1)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO notifications
                (stock_symbol, trigger_type, trigger_price, message, action_link, is_sent)
            VALUES (%s, %s, %s, %s, %s, 1)
            """,
            (symbol, trigger_type, float(price), message, link)
        )
        conn.commit()
        log.info("✅ Saved notification to DB: %s — %s", symbol, trigger_type)
        return True
    except mysql.connector.Error as e:
        log.error("❌ MySQL error saving notification for %s: %s", symbol, e)
        if conn and conn.is_connected():
            try: conn.rollback()
            except: pass
        return False
    except Exception as e:
        log.error("❌ Unexpected error saving notification for %s: %s", symbol, e)
        if conn and conn.is_connected():
            try: conn.rollback()
            except: pass
        return False
    finally:
        try:
            if cur:  cur.close()
            if conn and conn.is_connected(): conn.close()
        except: pass

# ==================== DUPLICATE CHECK ====================
def is_trigger_already_sent(symbol, trigger_type):
    """Return True if the same trigger was already sent within the last 24 hours."""
    conn = cur = None
    try:
        conn = get_db_connection()
        if not conn:
            return False

        conn.ping(reconnect=True, attempts=3, delay=1)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM notifications
            WHERE stock_symbol = %s
              AND trigger_type  = %s
              AND created_at    > NOW() - INTERVAL 24 HOUR
            """,
            (symbol, trigger_type)
        )
        count = cur.fetchone()[0]
        if count > 0:
            log.info("⏭️  Skipping %s (%s) — already sent in last 24 h", symbol, trigger_type)
        return count > 0
    except Exception as e:
        log.warning("⚠️ Error checking duplicate for %s: %s", symbol, e)
        return False
    finally:
        try:
            if cur:  cur.close()
            if conn and conn.is_connected(): conn.close()
        except: pass

# ==================== HOLDINGS ====================
def get_all_equity_holdings():
    """Fetch open equity holdings across ALL active accounts."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return []

        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
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
            JOIN  accounts     a  ON p.account_id = a.account_id
            LEFT JOIN stocks_master sm ON p.stock_id = sm.stock_id
            WHERE a.is_active         = TRUE
              AND p.instrument_type   = 'EQUITY'
              AND p.is_open           = TRUE
              AND p.net_quantity      > 0
            ORDER BY p.symbol, a.account_id
            """
        )
        holdings = cursor.fetchall()
        cursor.close()
        return holdings
    except Exception as e:
        log.error("❌ Error fetching holdings: %s", e)
        return []
    finally:
        try:
            if conn and conn.is_connected(): conn.close()
        except: pass

# ==================== SCRIP MASTER ====================
def load_scrip_master():
    """
    Load the NSE ScripMaster CSV.
    Returns (dict_by_name, dict_by_symbol, dataframe) or ({}, {}, empty_df) on failure.
    """
    try:
        csv_path = os.path.join(PROJECT_ROOT, 'ScripMaster_all.csv')
        df = pd.read_csv(csv_path)
        df_stocks = df[df['Series'] == 'EQ'].copy()

        scrip_dict_by_name   = dict(zip(df_stocks['Name'],      df_stocks['ScripCode']))
        scrip_dict_by_symbol = {}
        for name, code in scrip_dict_by_name.items():
            symbol = name.split()[0].strip().upper()
            scrip_dict_by_symbol[symbol] = code

        log.info("✅ ScripMaster loaded — %d EQ entries", len(df_stocks))
        return scrip_dict_by_name, scrip_dict_by_symbol, df_stocks
    except FileNotFoundError:
        log.critical("❌ ScripMaster_all.csv not found at: %s", os.path.join(PROJECT_ROOT, 'ScripMaster_all.csv'))
        return {}, {}, pd.DataFrame()
    except Exception as e:
        log.error("❌ Failed to load ScripMaster: %s", e)
        return {}, {}, pd.DataFrame()

def get_scrip_code_from_csv(symbol_or_name, csv_by_name, csv_by_symbol, csv_df):
    """
    Multi-strategy lookup — returns (scrip_code, matched_name, exch, exch_type)
    or (None, None, None, None) if not found.
    """
    def _row_info(scrip_code):
        row = csv_df[csv_df['ScripCode'] == scrip_code].iloc[0]
        return (
            scrip_code,
            str(row.get('Exch',     'N')).strip().upper(),
            str(row.get('ExchType', 'C')).strip().upper()
        )

    # 1. Direct symbol match
    if symbol_or_name in csv_by_symbol:
        code = csv_by_symbol[symbol_or_name]
        code, exch, exch_type = _row_info(code)
        return code, symbol_or_name, exch, exch_type

    # 2. Direct name match
    if symbol_or_name in csv_by_name:
        code = csv_by_name[symbol_or_name]
        code, exch, exch_type = _row_info(code)
        return code, symbol_or_name, exch, exch_type

    # 3. Partial match on Name column
    matched = csv_df[csv_df['Name'].str.contains(symbol_or_name, case=False, na=False)]
    if not matched.empty:
        row = matched.iloc[0]
        return (int(row['ScripCode']), row['Name'],
                str(row.get('Exch', 'N')).strip().upper(),
                str(row.get('ExchType', 'C')).strip().upper())

    # 4. Partial match on ScripName column
    if 'ScripName' in csv_df.columns:
        matched = csv_df[csv_df['ScripName'].str.contains(symbol_or_name, case=False, na=False)]
        if not matched.empty:
            row = matched.iloc[0]
            return (int(row['ScripCode']), row['Name'],
                    str(row.get('Exch', 'N')).strip().upper(),
                    str(row.get('ExchType', 'C')).strip().upper())

    return None, None, None, None

# ==================== 52-WEEK DATA ====================
def fetch_52_week_data(scrip_code, symbol, exch, exch_type):
    """
    Fetch 52-week OHLC data via the broker API.
    Returns dict with week_52_high, week_52_low, current_price — or None on failure.
    """
    try:
        client = get_client()
        if client is None:
            log.warning("⚠️ get_client() returned None — skipping %s", symbol)
            return None

        end_date   = datetime.now().strftime('%Y-%m-%d')
        start_52w  = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')

        df_52w = client.historical_data(exch, exch_type, int(scrip_code), '1d', start_52w, end_date)

        if df_52w is None or df_52w.empty:
            log.warning("⚠️ No historical data returned for %s (%s:%s)", symbol, exch, exch_type)
            return None

        df_52w['Datetime'] = pd.to_datetime(df_52w['Datetime'])
        df_52w = df_52w.sort_values('Datetime')

        return {
            'week_52_high':  round(float(df_52w['High'].max()),          2),
            'week_52_low':   round(float(df_52w['Low'].min()),           2),
            'current_price': round(float(df_52w.iloc[-1]['Close']),      2),
        }
    except Exception as e:
        log.error("❌ Error fetching 52-week data for %s: %s", symbol, e)
        return None

# ==================== TRIGGER CHECK ====================
def check_90_percent_trigger(symbol, week_52_low, week_52_high, current_price):
    """
    Check if price has crossed the 90% (near high) or 10% (near low) threshold.
    Returns (triggered: bool, trigger_type: str|None, message: str|None).
    """
    price_range = week_52_high - week_52_low
    if price_range <= 0:
        return False, None, None

    upper_threshold = week_52_low + price_range * 0.90
    lower_threshold = week_52_low + price_range * 0.10

    if current_price >= upper_threshold:
        trigger_type = "90%_NEAR_52W_HIGH"
        msg  = f"🚀 ALERT: {symbol} is at 90% of its 52-Week range (Near HIGH)!\n"
        msg += f"Current Price  : ₹{current_price}\n"
        msg += f"52W High       : ₹{week_52_high}\n"
        msg += f"52W Low        : ₹{week_52_low}\n"
        msg += f"Distance to High: ₹{week_52_high - current_price:.2f}"
        return True, trigger_type, msg

    if current_price <= lower_threshold:
        trigger_type = "90%_NEAR_52W_LOW"
        msg  = f"⚠️ ALERT: {symbol} is at 10% of its 52-Week range (Near LOW)!\n"
        msg += f"Current Price  : ₹{current_price}\n"
        msg += f"52W Low        : ₹{week_52_low}\n"
        msg += f"52W High       : ₹{week_52_high}\n"
        msg += f"Distance to Low : ₹{current_price - week_52_low:.2f}"
        return True, trigger_type, msg

    return False, None, None

# ==================== MAIN ====================
def main():
    log.info("=" * 60)
    log.info("🚀 Starting background stock alert check")
    log.info("=" * 60)

    csv_by_name, csv_by_symbol, csv_df = load_scrip_master()
    if not csv_by_name:
        log.critical("❌ ScripMaster not available — aborting.")
        sys.exit(1)

    holdings = get_all_equity_holdings()
    if not holdings:
        log.warning("⚠️ No open equity holdings found — nothing to check.")
        return

    log.info("📋 Checking %d holdings across all active accounts...", len(holdings))

    # ✅ Collect all alerts first — don't send email inside loop
    all_alerts = []
    triggers_skipped = 0
    errors = 0

    for holding in holdings:
        symbol      = holding['symbol']
        client_code = holding.get('client_code', 'N/A')

        scrip_code, matched_name, exch, exch_type = get_scrip_code_from_csv(
            symbol, csv_by_name, csv_by_symbol, csv_df
        )

        if not scrip_code:
            log.warning("⚠️ '%s' not found in ScripMaster — skipping", symbol)
            errors += 1
            continue

        week_data = fetch_52_week_data(scrip_code, symbol, exch, exch_type)
        if not week_data:
            log.warning("⚠️ No 52-week data for %s — skipping", symbol)
            errors += 1
            continue

        triggered, trigger_type, trigger_msg = check_90_percent_trigger(
            symbol,
            week_data['week_52_low'],
            week_data['week_52_high'],
            week_data['current_price']
        )

        if not triggered:
            log.info("✅ %s — no trigger (₹%s)", symbol, week_data['current_price'])
            continue

        log.info("🚨 TRIGGER: %s — %s (acct: %s)", symbol, trigger_type, client_code)

        if is_trigger_already_sent(symbol, trigger_type):
            triggers_skipped += 1
            continue

        # Save to DB immediately (one per stock, as before)
        link = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}"
        saved = save_notification(symbol, trigger_type, week_data['current_price'], trigger_msg, link)

        # Compute distance to the relevant boundary for the table column
        if "HIGH" in trigger_type:
            distance = week_data['week_52_high'] - week_data['current_price']
        else:
            distance = week_data['current_price'] - week_data['week_52_low']

        if saved:
            all_alerts.append({
                'symbol':        symbol,
                'client_code':   client_code,
                'trigger_type':  trigger_type,
                'link':          link,
                # numeric fields for the HTML table
                'current_price': week_data['current_price'],
                'week_52_low':   week_data['week_52_low'],
                'week_52_high':  week_data['week_52_high'],
                'distance':      round(distance, 2),
            })
        else:
            errors += 1

    # ✅ Send ONE combined email for all alerts
    if all_alerts:
        subject   = f"Portfolio Alert — {len(all_alerts)} stock(s) triggered"
        run_time  = datetime.now().strftime('%d %b %Y %I:%M %p')
        body_html = build_alert_email_html(all_alerts, run_time)
        email_sent = send_email_alert(subject, body_html)
        if email_sent:
            log.info("📧 Single combined email sent for %d alert(s)", len(all_alerts))
        else:
            log.error("❌ Combined email failed")
    else:
        log.info("📭 No new alerts to email.")

    log.info("=" * 60)
    log.info("✅ Run complete — %d alert(s) sent | %d skipped (24h dedup) | %d error(s)",
             len(all_alerts), triggers_skipped, errors)
    log.info("=" * 60)

if __name__ == "__main__":
    main()