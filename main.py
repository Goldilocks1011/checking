from fastapi import FastAPI
from backend.database import engine, Base
from backend.routers import (
    users,
    upload,
    holdings,
    transactions,
    pnl,
    intraday,
    fno_upload,
    fno_positions,
    fno_pnl,
    stock_master,
    tax_harvest,
    ledger,
    corp_actions,
    manual,
    groups,
    group_stock_master,
    merged,
)
from backend.models import (
    User,
    Transaction,
    Holding,
    ProcessedFile,
    Pnl,
    Intraday,
    FnoTransaction,
    FnoOpenPosition,
    FnoPnl,
    StockMasterMapping,
    UnmatchedSymbol,
    LedgerEntry,
    CorporateAction,
    Group,
    GroupMember,
    Account,
    PasswordReset,
    SymbolNormalisation,
    ScripMasterCache,
    FnoDividendAdjustment,
    FnoSyntheticTransaction,
)
from backend.routers import (
    prices,
    stats,
    fno_transactions,
    auth,
    ce_pe_screener,
    scrip_master_upload,
    wishlist_router as wishlist,
)
from backend.routers.impact_router import router as impact_router
from backend.routers import fno_adjustments, fno_stale_positions, task_status_router

# Create all tables (including new ones)
from backend.logging_config import setup_logging

setup_logging()


app = FastAPI(title="Portfolio Tracker v2")


@app.on_event("startup")
async def startup_event():
    # This runs ONLY ONCE when the app starts, not on every reload
    Base.metadata.create_all(bind=engine)
    print("Database tables ensured.")


app.include_router(users.router, prefix="/api/v1")
app.include_router(upload.router, prefix="/api/v1")
app.include_router(holdings.router, prefix="/api/v1")
app.include_router(transactions.router, prefix="/api/v1")
app.include_router(pnl.router, prefix="/api/v1")
app.include_router(intraday.router, prefix="/api/v1")
app.include_router(fno_upload.router, prefix="/api/v1")
app.include_router(fno_positions.router, prefix="/api/v1")
app.include_router(fno_pnl.router, prefix="/api/v1")
app.include_router(stock_master.router, prefix="/api/v1")
app.include_router(tax_harvest.router, prefix="/api/v1")
app.include_router(ledger.router, prefix="/api/v1")
app.include_router(corp_actions.router, prefix="/api/v1")
app.include_router(manual.router, prefix="/api/v1")
app.include_router(groups.router, prefix="/api/v1")
app.include_router(group_stock_master.router, prefix="/api/v1")
app.include_router(merged.router, prefix="/api/v1")
app.include_router(prices.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(fno_transactions.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(ce_pe_screener.router, prefix="/api/v1")
app.include_router(scrip_master_upload.router, prefix="/api/v1")  # ← NEW
app.include_router(wishlist.router, prefix="/api/v1")
app.include_router(impact_router, prefix="/api/v1")
app.include_router(fno_adjustments.router, prefix="/api/v1")
app.include_router(fno_stale_positions.router, prefix="/api/v1")
app.include_router(task_status_router.router, prefix="/api/v1")


@app.get("/")
def root():
    return {"status": "running"}
