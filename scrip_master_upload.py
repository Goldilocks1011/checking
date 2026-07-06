"""
routers/scrip_master_upload.py
================================
Endpoints:
  POST /stock-master/upload-scrip-master           — Upload ScripMaster CSV, upsert to DB
  POST /stock-master/download-scrip-master         — Auto-download from 5paisa URL, upsert to DB
  GET  /stock-master/scrip-master-stats            — Row counts / status
  POST /stock-master/refresh-fno-from-scrip-master — Fix lot sizes for all stocks
"""
import requests as _requests
import asyncio
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal
from services.scrip_master_db import upsert_scrip_master, get_db_stats, is_db_populated
import logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["ScripMaster"])

_SCRIP_MASTER_URL = (
    "https://openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All"
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/stock-master/upload-scrip-master")
async def upload_scrip_master(file: UploadFile = File(...)):
    """
    Upload ScripMaster_all.csv manually and upsert all rows into scrip_master_cache.
    Safe to re-upload — only adds/updates rows, never deletes.
    Runs in a background thread — this file can be tens of thousands of rows.
    """
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files accepted")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    result = await asyncio.to_thread(upsert_scrip_master, file_bytes)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "status":   "success",
        "message":  f"ScripMaster uploaded: {result['total']:,} rows processed",
        "inserted": result["inserted"],
        "updated":  result["updated"],
        "errors":   result["errors"],
        "total":    result["total"],
    }


@router.post("/stock-master/download-scrip-master")
async def download_scrip_master():
    """
    Auto-download latest ScripMaster CSV from 5paisa public URL and upsert to DB.
    Downloads ~34 MB and processes it — runs entirely in a background thread
    so it never freezes the server for other users.
    """
    def _do_download_and_upsert() -> dict:
        logger.info(f"[ScripMaster] Downloading from {_SCRIP_MASTER_URL}")
        resp = _requests.get(
            _SCRIP_MASTER_URL,
            timeout=120,
            stream=False,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept":     "text/csv,*/*",
            },
        )
        resp.raise_for_status()

        file_bytes = resp.content
        if not file_bytes or len(file_bytes) < 1000:
            raise ValueError(f"Downloaded file too small ({len(file_bytes)} bytes) — may be an error page.")

        logger.info(f"[ScripMaster] Downloaded {len(file_bytes):,} bytes — processing...")
        result = upsert_scrip_master(file_bytes)
        result["_download_size"] = f"{len(file_bytes) / 1024 / 1024:.1f} MB"
        return result

    try:
        result = await asyncio.to_thread(_do_download_and_upsert)
    except _requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Download timed out — 5paisa server too slow. Try manual upload.")
    except _requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Download failed: {e}")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "status":        "success",
        "source":        "auto_download",
        "download_size": result.get("_download_size", "?"),
        "message":       f"ScripMaster downloaded & saved: {result['total']:,} rows processed",
        "inserted":      result["inserted"],
        "updated":       result["updated"],
        "errors":        result["errors"],
        "total":         result["total"],
    }


@router.get("/stock-master/scrip-master-stats")
def scrip_master_stats():
    """Return scrip_master_cache table stats — use to verify upload worked."""
    populated = is_db_populated()
    if not populated:
        return {
            "populated": False,
            "message":   "ScripMaster DB is empty. Upload or download ScripMaster first.",
        }
    stats = get_db_stats()
    return {"populated": True, **stats}


@router.post("/stock-master/refresh-fno-from-scrip-master")
async def refresh_fno_from_scrip_master(db: Session = Depends(get_db)):
    """
    After uploading a new ScripMaster, re-resolve F&O info for all stocks
    in stock_master_mapping. Loops over every mapped stock, so it runs
    in a background thread.
    """
    from services.scrip_master_db import is_db_populated, query_fno_info
    if not is_db_populated():
        raise HTTPException(status_code=400, detail="ScripMaster DB not populated yet")

    from sqlalchemy import text

    def _do_refresh() -> int:
        rows = db.execute(
            text("SELECT isin, canonical_symbol FROM stock_master_mapping")
        ).fetchall()

        refreshed = 0
        for r in rows:
            can = str(r.canonical_symbol or "").strip().upper()
            if not can:
                continue
            fno, lot = query_fno_info(can)
            if fno and lot > 1:
                db.execute(
                    text("""UPDATE stock_master_mapping
                            SET fno_available=1, lot_size=:lot, updated_at=NOW()
                            WHERE isin=:isin"""),
                    {"lot": lot, "isin": r.isin}
                )
                refreshed += 1

        db.commit()
        return refreshed

    refreshed = await asyncio.to_thread(_do_refresh)
    return {"status": "success", "refreshed": refreshed}

@router.post("/stock-master/rebuild-all")
async def rebuild_all_stock_master(db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM stock_master_mapping"))
    db.execute(text("DELETE FROM unmatched_symbols"))
    db.commit()

    def _rebuild_all():
        from database import SessionLocal
        new_db = SessionLocal()          # ← fresh session for the thread
        try:
            users = new_db.query(User).all()
            for u in users:
                auto_populate(u.id)
            return len(users)
        finally:
            new_db.close()

    count = await asyncio.to_thread(_rebuild_all)
    return {"status": "rebuilt", "users_processed": count}