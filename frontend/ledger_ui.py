"""
ledger_ui.py — Ledger Tab UI
==============================
Renders the Ledger tab for a single user.

Features:
  • Broker validation warning — warns if uploaded file doesn't match user's broker
  • Upload section with auto-detected broker shown to user
  • Period summaries table — one row per uploaded file, showing:
      date range | opening balance | closing balance | inserted | skipped | duplicate flag
  • Multi-upload intelligence:
      - April + May uploaded separately → 2 period rows, total unique rows in DB
      - April+May combined file → "all_duplicate" = true, labelled clearly
      - No data duplication in ledger_entries (MD5 dedup)
  • Full ledger entries table with date filter + search
  • Inline balance display: negative = Dr (owes broker), positive = Cr (broker owes)
"""
import streamlit as st
import pandas as pd
from api_client import get_ledger_periods, upload_ledger


# ─── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_bal(v) -> str:
    """Format a signed balance. Negative = Dr (debit), Positive = Cr (credit)."""
    try:
        n = float(v)
    except Exception:
        return str(v)
    if n < 0:
        return f"₹{abs(n):,.2f} Dr"
    return f"₹{n:,.2f} Cr"


def _fmt_inr(v) -> str:
    try:
        n = float(v)
        return f"₹{n:,.2f}"
    except Exception:
        return "—"


def _color_balance(val):
    """Red for Dr (negative), green for Cr (positive)."""
    try:
        n = float(val)
        if n < 0:
            return "color:#f48fb1;font-weight:bold"
        if n > 0:
            return "color:#6fcf97"
    except Exception:
        pass
    return ""


def _color_pnl(val):
    try:
        n = float(val)
        if n > 0:
            return "color:#6fcf97"
        if n < 0:
            return "color:#f48fb1"
    except Exception:
        pass
    return ""


# ─── Broker compatibility map ──────────────────────────────────────────────────

_BROKER_LEDGER_MAP = {
    '5paisa':   '5paisa',
    'iifl':     'IIFL',
    'zerodha':  'Zerodha',
    'multiple': None,   # Multi-broker user → allow any
}

_ACCEPTED_EXTENSIONS = {
    '5paisa':  ['xls', 'xlsx'],
    'IIFL':    ['xls', 'xlsx'],
    'Zerodha': ['xls', 'xlsx'],
}

_DOWNLOAD_HINTS = {
    '5paisa':  "📌 Ledger path: Report → Ledger Report → Select FY date range → Download",
    'IIFL':    "📌 Ledger path: Backoffice → Account → Statement of Account (Equity/Currency Ledger)",
    'Zerodha': "📌 Ledger path: Console → Reports → Ledger → Select FY → Download",
}


# ─── Period summaries section ─────────────────────────────────────────────────

def _render_period_summaries(user_id: int):
    """Show all uploaded ledger files as a summary table."""
    try:
        periods = get_ledger_periods(user_id)
    except Exception as e:
        st.warning(f"Could not load period summaries: {e}")
        return

    if not periods:
        st.info("No ledger files uploaded yet.")
        return

    # ── Aggregate stats ──────────────────────────────────────────────────────
    total_inserted = sum(p.get('total_inserted', 0) for p in periods)
    total_files    = len(periods)
    unique_months  = set()
    for p in periods:
        if p.get('period_start') and p.get('period_end'):
            try:
                start = pd.to_datetime(p['period_start'])
                end   = pd.to_datetime(p['period_end'])
                for m in pd.date_range(start, end, freq='MS'):
                    unique_months.add(m.strftime('%Y-%m'))
            except Exception:
                pass

    c1, c2, c3 = st.columns(3)
    c1.metric("Uploaded Files", total_files)
    c2.metric("Total Rows in DB", total_inserted)
    c3.metric("Months Covered", len(unique_months))

    st.divider()

    # ── Period table ─────────────────────────────────────────────────────────
    rows = []
    for p in periods:
        ob = p.get('opening_balance', 0) or 0
        cb = p.get('closing_balance', 0) or 0
        is_dup = bool(p.get('all_duplicate', 0))
        rows.append({
            'Broker':          p.get('broker', '—'),
            'File':            p.get('source_file', '—'),
            'Period Start':    p.get('period_start', '—'),
            'Period End':      p.get('period_end', '—'),
            'Opening Balance': ob,
            'Closing Balance': cb,
            'Funds Added':     p.get('funds_added', 0) or 0,
            'Eq Net P&L':      p.get('eq_net', 0) or 0,
            'F&O Net P&L':     p.get('fno_net', 0) or 0,
            'Rows Inserted':   p.get('total_inserted', 0),
            'Rows Skipped':    p.get('total_skipped', 0),
            'Status':          '🔁 All Duplicate' if is_dup else '✅ New Rows',
            'Uploaded At':     str(p.get('uploaded_at', ''))[:16],
        })

    df = pd.DataFrame(rows)

    # Format balance columns for display
    for col in ('Opening Balance', 'Closing Balance'):
        df[col] = df[col].apply(_fmt_bal)
    for col in ('Funds Added', 'Eq Net P&L', 'F&O Net P&L'):
        df[col] = df[col].apply(_fmt_inr)

    st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Explain multi-upload scenario ────────────────────────────────────────
    dup_count = sum(1 for p in periods if p.get('all_duplicate'))
    if dup_count > 0:
        st.info(
            f"ℹ️ **{dup_count} file(s) marked '🔁 All Duplicate'** — every row in those files "
            "already existed in the database (likely uploaded earlier as part of a monthly file). "
            "No data was duplicated. The period summary is recorded for reference."
        )


