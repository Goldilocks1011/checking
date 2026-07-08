
from sqlalchemy import text
from backend.database import SessionLocal

def create_group(name: str, account_id: int) -> dict:
    db = SessionLocal()
    try:
        result = db.execute(
            text("INSERT INTO user_groups (name, account_id) VALUES (:name, :aid)"),
            {"name": name, "aid": account_id}
        )
        db.commit()
        new_id = result.lastrowid
        return {"id": new_id, "name": name}
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

def list_groups(account_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT * FROM user_groups WHERE account_id = :aid ORDER BY name"),
            {"aid": account_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()

def add_member(group_id: int, user_id: int, account_id: int) -> dict:
    db = SessionLocal()
    try:
        # First verify the group belongs to the account
        group = db.execute(
            text("SELECT id FROM user_groups WHERE id = :gid AND account_id = :aid"),
            {"gid": group_id, "aid": account_id}
        ).first()
        if not group:
            raise PermissionError("Group not found or access denied")
        # Also verify the user belongs to the same account
        user = db.execute(
            text("SELECT id FROM users WHERE id = :uid AND account_id = :aid"),
            {"uid": user_id, "aid": account_id}
        ).first()
        if not user:
            raise PermissionError("User not found or access denied")
        db.execute(
            text("INSERT IGNORE INTO group_members (group_id, user_id) VALUES (:gid, :uid)"),
            {"gid": group_id, "uid": user_id}
        )
        db.commit()
        return {"status": "added"}
    finally:
        db.close()

def remove_member(group_id: int, user_id: int, account_id: int) -> dict:
    db = SessionLocal()
    try:
        # Verify ownership
        group = db.execute(
            text("SELECT id FROM user_groups WHERE id = :gid AND account_id = :aid"),
            {"gid": group_id, "aid": account_id}
        ).first()
        if not group:
            raise PermissionError("Group not found or access denied")
        db.execute(
            text("DELETE FROM group_members WHERE group_id = :gid AND user_id = :uid"),
            {"gid": group_id, "uid": user_id}
        )
        db.commit()
        return {"status": "removed"}
    finally:
        db.close()

def get_group_members(group_id: int, account_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        # Verify ownership
        group = db.execute(
            text("SELECT id FROM user_groups WHERE id = :gid AND account_id = :aid"),
            {"gid": group_id, "aid": account_id}
        ).first()
        if not group:
            return []
        rows = db.execute(
            text("""
                SELECT u.id, u.username, u.broker
                FROM group_members gm
                JOIN users u ON u.id = gm.user_id
                WHERE gm.group_id = :gid
                ORDER BY u.username
            """),
            {"gid": group_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()

def get_group_holdings(group_id: int, account_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        # Verify ownership
        group = db.execute(
            text("SELECT id FROM user_groups WHERE id = :gid AND account_id = :aid"),
            {"gid": group_id, "aid": account_id}
        ).first()
        if not group:
            return []
        rows = db.execute(
            text("""
                SELECT h.symbol, SUM(h.quantity) as total_qty, SUM(h.total_invested) as total_invested
                FROM holdings h
                JOIN group_members gm ON gm.user_id = h.user_id
                WHERE gm.group_id = :gid
                GROUP BY h.symbol
                ORDER BY total_invested DESC
            """),
            {"gid": group_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()