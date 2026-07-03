from sqlalchemy import Boolean, DateTime, ForeignKey, Column, Integer, String, Float, DateTime, func, Text, UniqueConstraint
from database import Base
from sqlalchemy import LargeBinary, Index, UniqueConstraint


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    broker = Column(String(20), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    referral_id = Column(Integer, nullable=True)
class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "exchange", "trade_date", "quantity", "price", "trade_type"),
        Index("idx_trans_user_seg_date", "user_id", "segment", "trade_date", "id"),          # <-- add
        Index("idx_trans_user_seg_symbol_broker", "user_id", "segment", "symbol", "broker"), # <-- add
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(100), nullable=False)
    company_name = Column(String(200))
    exchange = Column(String(10))
    isin = Column(String(20))
    segment = Column(String(5), default="EQ")
    trade_date = Column(String(10), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    trade_type = Column(String(20), nullable=False)
    brokerage = Column(Float, default=0)
    tax_charges = Column(Float, default=0)
    broker = Column(String(20))
    source_file = Column(String(200))
    remarks = Column(Text)

class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (
    Index("idx_holdings_user_seg_symbol", "user_id", "segment", "symbol"),
    Index("idx_holdings_user_qty", "user_id", "quantity"),
)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(100), nullable=False)
    company_name = Column(String(200))
    exchange = Column(String(10))
    isin = Column(String(20))
    segment = Column(String(5), default="EQ")
    quantity = Column(Float, nullable=False)
    avg_buy_price = Column(Float, nullable=False)
    total_invested = Column(Float, nullable=False)
    first_buy_date = Column(String(10))
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ProcessedFile(Base):
    __tablename__ = "processed_files"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    filename = Column(String(200), nullable=False)
    records_added = Column(Integer, default=0)
    processed_at = Column(DateTime, server_default=func.now())
    file_content = Column(LargeBinary, nullable=True)
    file_type = Column(String(10), nullable=False, default='EQ')
    __table_args__ = (
        UniqueConstraint("user_id", "filename", "file_type", name="uq_user_filename_filetype"),
    )

class Pnl(Base):
    __tablename__ = "pnl"
    __table_args__ = (
    Index("idx_pnl_user", "user_id"),
)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(100), nullable=False)
    company_name = Column(String(200))
    isin = Column(String(20))
    exchange = Column(String(10))
    segment = Column(String(5), default="EQ")
    buy_date = Column(String(10))
    sell_date = Column(String(10), nullable=False)
    buy_price = Column(Float)
    sell_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    holding_days = Column(Integer)
    term_type = Column(String(10))
    gross_pnl = Column(Float)
    tax_rate = Column(Float)
    tax_amount = Column(Float)
    net_pnl = Column(Float)
    broker = Column(String(20))

class Intraday(Base):
    __tablename__ = "intraday"
    __table_args__ = (
    Index("idx_intraday_user", "user_id"),
)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(100), nullable=False)
    company_name = Column(String(200))
    exchange = Column(String(10))
    segment = Column(String(5), default="EQ")
    trade_date = Column(String(10), nullable=False)
    buy_price = Column(Float, nullable=False)
    sell_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    gross_pnl = Column(Float)
    broker = Column(String(20))
    
class FnoTransaction(Base):
    __tablename__ = "fno_transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "trade_date", "trade_type", "quantity", "price"),
        Index("idx_fno_txn_user_underlying", "user_id", "underlying"),
        Index("idx_fno_txn_user_expiry", "user_id", "expiry_date"),
        Index("idx_fno_txn_user_instrument", "user_id", "instrument_type"),
)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(200), nullable=False)
    underlying = Column(String(100), nullable=False)
    exchange = Column(String(10), default="NSE")
    instrument_type = Column(String(5), nullable=False)  # FUT, CE, PE
    expiry_date = Column(String(10))
    strike_price = Column(Float, default=0)
    trade_date = Column(String(10), nullable=False)
    trade_type = Column(String(5), nullable=False)      # BUY, SELL
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    brokerage = Column(Float, default=0)
    tax_charges = Column(Float, default=0)
    broker = Column(String(20))
    source_file = Column(String(200))
    remarks = Column(Text)

class FnoOpenPosition(Base):
    __tablename__ = "fno_open_positions"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "broker", "as_of_date"),
        Index("idx_fno_open_user_type", "user_id", "instrument_type"),
        Index("idx_fno_open_user_expiry", "user_id", "expiry_date"),
        Index("idx_fno_open_user_qty", "user_id", "open_qty"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(200), nullable=False)
    underlying = Column(String(100), nullable=False)
    exchange = Column(String(10), default="NSE")
    instrument_type = Column(String(5), nullable=False)
    expiry_date = Column(String(10))
    strike_price = Column(Float, default=0)
    open_qty = Column(Float, nullable=False)   # negative = short
    avg_price = Column(Float, default=0)
    closing_price = Column(Float, default=0)
    unrealized_pnl = Column(Float, default=0)
    as_of_date = Column(String(10))
    trade_date = Column(String(10))
    broker = Column(String(20))
    source_file = Column(String(200))

