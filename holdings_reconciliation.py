"""
Holdings Reconciliation Service
================================
Compares broker-provided holdings with transaction-derived holdings.
Detects mismatches and provides correction suggestions.

Key functions:
  - compare_holdings() — find matched, extra, and missing holdings
  - apply_corrections() — insert synthetic transactions/corporate actions
"""
from sqlalchemy import text
from backend.database import SessionLocal
import pandas as pd
import logging
from datetime import datetime
from backend.services.symbol_resolver import get_canonical
logger = logging.getLogger(__name__)


def _resolve_key(symbol: str, isin: str, db) -> str:
    """
    Resolve a unique key for matching holdings.
    Priority: 
      1. If ISIN exists, map ISIN -> canonical_symbol via stock_master_mapping.
      2. Else, resolve raw symbol using symbol_resolver (handles suffix stripping).
    """
    isin_clean = str(isin or "").strip().upper()
    
    # 1. If we have an ISIN, try to find the canonical symbol from the master mapping
    if isin_clean and isin_clean != "NAN":
        try:
            row = db.execute(
                text("""
                    SELECT canonical_symbol FROM stock_master_mapping
                    WHERE isin = :isin LIMIT 1
                """),
                {"isin": isin_clean},
            ).first()
            if row and row.canonical_symbol:
                return f"SYM:{row.canonical_symbol}"
        except Exception:
            pass

    # 2. If no ISIN or mapping failed, use symbol_resolver to strip suffixes 
    #    (e.g., -EQTY, _EQ, -RE, LTD, etc.) and get the canonical
    symbol_clean = str(symbol or "").strip().upper()
    canonical = get_canonical(symbol_clean)
    return f"SYM:{canonical}"

