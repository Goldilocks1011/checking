"""
account_router.py
=================
Determines account role and master reference for a given user.
"""
from sqlalchemy import text
from database import SessionLocal


def get_account_role_and_master(user_id: int) -> tuple[str, int | None]:
    """
    Returns ('master', None) or ('child', master_user_id).
    A user is 'master' if they have no referral_id set.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT referral_id FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()
        if not row or not row.referral_id:
            return "master", None
        return "child", int(row.referral_id)
    except Exception:
        return "master", None
    finally:
        db.close()


def get_master_open_ce_symbols(master_user_id: int) -> set[str]:
    """
    Returns set of underlying symbols where the master has an active SHORT CE.
    Child accounts must skip these symbols (master already covered).
    """
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT DISTINCT UPPER(underlying) AS underlying
                FROM fno_open_positions
                WHERE user_id         = :uid
                  AND instrument_type = 'CE'
                  AND open_qty        < 0          -- negative = sold/short
                  AND ABS(open_qty)   > 0.001
            """),
            {"uid": master_user_id}
        ).fetchall()
        return {r.underlying for r in rows}
    except Exception:
        return set()
    finally:
        db.close()