class FnoPnl(Base):
    __tablename__ = "fno_pnl"
    __table_args__ = (
    Index("idx_fno_pnl_user", "user_id"),
)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(200), nullable=False)
    underlying = Column(String(100), nullable=False)
    exchange = Column(String(10), default="NSE")
    instrument_type = Column(String(5), nullable=False)
    expiry_date = Column(String(10))
    strike_price = Column(Float, default=0)
    buy_date = Column(String(10))
    sell_date = Column(String(10), nullable=False)
    buy_price = Column(Float, default=0)
    sell_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    gross_pnl = Column(Float)
    broker = Column(String(20))  
    
class StockMasterMapping(Base):
    __tablename__ = "stock_master_mapping"
    __table_args__ = (
    Index("idx_smm_canonical", "canonical_symbol"),
    # Optional: add index on fno_available and lot_size if used in WHERE
    Index("idx_smm_fno_lot", "fno_available", "lot_size"),
)
    isin = Column(String(20), primary_key=True)
    standard_name = Column(String(200), nullable=False)
    user_custom_name = Column(String(200))
    canonical_symbol = Column(String(100))
    fno_available = Column(Integer, default=0)
    lot_size = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class UnmatchedSymbol(Base):
    __tablename__ = "unmatched_symbols"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    broker = Column(String(20), nullable=False)
    raw_symbol = Column(String(200), nullable=False)
    company_name = Column(String(200))
    qty = Column(Float, default=0)
    resolved = Column(Integer, default=0)
    resolved_isin = Column(String(20))
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("user_id", "broker", "raw_symbol"),) 
    
    
class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
    Index("idx_ledger_user", "user_id"),
)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    date = Column(String(10))
    segment = Column(String(20))
    particular = Column(String(200))
    description = Column(Text)
    debit = Column(Float, default=0)
    credit = Column(Float, default=0)
    balance = Column(Float, default=0)
    source_file = Column(String(200))
class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (
    Index("idx_ca_user_type_date", "user_id", "action_type", "ex_date", "id"),
)
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(100), nullable=False)
    isin = Column(String(20))
    company_name = Column(String(200))
    action_type = Column(String(20), nullable=False)  # BONUS, SPLIT, DEMERGER, MERGER, DIVIDEND, TRANSFER
    ex_date = Column(String(10))
    record_date = Column(String(10))
    action_details = Column(Text)   # JSON string
    source = Column(String(20), default="transaction_file")
    is_verified = Column(Integer, default=0)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("user_id", "symbol", "action_type", "ex_date"),)
    
    
class Group(Base):
    __tablename__ = "user_groups"          # NOT "groups"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    
class GroupMember(Base):
    __tablename__ = "group_members"
    group_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, primary_key=True)
    broker_role = Column(String(20), default="")
    
class SymbolNormalisation(Base):
    __tablename__ = "symbol_normalisation"
    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_symbol = Column(String(200), nullable=False)       # e.g., "BAJAJ AUTO"
    canonical_symbol = Column(String(100), nullable=False)  # e.g., "BAJAJ-AUTO"
    last_used = Column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("raw_symbol"),)
    
class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class PasswordReset(Base):
    __tablename__ = "password_resets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    token = Column(String(255), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    