def compare_holdings(user_id: int, broker_holdings: list[dict]) -> dict:
    """
    Compare broker holdings with transaction-derived holdings.

    Args:
        user_id: Portfolio user ID
        broker_holdings: List of dicts from parsed holding file:
            [{"symbol", "isin", "quantity", "avg_cost", "market_price", "market_value"}, ...]

    Returns:
        {
            "matched": [
                {"symbol", "isin", "key", "broker_qty", "your_qty", "difference", "status"}
            ],
            "extra": [
                {"symbol", "isin", "key", "broker_qty", "your_qty", "difference", "avg_cost"}
            ],
            "missing": [
                {"symbol", "isin", "key", "broker_qty", "your_qty", "difference"}
            ],
            "comparison_date": "YYYY-MM-DD",
        }
    """
    db = SessionLocal()
    try:
        # Load transaction-derived holdings
        txn_holdings = db.execute(
            text("""
                SELECT symbol, isin, quantity, avg_buy_price, total_invested
                FROM holdings
                WHERE user_id = :uid AND segment = 'EQ' AND quantity > 0
            """),
            {"uid": user_id},
        ).fetchall()

        txn_dict = {}
        for row in txn_holdings:
            key = _resolve_key(row.symbol, row.isin, db)
            txn_dict[key] = {
                "symbol": row.symbol,
                "isin": row.isin,
                "quantity": float(row.quantity),
                "avg_cost": float(row.avg_buy_price),
                "total_invested": float(row.total_invested),
            }

        # Build broker holdings dict with same keys
        broker_dict = {}
        for bh in broker_holdings:
            key = _resolve_key(bh["symbol"], bh.get("isin", ""), db)
            if key in broker_dict:
                # 🚨 CRITICAL: Sum quantities if the same key appears (e.g., Pledge + Beneficiary)
                broker_dict[key]["quantity"] += float(bh["quantity"])
                # Optional: Recalculate avg cost if needed (simple average here, or keep existing)
                existing_avg = broker_dict[key]["avg_cost"]
                new_avg = float(bh.get("avg_cost", 0))
                broker_dict[key]["avg_cost"] = (existing_avg + new_avg) / 2
            else:
                broker_dict[key] = {
                    "symbol": bh["symbol"],
                    "isin": bh.get("isin", ""),
                    "quantity": float(bh["quantity"]),
                    "avg_cost": float(bh.get("avg_cost", 0)),
                    "market_price": float(bh.get("market_price", 0)),
                    "market_value": float(bh.get("market_value", 0)),
                }

        # Compare
        matched = []
        extra = []
        missing = []
        qty_tolerance = 2.0  # Allow ±2 shares for fractional matches

        # Find matched and missing
        for key, txn_data in txn_dict.items():
            if key in broker_dict:
                broker_data = broker_dict[key]
                qty_diff = abs(txn_data["quantity"] - broker_data["quantity"])
                if qty_diff <= qty_tolerance:
                    matched.append({
                        "symbol": txn_data["symbol"],
                        "isin": txn_data["isin"],
                        "key": key,
                        "broker_qty": broker_data["quantity"],
                        "your_qty": txn_data["quantity"],
                        "difference": 0,
                        "status": "MATCHED",
                    })
                else:
                    # Quantity mismatch — if broker has more, it's "extra" in broker's perspective
                    if broker_data["quantity"] > txn_data["quantity"]:
                        extra.append({
                            "symbol": broker_data["symbol"],
                            "isin": broker_data["isin"],
                            "key": key,
                            "broker_qty": broker_data["quantity"],
                            "your_qty": txn_data["quantity"],
                            "difference": broker_data["quantity"] - txn_data["quantity"],
                            "avg_cost": broker_data["avg_cost"],
                            "market_price": broker_data["market_price"],
                            "source": "",  # User to fill
                        })
                    else:
                        # You have more, broker has less — you have "extra"
                        missing.append({
                            "symbol": txn_data["symbol"],
                            "isin": txn_data["isin"],
                            "key": key,
                            "broker_qty": broker_data["quantity"],
                            "your_qty": txn_data["quantity"],
                            "difference": txn_data["quantity"] - broker_data["quantity"],
                        })
            else:
                # Completely missing from broker
                missing.append({
                    "symbol": txn_data["symbol"],
                    "isin": txn_data["isin"],
                    "key": key,
                    "broker_qty": 0.0,
                    "your_qty": txn_data["quantity"],
                    "difference": txn_data["quantity"],
                })

        # Find extra (in broker, not in txn_dict)
        for key, broker_data in broker_dict.items():
            if key not in txn_dict:
                extra.append({
                    "symbol": broker_data["symbol"],
                    "isin": broker_data["isin"],
                    "key": key,
                    "broker_qty": broker_data["quantity"],
                    "your_qty": 0.0,
                    "difference": broker_data["quantity"],
                    "avg_cost": broker_data["avg_cost"],
                    "market_price": broker_data["market_price"],
                    "source": "",  # User to fill
                })

        return {
            "matched": matched,
            "extra": extra,
            "missing": missing,
            "comparison_date": datetime.now().strftime("%Y-%m-%d"),
            "summary": {
                "total_matched": len(matched),
                "total_extra": len(extra),
                "total_missing": len(missing),
            },
        }

    finally:
        db.close()


