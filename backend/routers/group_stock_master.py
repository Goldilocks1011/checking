from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import SessionLocal
from services.group_stock_master import build_group_stock_grid

router = APIRouter(tags=["Group Stock Master"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/groups/{group_id}/stock-master")
def group_stock_master(group_id: int, db: Session = Depends(get_db)):
    return build_group_stock_grid(group_id)