class UserStockSymbolMapping(Base):
    __tablename__ = "user_stock_symbol_mapping"
    __table_args__ = (Index("idx_usm_user_symbol", "user_id", "symbol"),
                       UniqueConstraint("user_id", "isin", "broker", name="uq_user_isin_broker"),
                      )
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    isin = Column(String(20), ForeignKey("stock_master_mapping.isin"), nullable=False)
    broker = Column(String(20), nullable=False)       # "Zerodha", "5paisa", "IIFL"
    symbol = Column(String(100), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
       
    
class FOLotSize(Base):
    __tablename__ = "fo_lot_sizes"
    symbol = Column(String(100), primary_key=True)   # canonical NSE ticker
    lot_size = Column(Integer, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    
    
class ScripMasterCache(Base):
    """
    Stores every row from ScripMaster_all.csv in the DB.
    On re-upload we UPSERT (update changed values, insert new rows).
    Primary key = (ScripCode, Exch) — unique per exchange listing.
    """
    __tablename__ = "scrip_master_cache"
    __table_args__ = ( 
    UniqueConstraint("scrip_code", "exch", name="uq_scrip_code_exch"),
    Index("idx_exch_exchtype", "exch", "exch_type"),                            # <-- must exist
    Index("idx_exch_exchtype_isin", "exch", "exch_type", "isin"),              # <-- must exist
    Index("idx_exch_type_scrip_lot_symbol", "exch", "exch_type", "scrip_type", "lot_size", "symbol_root"),  # <-- must exist
)
 
    id          = Column(Integer, primary_key=True, autoincrement=True)
    scrip_code  = Column(String(20),  nullable=False, index=True)
    exch        = Column(String(5),   nullable=False)          # "N" / "B"
    exch_type   = Column(String(5),   nullable=False)          # "C" / "D"
    name        = Column(String(200), nullable=True)
    symbol_root = Column(String(100), nullable=True, index=True)
    full_name   = Column(String(300), nullable=True)
    scrip_data  = Column(String(200), nullable=True)
    isin        = Column(String(20),  nullable=True,  index=True)
    series      = Column(String(10),  nullable=True)
    scrip_type  = Column(String(10),  nullable=True)           # "XX" / "CE" / "PE" / "" for EQ
    strike_rate = Column(Float,       default=0.0)
    expiry      = Column(String(30),  nullable=True)
    lot_size    = Column(Integer,     default=0)
    tick_size   = Column(Float,       default=0.0)
    qty_limit   = Column(Integer,     default=0)
    multiplier  = Column(Float,       default=1.0)
    fno_flag    = Column(Boolean,     default=False)           # True if this row is a derivative
    updated_at  = Column(DateTime,    server_default=func.now(), onupdate=func.now())
 

# ─────────────────────────────────────────────────────────────────────────────
# Dividend-forced F&O adjustment records
# ─────────────────────────────────────────────────────────────────────────────
class FnoDividendAdjustment(Base):
    __tablename__ = "fno_dividend_adjustments"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "underlying", "instrument_type", "old_strike", "ex_date",
            name="uq_fno_div_adj",
        ),
        Index("idx_fno_div_adj_user_status", "user_id", "status"),
        Index("idx_fno_div_adj_user_ex",     "user_id", "ex_date"),
    )

    id               = Column(Integer,  primary_key=True, autoincrement=True)
    user_id          = Column(Integer,  nullable=False)
    underlying       = Column(String(100), nullable=False)
    instrument_type  = Column(String(5),   nullable=False)   # CE / PE / FUT
    old_strike       = Column(Float,   default=0.0)
    new_strike       = Column(Float,   default=0.0)
    old_qty          = Column(Float,   default=0.0)
    new_qty          = Column(Float,   default=0.0)
    ex_date          = Column(String(10), nullable=False)     # YYYY-MM-DD
    expiry_date      = Column(String(10), nullable=True)      # contract expiry
    dividend_amount  = Column(Float,   default=0.0)
    spot_prev        = Column(Float,   default=0.0)           # S_prev used
    status           = Column(String(20), default="PENDING")
    # PENDING | APPLIED | SKIPPED | USER_UPLOADED
    scenario         = Column(String(2),  default="A")        # A=future, B=past
    applied_at       = Column(DateTime, nullable=True)
    notes            = Column(Text,     nullable=True)
    created_at       = Column(DateTime, server_default=func.now())


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic (bookkeeping) transactions generated by the adjustment engine
# ─────────────────────────────────────────────────────────────────────────────
class FnoSyntheticTransaction(Base):
    __tablename__ = "fno_synthetic_transactions"
    __table_args__ = (
        Index("idx_fno_syn_user_und",    "user_id", "underlying"),
        Index("idx_fno_syn_user_adj",    "user_id", "adjustment_id"),
        Index("idx_fno_syn_user_expiry", "user_id", "expiry_date"),
    )

    id               = Column(Integer,  primary_key=True, autoincrement=True)
    user_id          = Column(Integer,  nullable=False)
    adjustment_id    = Column(Integer,  ForeignKey("fno_dividend_adjustments.id"),
                              nullable=True)
    underlying       = Column(String(100), nullable=False)
    instrument_type  = Column(String(5),   nullable=False)
    expiry_date      = Column(String(10),  nullable=True)
    strike_price     = Column(Float,   default=0.0)
    trade_type       = Column(String(5),   nullable=False)    # BUY / SELL
    quantity         = Column(Float,   nullable=False)
    price            = Column(Float,   default=0.0)           # carry_avg price
    trade_date       = Column(String(10),  nullable=False)    # ex_date of dividend
    source           = Column(String(50),  default="SYNTHETIC_ADJUSTMENT")
    notes            = Column(Text,    nullable=True)
    created_at       = Column(DateTime, server_default=func.now())