def apply_corrections(user_id: int, corrections: list[dict]) -> dict:
    """
    Apply user-confirmed corrections to holdings.

    Args:
        user_id: Portfolio user ID
        corrections: List of dicts from UI:
            [
                {
                    "symbol": "TCS",
                    "isin": "INE467B01014",
                    "quantity": 10,
                    "source": "IPO|BONUS|SPLIT|MERGER|DEMERGER|TRANSFER|MANUAL_BUY|SELL|IGNORE",
                    "price": 2000 (optional, for IPO/MANUAL_BUY),
                },
                ...
            ]

    Returns:
        {
            "status": "success/error",
            "message": "...",
            "actions_taken": [
                {"symbol", "source", "qty", "action_type", "result"}
            ],
            "errors": [...]
        }
    """
    db = SessionLocal()
    actions = []
    errors = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        for corr in corrections:
            symbol = corr.get("symbol", "").strip().upper()
            isin = corr.get("isin", "").strip().upper()
            qty = float(corr.get("quantity", 0))
            source = corr.get("source", "").strip().upper()
            price = float(corr.get("price", 0))

            if qty <= 0 or not symbol or not source:
                errors.append(f"Invalid correction for {symbol}: qty={qty}, source={source}")
                continue

            try:
                if source == "IPO":
                    # Insert a BUY transaction
                    if price <= 0:
                        price = 1000  # Default IPO price if not provided
                    db.execute(
                        text("""
                            INSERT INTO transactions
                            (user_id, symbol, company_name, exchange, isin, segment,
                             trade_date, quantity, price, trade_type,
                             brokerage, tax_charges, broker, source_file, remarks)
                            VALUES (:uid, :sym, :comp, :exch, :isin, :seg,
                                    :tdate, :qty, :price, :tt,
                                    0, 0, 'Manual', :src, :rem)
                        """),
                        {
                            "uid": user_id,
                            "sym": symbol,
                            "comp": symbol,
                            "exch": "NSE",
                            "isin": isin,
                            "seg": "EQ",
                            "tdate": today,
                            "qty": qty,
                            "price": price,
                            "tt": "BUY",
                            "src": "IPO_Reconciliation",
                            "rem": f"IPO allocation from reconciliation",
                        },
                    )
                    db.commit()
                    actions.append({
                        "symbol": symbol,
                        "source": "IPO",
                        "qty": qty,
                        "action_type": "INSERT_TRANSACTION_BUY",
                        "result": "CREATED",
                    })

                elif source in ("BONUS", "SPLIT"):
                    # Insert corporate action
                    ratio = corr.get("ratio", "1:1")
                    db.execute(
                        text("""
                            INSERT INTO corporate_actions
                            (user_id, symbol, isin, company_name, action_type,
                             ex_date, action_details, source, notes)
                            VALUES (:uid, :sym, :isin, :comp, :type,
                                    :exdate, :details, :src, :notes)
                            ON DUPLICATE KEY UPDATE action_details = :details
                        """),
                        {
                            "uid": user_id,
                            "sym": symbol,
                            "isin": isin,
                            "comp": symbol,
                            "type": source,
                            "exdate": today,
                            "details": f'{{"ratio": "{ratio}"}}',
                            "src": "Reconciliation",
                            "notes": f"{source} detected from reconciliation",
                        },
                    )
                    db.commit()
                    actions.append({
                        "symbol": symbol,
                        "source": source,
                        "qty": qty,
                        "action_type": "INSERT_CORPORATE_ACTION",
                        "result": "CREATED",
                    })

                elif source == "TRANSFER":
                    # Insert TRANSFER_IN transaction
                    db.execute(
                        text("""
                            INSERT INTO transactions
                            (user_id, symbol, company_name, exchange, isin, segment,
                             trade_date, quantity, price, trade_type,
                             brokerage, tax_charges, broker, source_file, remarks)
                            VALUES (:uid, :sym, :comp, :exch, :isin, :seg,
                                    :tdate, :qty, :price, :tt,
                                    0, 0, 'Manual', :src, :rem)
                        """),
                        {
                            "uid": user_id,
                            "sym": symbol,
                            "comp": symbol,
                            "exch": "NSE",
                            "isin": isin,
                            "seg": "EQ",
                            "tdate": today,
                            "qty": qty,
                            "price": 0.0,
                            "tt": "TRANSFER_IN",
                            "src": "Transfer_Reconciliation",
                            "rem": f"Transfer in detected from reconciliation",
                        },
                    )
                    db.commit()
                    actions.append({
                        "symbol": symbol,
                        "source": "TRANSFER",
                        "qty": qty,
                        "action_type": "INSERT_TRANSFER_IN",
                        "result": "CREATED",
                    })

                elif source == "MANUAL_BUY":
                    # Insert a manual BUY transaction at broker's avg cost
                    if price <= 0:
                        price = 100  # Default if not provided
                    db.execute(
                        text("""
                            INSERT INTO transactions
                            (user_id, symbol, company_name, exchange, isin, segment,
                             trade_date, quantity, price, trade_type,
                             brokerage, tax_charges, broker, source_file, remarks)
                            VALUES (:uid, :sym, :comp, :exch, :isin, :seg,
                                    :tdate, :qty, :price, :tt,
                                    0, 0, 'Manual', :src, :rem)
                        """),
                        {
                            "uid": user_id,
                            "sym": symbol,
                            "comp": symbol,
                            "exch": "NSE",
                            "isin": isin,
                            "seg": "EQ",
                            "tdate": today,
                            "qty": qty,
                            "price": price,
                            "tt": "BUY",
                            "src": "Manual_Reconciliation",
                            "rem": f"Manual buy from reconciliation",
                        },
                    )
                    db.commit()
                    actions.append({
                        "symbol": symbol,
                        "source": "MANUAL_BUY",
                        "qty": qty,
                        "action_type": "INSERT_TRANSACTION_BUY",
                        "result": "CREATED",
                    })

                elif source == "SELL":
                    # User confirms they sold unrecorded — insert SELL transaction
                    if price <= 0:
                        price = 100  # Default sell price if not provided
                    db.execute(
                        text("""
                            INSERT INTO transactions
                            (user_id, symbol, company_name, exchange, isin, segment,
                             trade_date, quantity, price, trade_type,
                             brokerage, tax_charges, broker, source_file, remarks)
                            VALUES (:uid, :sym, :comp, :exch, :isin, :seg,
                                    :tdate, :qty, :price, :tt,
                                    0, 0, 'Manual', :src, :rem)
                        """),
                        {
                            "uid": user_id,
                            "sym": symbol,
                            "comp": symbol,
                            "exch": "NSE",
                            "isin": isin,
                            "seg": "EQ",
                            "tdate": today,
                            "qty": qty,
                            "price": price,
                            "tt": "SELL",
                            "src": "Sell_Reconciliation",
                            "rem": f"Unrecorded sell from reconciliation",
                        },
                    )
                    db.commit()
                    actions.append({
                        "symbol": symbol,
                        "source": "SELL",
                        "qty": qty,
                        "action_type": "INSERT_TRANSACTION_SELL",
                        "result": "CREATED",
                    })

                elif source == "RIGHTS":
                    # Rights Entitlements lapse/expire. Removing them from holdings via a transfer out.
                    db.execute(
                        text("""
                            INSERT INTO transactions
                            (user_id, symbol, company_name, exchange, isin, segment,
                             trade_date, quantity, price, trade_type,
                             brokerage, tax_charges, broker, source_file, remarks)
                            VALUES (:uid, :sym, :comp, :exch, :isin, :seg,
                                    :tdate, :qty, :price, :tt,
                                    0, 0, 'Manual', :src, :rem)
                        """),
                        {
                            "uid": user_id,
                            "sym": symbol,
                            "comp": symbol,
                            "exch": "NSE",
                            "isin": isin,
                            "seg": "EQ",
                            "tdate": today,
                            "qty": qty,
                            "price": 0.0,
                            "tt": "TRANSFER_OUT",
                            "src": "Rights_Reconciliation",
                            "rem": f"Rights Entitlement (-RE) removed from reconciliation",
                        },
                    )
                    db.commit()
                    actions.append({
                        "symbol": symbol,
                        "source": "RIGHTS",
                        "qty": qty,
                        "action_type": "INSERT_TRANSFER_OUT",
                        "result": "REMOVED",
                    })
                        
                elif source == "IGNORE":
                    # User ignores the discrepancy
                    actions.append({
                        "symbol": symbol,
                        "source": "IGNORE",
                        "qty": qty,
                        "action_type": "NO_ACTION",
                        "result": "IGNORED",
                    })

            except Exception as e:
                db.rollback()
                errors.append(f"Error processing {symbol} ({source}): {e}")
                logger.error(f"Holdings reconciliation error: {e}", exc_info=True)

        # After all corrections, rebuild FIFO holdings/P&L
        if actions and not errors:
            from backend.services.engine import recalculate_derived

            recalculate_derived(user_id, db)
            logger.info(
                f"[Reconciliation] User {user_id}: {len(actions)} actions applied, holdings rebuilt"
            )

        return {
            "status": "success" if not errors else "partial",
            "message": f"Applied {len(actions)} correction(s)" + (f"; {len(errors)} error(s)" if errors else ""),
            "actions_taken": actions,
            "errors": errors,
        }

    except Exception as e:
        db.rollback()
        logger.error(f"[Reconciliation] Fatal error for user {user_id}: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Reconciliation failed: {e}",
            "actions_taken": actions,
            "errors": [str(e)],
        }
    finally:
        db.close()