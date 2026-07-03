"""
backend_alerts.py
-----------------
Headless backend script that alerts when:
  1. A stock moves ±10% vs its previous close (intraday momentum alert)
  2. A stock shows a sustained trend — closing in the same direction
     for N consecutive days, OR cumulative move > threshold over N days.

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

# ==================== TREND DETECTION CONFIG ====================
TREND_CONSECUTIVE_DAYS = 7      # How many consecutive same-direction closes = trend alert
TREND_CUMULATIVE_PCT   = 15.0   # Cumulative % move over TREND_WINDOW_DAYS = trend alert
TREND_WINDOW_DAYS      = 7      # Window for cumulative % check
FETCH_HISTORY_DAYS     = 30     # Calendar days to fetch (guarantees enough trading days)

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


def _alert_type_meta(trigger_type):
    """
    Returns (icon, badge_label, badge_color, row_bg, pct_color, direction_label)
    based on trigger_type string.
    """
    t = trigger_type.upper()

    if "TREND_DOWN" in t:
        return ("📉", "TREND ↓", "#7f1d1d", "#fff0f0", "#dc2626", "SUSTAINED DOWN ▼")
    if "TREND_UP" in t:
        return ("📈", "TREND ↑", "#14532d", "#f0fff4", "#16a34a", "SUSTAINED UP ▲")
    if "10%_MOVE_UP" in t or "MOVE_UP" in t:
        return ("🚀", "SPIKE ↑", "#1e3a5f", "#fff8f0", "#16a34a", "SPIKE UP ▲")
    # default: move down
    return ("⚠️", "SPIKE ↓", "#78350f", "#f0f8ff", "#dc2626", "SPIKE DOWN ▼")


def build_alert_email_html(alerts, run_time):
    """Build a styled HTML email with one table row per triggered stock."""

    rows = ""
    for alert in alerts:
        icon, badge, badge_bg, row_bg, pct_color, direction = _alert_type_meta(alert['trigger_type'])
        is_up     = "UP" in alert['trigger_type']
        pct       = alert['pct_change']
        pct_str   = f"{'+' if is_up else ''}{pct:.2f}%"

        # Extra trend detail (only for trend alerts)
        trend_detail = ""
        if "TREND" in alert['trigger_type'].upper() and alert.get('trend_detail'):
            trend_detail = f"<br><span style='font-size:11px;color:#555;'>{alert['trend_detail']}</span>"

        rows += f"""
        <tr style="background:{row_bg};">
            <td style="padding:10px 12px; font-weight:600;">{alert['client_code']}</td>
            <td style="padding:10px 12px; font-weight:700;">{alert['symbol']}</td>
            <td style="padding:10px 12px;">
                <span style="background:{badge_bg};color:#fff;padding:2px 7px;
                             border-radius:4px;font-size:11px;font-weight:700;">
                    {badge}
                </span>
                &nbsp;{icon} {direction}{trend_detail}
            </td>
            <td style="padding:10px 12px; text-align:right;">₹{alert['prev_close']}</td>
            <td style="padding:10px 12px; text-align:right;">₹{alert['current_price']}</td>
            <td style="padding:10px 12px; text-align:right;
                       font-weight:700; color:{pct_color};">
                {pct_str}
            </td>
            <td style="padding:10px 12px; text-align:center;">
                <a href="{alert['link']}" style="color:#1a73e8; text-decoration:none; font-weight:600;">
                    📊 Chart
                </a>
            </td>
        </tr>"""

    # Summary counts per alert type
    spike_count = sum(1 for a in alerts if "TREND" not in a['trigger_type'].upper())
    trend_count = sum(1 for a in alerts if "TREND" in a['trigger_type'].upper())
    summary_parts = []
    if spike_count:
        summary_parts.append(f"<b>{spike_count}</b> single-day spike(s)")
    if trend_count:
        summary_parts.append(f"<b>{trend_count}</b> multi-day trend(s)")
    summary_str = " &nbsp;|&nbsp; ".join(summary_parts) if summary_parts else f"<b>{len(alerts)}</b> alert(s)"

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
          <p style="margin:6px 0 0; color:#ccc; font-size:13px;">{summary_str}</p>
          <p style="margin:4px 0 0; color:#aaa; font-size:12px;">{run_time}</p>
        </div>

        <!-- Legend -->
        <div style="padding:10px 20px; background:#f0f4ff; border-bottom:1px solid #dde3f0;
                    font-size:12px; color:#444;">
          &nbsp;
          <span style="background:#7f1d1d;color:#fff;padding:2px 6px;border-radius:3px;">TREND ↓</span>
          &nbsp;Closed lower for {TREND_CONSECUTIVE_DAYS}+ consecutive days
          &nbsp;&nbsp;&nbsp;
          <span style="background:#14532d;color:#fff;padding:2px 6px;border-radius:3px;">TREND ↑</span>
          &nbsp;Closed higher for {TREND_CONSECUTIVE_DAYS}+ consecutive days
          &nbsp;&nbsp;&nbsp;
          <span style="background:#1e3a5f;color:#fff;padding:2px 6px;border-radius:3px;">SPIKE</span>
          &nbsp;Single-day ±10% move vs prev close
        </div>

        <!-- Table -->
        <div style="padding:16px; overflow-x:auto;">
          <table style="width:100%; border-collapse:collapse; font-size:14px;">
            <thead>
              <tr style="background:#1a1a2e; color:#fff;">
                <th style="padding:10px 12px; text-align:left;">Account</th>
                <th style="padding:10px 12px; text-align:left;">Symbol</th>
                <th style="padding:10px 12px; text-align:left;">Alert Type &amp; Reason</th>
                <th style="padding:10px 12px; text-align:right;">Prev Close</th>
                <th style="padding:10px 12px; text-align:right;">Current Price</th>
                <th style="padding:10px 12px; text-align:right;">% Change</th>
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
          SPIKE alerts fire when a stock moves ±10% in a single day vs previous close.<br>
          TREND alerts fire when a stock closes in the same direction for
          {TREND_CONSECUTIVE_DAYS}+ consecutive days
          OR moves {TREND_CUMULATIVE_PCT}%+ cumulatively over {TREND_WINDOW_DAYS} trading days.<br>
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
              AND trigger_time  > NOW() - INTERVAL 24 HOUR
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
        df = pd.read_csv(csv_path, low_memory=False)   # suppresses DtypeWarning
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

# ==================== PRICE DATA ====================
def fetch_price_data(client, scrip_code, symbol, exch, exch_type):
    """
    Fetch the last FETCH_HISTORY_DAYS calendar days of daily OHLC data.

    NOTE: 'client' is passed in from main() — created ONCE for the whole run,
    not per stock. This avoids re-authenticating 128 times and triggering 401s.

    Returns dict with:
        prev_close     — second-to-last candle's Close (yesterday's close)
        current_price  — last candle's Close (today's latest close)
        closes         — list of all Close prices sorted oldest→newest
        dates          — corresponding list of date strings
    Returns None on failure.
    """
    try:
        end_date   = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=FETCH_HISTORY_DAYS)).strftime('%Y-%m-%d')

        df = client.historical_data(exch, exch_type, int(scrip_code), '1d', start_date, end_date)

        if df is None or df.empty:
            log.warning("⚠️ No price data returned for %s (%s:%s)", symbol, exch, exch_type)
            return None

        df['Datetime'] = pd.to_datetime(df['Datetime'])
        df = df.sort_values('Datetime').reset_index(drop=True)

        if len(df) < 2:
            log.warning("⚠️ Not enough candles for %s to compute prev_close", symbol)
            return None

        closes = [round(float(c), 2) for c in df['Close'].tolist()]
        dates  = [str(d.date()) for d in df['Datetime']]

        return {
            'prev_close':    closes[-2],
            'current_price': closes[-1],
            'closes':        closes,
            'dates':         dates,
        }
    except Exception as e:
        log.error("❌ Error fetching price data for %s: %s", symbol, e)
        return None

# ==================== TRIGGER CHECK: SINGLE-DAY SPIKE ====================
def check_10_percent_move(symbol, prev_close, current_price):
    """
    Check if the stock has moved more than ±10% vs its previous close.
    Returns (triggered, trigger_type, message, pct_change).
    """
    if prev_close <= 0:
        return False, None, None, 0.0

    pct_change = ((current_price - prev_close) / prev_close) * 100

    if pct_change >= 10.0:
        trigger_type = "10%_MOVE_UP"
        msg  = f"🚀 ALERT: {symbol} moved UP {pct_change:.2f}% vs previous close!\n"
        msg += f"Previous Close : ₹{prev_close}\n"
        msg += f"Current Price  : ₹{current_price}\n"
        msg += f"Change         : +{pct_change:.2f}%"
        return True, trigger_type, msg, round(pct_change, 2)

    if pct_change <= -10.0:
        trigger_type = "10%_MOVE_DOWN"
        msg  = f"⚠️ ALERT: {symbol} moved DOWN {abs(pct_change):.2f}% vs previous close!\n"
        msg += f"Previous Close : ₹{prev_close}\n"
        msg += f"Current Price  : ₹{current_price}\n"
        msg += f"Change         : {pct_change:.2f}%"
        return True, trigger_type, msg, round(pct_change, 2)

    return False, None, None, round(pct_change, 2)

# ==================== TRIGGER CHECK: MULTI-DAY TREND ====================
def check_trend_pattern(symbol, closes, dates):
    """
    Detect sustained directional trends in closing prices.

    Two checks:
    A) Consecutive closes: if the last TREND_CONSECUTIVE_DAYS candles all closed
       in the same direction (each day lower/higher than the previous), fire alert.

    B) Cumulative move: if the stock has moved more than ±TREND_CUMULATIVE_PCT%
       over the last TREND_WINDOW_DAYS trading days, fire alert.

    Returns list of dicts: [{'trigger_type', 'message', 'pct_change', 'trend_detail'}, ...]
    Multiple triggers possible (e.g. both consecutive + cumulative).
    Returns [] if no trend detected.
    """
    alerts = []

    if len(closes) < 2:
        return alerts

    # ---- A) Consecutive same-direction closes ----
    if len(closes) >= TREND_CONSECUTIVE_DAYS + 1:
        window = closes[-(TREND_CONSECUTIVE_DAYS + 1):]   # N+1 candles → N day-over-day diffs
        day_moves = [window[i+1] - window[i] for i in range(len(window) - 1)]

        all_down = all(m < 0 for m in day_moves)
        all_up   = all(m > 0 for m in day_moves)

        if all_down or all_up:
            direction   = "DOWN" if all_down else "UP"
            cum_pct     = ((closes[-1] - closes[-(TREND_CONSECUTIVE_DAYS + 1)])
                           / closes[-(TREND_CONSECUTIVE_DAYS + 1)]) * 100
            trigger_type = f"TREND_{direction}_{TREND_CONSECUTIVE_DAYS}D_CONSECUTIVE"

            # Build a mini summary e.g. "Day 1: ₹100 → Day 7: ₹88 (−12.0%)"
            start_price  = closes[-(TREND_CONSECUTIVE_DAYS + 1)]
            start_date   = dates[-(TREND_CONSECUTIVE_DAYS + 1)] if len(dates) > TREND_CONSECUTIVE_DAYS else "N/A"
            end_date_str = dates[-1] if dates else "N/A"
            detail = (f"{TREND_CONSECUTIVE_DAYS} consecutive {direction.lower()} days "
                      f"({start_date} ₹{start_price} → {end_date_str} ₹{closes[-1]}, "
                      f"cumulative {cum_pct:+.2f}%)")

            msg  = f"📉 TREND ALERT: {symbol} closed {direction} for {TREND_CONSECUTIVE_DAYS} consecutive days!\n"
            msg += f"Period      : {start_date} to {end_date_str}\n"
            msg += f"Start Price : ₹{start_price}\n"
            msg += f"End Price   : ₹{closes[-1]}\n"
            msg += f"Cumulative  : {cum_pct:+.2f}%\n"
            msg += f"Daily moves : {[round(m, 2) for m in day_moves]}"

            alerts.append({
                'trigger_type': trigger_type,
                'message':      msg,
                'pct_change':   round(cum_pct, 2),
                'trend_detail': detail,
            })
            log.info("📉 TREND CONSECUTIVE %s: %s — %d days %s, cumulative %.2f%%",
                     direction, symbol, TREND_CONSECUTIVE_DAYS, direction, cum_pct)

    # ---- B) Cumulative % move over TREND_WINDOW_DAYS ----
    if len(closes) >= TREND_WINDOW_DAYS + 1:
        base_price  = closes[-(TREND_WINDOW_DAYS + 1)]
        cum_pct     = ((closes[-1] - base_price) / base_price) * 100
        start_date  = dates[-(TREND_WINDOW_DAYS + 1)] if len(dates) > TREND_WINDOW_DAYS else "N/A"
        end_date_str = dates[-1] if dates else "N/A"

        if abs(cum_pct) >= TREND_CUMULATIVE_PCT:
            direction    = "UP" if cum_pct > 0 else "DOWN"
            trigger_type = f"TREND_{direction}_{TREND_WINDOW_DAYS}D_CUMULATIVE"

            # Avoid double-alerting if consecutive trigger already captured same direction
            already_alerted = any(
                f"TREND_{direction}" in a['trigger_type'] for a in alerts
            )
            if not already_alerted:
                detail = (f"Cumulative {cum_pct:+.2f}% over {TREND_WINDOW_DAYS} trading days "
                          f"({start_date} ₹{base_price} → {end_date_str} ₹{closes[-1]})")

                msg  = f"📊 CUMULATIVE TREND ALERT: {symbol} moved {cum_pct:+.2f}% over {TREND_WINDOW_DAYS} trading days!\n"
                msg += f"Period     : {start_date} to {end_date_str}\n"
                msg += f"Base Price : ₹{base_price}\n"
                msg += f"End Price  : ₹{closes[-1]}\n"
                msg += f"Change     : {cum_pct:+.2f}%"

                alerts.append({
                    'trigger_type': trigger_type,
                    'message':      msg,
                    'pct_change':   round(cum_pct, 2),
                    'trend_detail': detail,
                })
                log.info("📊 TREND CUMULATIVE %s: %s — %.2f%% over %d days",
                         direction, symbol, cum_pct, TREND_WINDOW_DAYS)

    return alerts

# ==================== MAIN ====================
def main():
    log.info("=" * 60)
    log.info("🚀 Starting background stock alert check")
    log.info("   • Single-day spike  : ±10%% vs prev close")
    log.info("   • Consecutive trend : %d same-direction closes", TREND_CONSECUTIVE_DAYS)
    log.info("   • Cumulative trend  : ±%.0f%% over %d trading days",
             TREND_CUMULATIVE_PCT, TREND_WINDOW_DAYS)
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

    # ── KEY FIX: create ONE authenticated client for the entire run ──
    # Previously get_client() was called inside fetch_price_data() — once per stock.
    # That caused 128 "Using saved token..." prints and, when the token was stale,
    # 128 consecutive 401 Unauthorized errors. Now we authenticate once here,
    # and auth_manager will auto-relogin if the saved token has expired.
    log.info("🔐 Authenticating with 5paisa API...")
    client = get_client()
    if client is None:
        log.critical("❌ Could not get authenticated client — aborting.")
        sys.exit(1)
    log.info("✅ Authenticated successfully.")

    all_alerts       = []
    triggers_skipped = 0
    errors           = 0

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

        # Pass the shared client — no re-authentication per stock
        price_data = fetch_price_data(client, scrip_code, symbol, exch, exch_type)
        if not price_data:
            log.warning("⚠️ No price data for %s — skipping", symbol)
            errors += 1
            continue

        # ---- Collect all triggers for this symbol ----
        symbol_triggers = []

        # 1. Single-day spike check
        triggered, trigger_type, trigger_msg, pct_change = check_10_percent_move(
            symbol, price_data['prev_close'], price_data['current_price']
        )
        if triggered:
            symbol_triggers.append({
                'trigger_type': trigger_type,
                'message':      trigger_msg,
                'pct_change':   pct_change,
                'trend_detail': None,
            })
        else:
            log.info("✅ %s — no spike (%.2f%% day move, ₹%s)",
                     symbol, pct_change, price_data['current_price'])

        # 2. Multi-day trend check
        if len(price_data.get('closes', [])) >= 2:
            trend_triggers = check_trend_pattern(symbol, price_data['closes'], price_data['dates'])
            symbol_triggers.extend(trend_triggers)

        if not symbol_triggers:
            continue

        # ---- Dedup & save each trigger ----
        link = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}"

        for trig in symbol_triggers:
            tt = trig['trigger_type']
            log.info("🚨 TRIGGER: %s — %s %.2f%% (acct: %s)",
                     symbol, tt, trig['pct_change'], client_code)

            if is_trigger_already_sent(symbol, tt):
                triggers_skipped += 1
                continue

            saved = save_notification(symbol, tt, price_data['current_price'], trig['message'], link)

            if saved:
                all_alerts.append({
                    'symbol':        symbol,
                    'client_code':   client_code,
                    'trigger_type':  tt,
                    'link':          link,
                    'prev_close':    price_data['prev_close'],
                    'current_price': price_data['current_price'],
                    'pct_change':    trig['pct_change'],
                    'trend_detail':  trig.get('trend_detail'),
                })
            else:
                errors += 1

    # ---- Send ONE combined email for all alerts ----
    if all_alerts:
        subject   = f"Portfolio Alert — {len(all_alerts)} trigger(s) across your holdings"
        run_time  = datetime.now().strftime('%d %b %Y %I:%M %p')
        body_html = build_alert_email_html(all_alerts, run_time)
        email_sent = send_email_alert(subject, body_html)
        if email_sent:
            log.info("📧 Combined email sent for %d alert(s)", len(all_alerts))
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