"""
IIFL Equity/Currency Ledger Parser — v3 (Fixed)
=================================================
Fixes vs v2:
  1. Dr/Cr SIGN applied correctly to balances:
       "494444.92 Dr"  → -494444.92  (client owes broker)
       "3252860.6 Cr"  → +3252860.6  (broker owes client)
  2. Column positions resolved dynamically from the sparse 41-column IIFL file
  3. Date format DD/MM/YY handled with dayfirst=True
  4. Opening balance row excluded from transactions list (no date) but its
     signed balance is captured for the period summary
  5. Closing balance taken from the last row that has a valid date
"""
import io
import re
import pandas as pd
from collections import defaultdict


# ─── Balance-aware float (handles "421489.55 Dr" / "3252860.6 Cr") ───────────
def _sf_balance(val, default=0.0) -> float:
    """
    Parse IIFL balance strings.  Dr = client owes broker (negative).
                                  Cr = broker owes client (positive).
    """
    try:
        s = str(val).strip()
        is_dr = bool(re.search(r'\bDr\b', s, re.IGNORECASE))
        s = re.sub(r'\s*(Dr|Cr)\s*$', '', s, flags=re.IGNORECASE)
        s = s.replace(',', '')
        v = float(s) if s not in ('', 'nan', 'None') else default
        return -v if is_dr else v
    except Exception:
        return default


def _sf(val, default=0.0) -> float:
    """Plain safe-float — strips Dr/Cr but does NOT apply sign (used for Debit/Credit amounts)."""
    try:
        s = str(val).strip()
        s = re.sub(r'\s*(Dr|Cr)\s*$', '', s, flags=re.IGNORECASE)
        s = s.replace(',', '')
        return float(s) if s not in ('', 'nan', 'None') else default
    except Exception:
        return default


def _parse_date(val) -> str | None:
    """Convert '04/05/26' or '01/05/2026' → '2026-05-04'. Returns None on failure."""
    try:
        s = str(val).strip()
        if not s or s in ('nan', 'None', 'Date'):
            return None
        dt = pd.to_datetime(s, dayfirst=True, errors='coerce')
        if pd.isna(dt):
            return None
        return dt.strftime('%Y-%m-%d')
    except Exception:
        return None