# ─── Ledger entries table ─────────────────────────────────────────────────────

def _render_entries(user_id: int, get_ledger_fn):
    entries = []
    try:
        entries = get_ledger_fn(user_id)
    except Exception as e:
        st.error(f"Could not load ledger entries: {e}")
        return

    if not entries:
        st.info("No ledger entries found. Upload a ledger file above.")
        return

    df = pd.DataFrame(entries)

    # ── Filters ──────────────────────────────────────────────────────────────
    col_f1, col_f2, col_f3 = st.columns([2, 2, 3])

    # Date range filter
    all_dates = pd.to_datetime(df['date'], errors='coerce').dropna()
    if not all_dates.empty:
        min_dt = all_dates.min().date()
        max_dt = all_dates.max().date()
        sel_start = col_f1.date_input("From", value=min_dt, key="ledger_date_from")
        sel_end   = col_f2.date_input("To",   value=max_dt, key="ledger_date_to")
        df = df[
            (pd.to_datetime(df['date'], errors='coerce').dt.date >= sel_start) &
            (pd.to_datetime(df['date'], errors='coerce').dt.date <= sel_end)
        ]

    # Search filter
    search = col_f3.text_input("🔍 Search particulars", key="ledger_search")
    if search.strip():
        mask = df['particular'].str.contains(search.strip(), case=False, na=False)
        df = df[mask]

    st.caption(f"Showing **{len(df)}** entries")

    # ── Build display DataFrame ───────────────────────────────────────────────
    disp = df[['date', 'particular', 'description', 'debit', 'credit', 'balance', 'broker']].copy()
    disp.columns = ['Date', 'Particulars', 'Description', 'Debit (₹)', 'Credit (₹)', 'Balance', 'Broker']

    # Format debit/credit as plain INR, balance with Dr/Cr
    disp['Debit (₹)']  = disp['Debit (₹)'].apply(lambda x: f"₹{float(x):,.2f}" if float(x or 0) else "—")
    disp['Credit (₹)'] = disp['Credit (₹)'].apply(lambda x: f"₹{float(x):,.2f}" if float(x or 0) else "—")
    disp['Balance']     = disp['Balance'].apply(_fmt_bal)

    # Color balance column
    try:
        styled = disp.style.map(
            lambda v: "color:#f48fb1;font-weight:bold" if "Dr" in str(v) else
                      ("color:#6fcf97" if "Cr" in str(v) else ""),
            subset=["Balance"]
        )
    except Exception:
        styled = disp

    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── Summary metrics ───────────────────────────────────────────────────────
    st.divider()
    raw_df = df.copy()
    raw_df['debit']  = pd.to_numeric(raw_df['debit'],  errors='coerce').fillna(0)
    raw_df['credit'] = pd.to_numeric(raw_df['credit'], errors='coerce').fillna(0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Debit",  f"₹{raw_df['debit'].sum():,.2f}")
    c2.metric("Total Credit", f"₹{raw_df['credit'].sum():,.2f}")
    c3.metric("Net",          f"₹{(raw_df['credit'].sum() - raw_df['debit'].sum()):,.2f}")
    if 'balance' in df.columns and len(df):
        last_bal = float(df.iloc[-1].get('balance', 0) or 0)
        c4.metric("Last Balance", _fmt_bal(last_bal))


# ─── Main render function (called from app.py) ────────────────────────────────

def render_ledger_tab(
    user_id: int,
    upload_ledger_fn,
    get_ledger_fn,
    user_broker: str = '',
):
    """
    user_broker: the broker stored for this user (from users table).
                 Used to warn if a mismatched ledger file is uploaded.
    """
    st.subheader("📒 Ledger")

    # ── Broker context banner ─────────────────────────────────────────────────
    canonical_user_broker = _BROKER_LEDGER_MAP.get(user_broker.lower(), user_broker) if user_broker else ''
    if canonical_user_broker and canonical_user_broker != 'multiple':
        hint = _DOWNLOAD_HINTS.get(canonical_user_broker, '')
        st.info(
            f"This user is registered as **{canonical_user_broker}**. "
            f"Please upload the {canonical_user_broker} ledger file.  \n{hint}"
        )
    elif user_broker.lower() == 'multiple':
        st.info("Multi-broker user — upload ledger files from any supported broker (5paisa, IIFL, Zerodha).")

    # ── Upload section ────────────────────────────────────────────────────────
    with st.expander("📤 Upload Ledger File", expanded=True):
        uploaded = st.file_uploader(
            "Choose ledger file (.xls / .xlsx)",
            type=['xls', 'xlsx'],
            key='ledger_uploader',
        )

        # Override broker — allow user to force if auto-detect fails
        override_broker = st.selectbox(
            "Broker (auto-detect from filename, or override)",
            options=['auto', '5paisa', 'IIFL', 'Zerodha'],
            index=0,
            key='ledger_broker_override',
            help="Leave as 'auto' — the system detects from the filename automatically.",
        )

        if st.button("▶ Upload & Process", type="primary",
                     disabled=(uploaded is None), key="ledger_upload_btn"):
            with st.spinner("Processing ledger file…"):
                try:
                    result = upload_ledger_fn(
                        uploaded,
                        user_id,
                        broker=override_broker,
                    )
                except Exception as e:
                    st.error(f"Upload error: {e}")
                    result = None

            if result:
                # Broker mismatch warning
                if result.get('broker_warning'):
                    st.warning(result['broker_warning'])

                status = result.get('status', 'error')
                msg    = result.get('message', '')

                if status == 'success':
                    st.success(msg)
                    summary = result.get('summary', {})
                    if summary:
                        sc1, sc2, sc3 = st.columns(3)
                        sc1.metric("Period", summary.get('date_range', '—'))
                        sc2.metric(
                            "Opening Balance",
                            _fmt_bal(summary.get('opening_balance', 0))
                        )
                        sc3.metric(
                            "Closing Balance",
                            _fmt_bal(summary.get('closing_balance', 0))
                        )
                        sc4, sc5, sc6 = st.columns(3)
                        sc4.metric("Funds Added",    _fmt_inr(summary.get('funds_added', 0)))
                        sc5.metric("Eq Net P&L",     _fmt_inr(summary.get('eq_net', 0)))
                        sc6.metric("F&O Net P&L",    _fmt_inr(summary.get('fno_net', 0)))
                    # Clear cached data so tables refresh
                    for k in [f'ledger_entries_{user_id}', f'ledger_periods_{user_id}']:
                        st.session_state.pop(k, None)
                    st.rerun()

                elif status == 'info':
                    # 0 inserted (all duplicates)
                    st.info(msg)
                    if result.get('all_duplicate'):
                        st.caption(
                            "The file's rows already existed in the database from a previous upload. "
                            "Your ledger data is complete — no action needed."
                        )
                    for k in [f'ledger_entries_{user_id}', f'ledger_periods_{user_id}']:
                        st.session_state.pop(k, None)

                elif status == 'skipped':
                    st.warning(msg)

                else:
                    st.error(msg)

    st.divider()

    # ── Multi-upload explanation ──────────────────────────────────────────────
    with st.expander("ℹ️ How multiple ledger uploads work", expanded=False):
        st.markdown("""
**You can safely upload ledgers multiple times without creating duplicate entries.**

| Upload sequence | What happens |
|---|---|
| April ledger | 100 rows inserted, period Apr recorded |
| May ledger | 95 new rows inserted, period May recorded |
| April+May combined file | 0 new rows (all already in DB from above), period Apr–May recorded as *All Duplicate* |
| July ledger | 110 rows inserted, period Jul recorded |

The **Uploaded Files** section below shows one row per file you've uploaded.  
Files marked **🔁 All Duplicate** are fully covered by rows already in the DB from previous uploads — the data is not lost, just already present.  
The **Ledger Entries** table always shows the unique combined view across all uploads.
""")

    # ── Uploaded Files / Period Summaries ─────────────────────────────────────
    st.subheader("📁 Uploaded Files")
    if st.button("🔄 Refresh", key="ledger_periods_refresh"):
        st.session_state.pop(f'ledger_periods_{user_id}', None)

    _render_period_summaries(user_id)

    st.divider()

    # ── Ledger Entries ────────────────────────────────────────────────────────
    st.subheader("📋 All Ledger Entries")
    if st.button("🔄 Refresh Entries", key="ledger_entries_refresh"):
        st.session_state.pop(f'ledger_entries_{user_id}', None)

    _render_entries(user_id, get_ledger_fn)


# ─── Group ledger tab (stub — groups show individual member ledgers) ──────────
def render_group_ledger_tab(group_id: int, members: list, get_ledger_fn):
    st.subheader("📒 Group Ledger")
    if not members:
        st.info("No members in this group.")
        return
    member_names = [m['username'] for m in members]
    chosen_name = st.selectbox("Select member", member_names, key="grp_ledger_member")
    chosen = next(m for m in members if m['username'] == chosen_name)
    render_ledger_tab(
        user_id=chosen['id'],
        upload_ledger_fn=None,   # read-only in group context
        get_ledger_fn=get_ledger_fn,
        user_broker=chosen.get('broker', ''),
    )