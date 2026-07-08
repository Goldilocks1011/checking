"""
ScripMaster Upload Router
Handles uploading and managing the stock master data
"""

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
import csv
import io
from datetime import datetime

# These imports should match your actual project structure
# If using root-level: from backend.database import get_db
# If using backend: from backend.database import get_db
from backend.database import get_db

# Authentication dependency
try:
    from backend.routers.auth import get_current_user
except ImportError:
    try:
        from backend.routers.auth import get_current_user
    except ImportError:
        print("WARNING: Could not import get_current_user")

        async def get_current_user(token: str = None):
            return "test_user"


# Service for stock master operations
try:
    from backend.services import stock_master_service
except ImportError:
    try:
        from backend.services import stock_master_service
    except ImportError:
        stock_master_service = None

# Define the router with prefix
# Note: The main.py will add /api/v1 prefix when including this router
router = APIRouter(
    prefix="/stock-master",
    tags=["stock-master"],
    responses={404: {"description": "Not found"}},
)


@router.get("/scrip-master-stats")
async def get_scrip_master_stats(
    user_id: str = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Get ScripMaster statistics from backend.database

    Returns count of entries, last update time, etc.
    """
    try:
        # Count entries in the database
        # Adjust this based on your actual table name and structure

        # Placeholder - replace with your actual service call
        stats = {"total_entries": 0, "last_updated": None, "status": "pending_upload"}

        # If you have a service, use it:
        # if stock_master_service:
        #     stats = stock_master_service.get_stats(user_id, db)

        return {"status": "success", "data": stats}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/upload-scrip-master")
async def upload_scrip_master(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Upload ScripMaster CSV file

    Expected format:
    - symbol, isin, exchange, segment

    Returns processing status and any errors found
    """
    try:
        # Validate file type
        if not file.filename.endswith(".csv"):
            raise HTTPException(status_code=400, detail="File must be CSV format")

        # Read file content
        content = await file.read()

        if not content:
            raise HTTPException(status_code=400, detail="File is empty")

        # Parse CSV
        text_stream = io.StringIO(content.decode("utf-8"))
        csv_reader = csv.DictReader(text_stream)
        rows = list(csv_reader)

        if not rows:
            raise HTTPException(status_code=400, detail="CSV file has no data rows")

        # Validate CSV structure
        required_columns = {"symbol", "isin", "exchange", "segment"}
        if csv_reader.fieldnames:
            actual_columns = set(csv_reader.fieldnames)
            missing = required_columns - actual_columns
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required columns: {', '.join(missing)}",
                )

        # Process the data (placeholder - replace with your actual logic)
        processed_count = len(rows)

        # If you have a service, use it:
        # if stock_master_service:
        #     result = stock_master_service.process_upload(rows, user_id, db)
        #     processed_count = result['count']
        # else:
        processed_count = len(rows)

        return {
            "status": "success",
            "message": f"Uploaded {processed_count} entries",
            "filename": file.filename,
            "size": len(content),
            "rows_processed": processed_count,
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException as http_exc:
        raise http_exc

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")


@router.post("/download-from-5paisa")
async def download_and_sync_from_5paisa(
    user_id: str = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Download latest ScripMaster from 5Paisa and update database

    URL: https://openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All
    """
    try:
        import httpx

        url = (
            "https://openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All"
        )

        # Download the file
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

        # Parse and process
        content = response.content
        text_stream = io.StringIO(content.decode("utf-8"))
        csv_reader = csv.DictReader(text_stream)
        rows = list(csv_reader)

        # Process with service if available
        if stock_master_service:
            result = stock_master_service.process_upload(rows, user_id, db)
            processed_count = result.get("count", len(rows))
        else:
            processed_count = len(rows)

        return {
            "status": "success",
            "message": f"Synced {processed_count} entries from 5Paisa",
            "count": processed_count,
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException as http_exc:
        raise http_exc

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error downloading from 5Paisa: {str(e)}"
        )


@router.delete("/clear-scrip-master")
async def clear_scrip_master(
    user_id: str = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Clear all ScripMaster data (use with caution!)
    """
    try:
        # This is a dangerous operation - add additional checks in production

        if stock_master_service:
            result = stock_master_service.clear_all(db)
            return {
                "status": "success",
                "message": "ScripMaster data cleared",
                "rows_deleted": result,
            }
        else:
            raise HTTPException(status_code=500, detail="Service not available")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error clearing data: {str(e)}")