def parse(file) -> dict:
    file_bytes = file.read() if hasattr(file, 'read') else file

    df_raw = None
    for engine in ('xlrd', 'openpyxl'):
        try:
            df_raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0,
                                   header=None, engine=engine)
            break
        except Exception:
            continue
    if df_raw is None:
        raise ValueError('Cannot open IIFL ledger file')

    # ── Find column positions from header row ─────────────────────────────
    hdr_row = None
    col: dict[str, int] = {}

    for i in range(min(40, len(df_raw))):
        row = list(df_raw.iloc[i])
        for j, v in enumerate(row):
            sv = str(v).strip()
            if sv == 'Date' and 'Date' not in col:
                col['Date'] = j
            elif sv == 'Particulars' and 'Particulars' not in col:
                col['Particulars'] = j
            elif sv == 'Voucher' and 'Voucher' not in col:
                col['Voucher'] = j
            elif sv == 'Debit' and 'Debit' not in col:
                col['Debit'] = j
            elif sv == 'Credit' and 'Credit' not in col:
                col['Credit'] = j
            elif sv == 'Balance' and 'Balance' not in col:
                col['Balance'] = j
        if 'Date' in col and 'Particulars' in col and 'Balance' in col:
            hdr_row = i
            break

    if hdr_row is None or 'Date' not in col:
        raise ValueError('Header row not found in IIFL ledger')

    # ── Extract date range from earlier rows ──────────────────────────────
    date_range = ''
    for i in range(hdr_row):
        for v in df_raw.iloc[i]:
            s = str(v)
            m = re.search(r'Ledger From:\s*(\S+)\s+To[:\s]+(\S+)', s)
            if m:
                date_range = f'{m.group(1)} to {m.group(2)}'
            m2 = re.search(r'(\d{2}/\d{2}/\d{2,4})\s+To[:\s]+(\d{2}/\d{2}/\d{2,4})', s)
            if m2 and not date_range:
                date_range = f'{m2.group(1)} to {m2.group(2)}'

    # ── Opening balance row (row immediately after header) ────────────────
    opening_bal = 0.0
    ob_row_idx = hdr_row + 1
    if ob_row_idx < len(df_raw):
        ob_row = list(df_raw.iloc[ob_row_idx])
        part_val = str(ob_row[col['Particulars']]).strip() if 'Particulars' in col else ''
        if 'opening' in part_val.lower():
            bal_raw = ob_row[col['Balance']] if 'Balance' in col else None
            opening_bal = _sf_balance(bal_raw)   # ← signed correctly

    # ── Parse all data rows ───────────────────────────────────────────────
    transactions = []
    closing_bal = 0.0

    for i in range(hdr_row + 1, len(df_raw)):
        row = list(df_raw.iloc[i])

        date_str = _parse_date(row[col['Date']] if 'Date' in col else None)
        if not date_str:
            continue   # opening balance row / footer rows → skip

        part_val = str(row[col['Particulars']] if 'Particulars' in col else '').strip()
        if not part_val or part_val in ('nan', 'None'):
            continue

        vouch_val  = str(row[col['Voucher']]  if 'Voucher'  in col else '').strip()
        debit_val  = _sf(row[col['Debit']]   if 'Debit'   in col else 0)
        credit_val = _sf(row[col['Credit']]  if 'Credit'  in col else 0)
        # Balance uses signed version so running balance is meaningful
        bal_raw    = row[col['Balance']] if 'Balance' in col else 0
        bal_val    = _sf_balance(bal_raw)

        closing_bal = bal_val

        transactions.append({
            'Date':        date_str,
            'Particulars': part_val,
            'Voucher':     vouch_val if vouch_val not in ('nan', 'None') else '',
            'Debit':       debit_val,
            'Credit':      credit_val,
            'Balance':     bal_val,
        })

    # ── Aggregate computations ────────────────────────────────────────────
    def _sum_by(kw, col_key):
        return sum(t[col_key] for t in transactions
                   if re.search(kw, t['Particulars'], re.IGNORECASE))

    funds_added     = _sum_by(r'DEPOSIT|TRANSFER IN|net transfer|pay in', 'Credit')
    funds_withdrawn = _sum_by(r'PAYOUT|WITHDRAWAL|TRANSFER OUT',          'Debit')
    net_invested    = funds_added - funds_withdrawn

    eq_cr  = _sum_by(r'settlement|NSECAS|BSECAS|BILL FOR NM', 'Credit')
    eq_dr  = _sum_by(r'settlement|NSECAS|BSECAS|BILL FOR NM', 'Debit')
    fno_cr = _sum_by(r'FO BILL|FNO|NSEFNO|BSEFNO',            'Credit')
    fno_dr = _sum_by(r'FO BILL|FNO|NSEFNO|BSEFNO',            'Debit')

    dp_charges  = _sum_by(r'DP Bill|CDSL DP|DP BILL',          'Debit')
    int_charges = _sum_by(r'INTEREST|DELAYED PAYMENT|DELAYED',  'Debit')
    amc_charges = _sum_by(r'AMC',                               'Debit')
    total_chg   = dp_charges + int_charges + amc_charges

    monthly_map: dict = defaultdict(lambda: {'total_credit': 0.0, 'total_debit': 0.0, 'txn_count': 0})
    for t in transactions:
        mo = t['Date'][:7]
        monthly_map[mo]['total_credit'] += t['Credit']
        monthly_map[mo]['total_debit']  += t['Debit']
        monthly_map[mo]['txn_count']    += 1
    monthly = [{'Month': k, **v} for k, v in sorted(monthly_map.items())]
    top_credit = sorted(monthly, key=lambda x: x['total_credit'], reverse=True)[:3]

    return {
        'broker':            'IIFL',
        'date_range':        date_range,
        'opening_balance':   opening_bal,    # signed: negative = Dr (owes broker)
        'closing_balance':   closing_bal,    # signed: negative = Dr
        'total_debit':       sum(t['Debit']  for t in transactions),
        'total_credit':      sum(t['Credit'] for t in transactions),
        'funds_added':       funds_added,
        'funds_withdrawn':   funds_withdrawn,
        'net_invested':      net_invested,
        'bank_sources':      {},
        'eq_debit':          eq_dr,
        'eq_credit':         eq_cr,
        'fno_debit':         fno_dr,
        'fno_credit':        fno_cr,
        'eq_net':            eq_cr - eq_dr,
        'fno_net':           fno_cr - fno_dr,
        'dp_amc':            amc_charges,
        'dp_txn':            dp_charges,
        'margin_interest':   0.0,
        'mtf_interest':      0.0,
        'net_banking':       0.0,
        'delayed_payment':   int_charges,
        'brokerage_reversal': 0.0,
        'mtf_funding':       0.0,
        'mtf_repayment':     0.0,
        'total_charges':     total_chg,
        'monthly':           monthly,
        'top_credit_months': top_credit,
        'top_txn_months':    [],
        'transactions':      transactions,
        'total_rows':        len(transactions),
    }