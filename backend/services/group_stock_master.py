# backend/services/group_stock_master.py

from collections import defaultdict
from sqlalchemy import text
from database import SessionLocal

def build_group_stock_grid(group_id: int):
    db = SessionLocal()
    try:
        # 1. Fetch members
        members = db.execute(
            text("SELECT u.id, u.username, u.broker FROM group_members gm JOIN users u ON u.id = gm.user_id WHERE gm.group_id = :gid"),
            {"gid": group_id}
        ).fetchall()
        if not members:
            return []

        # Build column labels
        broker_count = defaultdict(int)
        for m in members:
            broker_count[m.broker.lower()] += 1

        def col_label(uname, broker):
            if broker_count[broker.lower()] > 1:
                return f"{uname} ({broker[:3].upper()})"
            return uname

        uid_label = {m.id: col_label(m.username, m.broker) for m in members}

        # 2. Fetch all distinct symbols from user_stock_symbol_mapping for all members
        #    This gives us every ISIN+broker combination ever traded.
        rows = db.execute(
            text("""
                SELECT usm.isin, usm.user_id, usm.symbol, h.quantity AS current_qty
                FROM user_stock_symbol_mapping usm
                JOIN users u ON u.id = usm.user_id
                JOIN group_members gm ON gm.user_id = u.id
                LEFT JOIN holdings h ON h.user_id = usm.user_id AND h.symbol = usm.symbol
                WHERE gm.group_id = :gid
                ORDER BY usm.isin, usm.user_id
            """),
            {"gid": group_id}
        ).fetchall()

        # 3. Aggregate: per ISIN, per user, collect symbol and current quantity (default 0)
        isin_data = defaultdict(lambda: defaultdict(dict))
        master_info = {}
        for r in rows:
            isin = r.isin
            uid = r.user_id
            sym = r.symbol
            qty = float(r.current_qty) if r.current_qty else 0.0
            # If multiple rows for same user+ISIN (e.g., two broker symbols), we keep the first symbol and sum quantity
            if uid not in isin_data[isin]:
                isin_data[isin][uid] = {"symbol": sym, "qty": qty}
            else:
                isin_data[isin][uid]["qty"] += qty

            if isin not in master_info:
                master_row = db.execute(
                    text("SELECT standard_name, fno_available, lot_size, canonical_symbol FROM stock_master_mapping WHERE isin=:isin"),
                    {"isin": isin}
                ).first()
                if master_row:
                    master_info[isin] = {
                        "standard_name": master_row.standard_name or "",
                        "fno_available": bool(master_row.fno_available),
                        "lot_size": int(master_row.lot_size or 0),
                        "canonical": master_row.canonical_symbol or "",
                    }
                else:
                    master_info[isin] = {
                        "standard_name": isin,  # fallback, but shouldn't happen
                        "fno_available": False,
                        "lot_size": 0,
                        "canonical": "",
                    }

        # 4. Build grid
        grid = []
        for isin, user_map in isin_data.items():
            mi = master_info.get(isin, {})
            total_qty = sum(info["qty"] for info in user_map.values())
            fno = mi.get("fno_available", False)
            lot = mi.get("lot_size", 0)
            pending = 0
            if fno and lot > 0 and total_qty > 0:
                pending = (lot - total_qty % lot) % lot

            row = {
                "ISIN": isin,
                "Name": mi.get("standard_name", ""),
                "Canonical": mi.get("canonical", ""),
                "F&O": "✅ Yes" if fno else "—",
                "Lot Size": lot if fno else "—",
                "Total Qty": round(total_qty, 2),
                "Pending Qty": pending,
            }
            # Per‑member columns
            for uid, info in user_map.items():
                label = uid_label.get(uid, f"user{uid}")
                row[f"{label}_symbol"] = info["symbol"]
                row[f"{label}_qty"] = round(info["qty"], 2)

            grid.append(row)

        return grid
    finally:
        db.close()