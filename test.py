#!/usr/bin/env python3
"""
dummy
test_resolution.py

Tests the symbol resolution pipeline on your problematic symbols.
Run from the backend/ directory after applying the above edits.
"""

import sys
import os
import logging

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

from backend.services.stock_master_service import _resolve_isin
from backend.services.scrip_master_db import is_db_populated
from backend.database import SessionLocal


def test_resolve(symbol: str, company: str = None) -> tuple[str, str]:
    """Wrapper around _resolve_isin."""
    if company is None:
        company = symbol
    return _resolve_isin(symbol, company)


def main():
    symbols = [
        "Aditya Birla Fashion",
        "Bajaj Hldg. & Inv.",
        "Bajaj Housing Fin.",
        "BEML Land Assets",
        "BHARATIWIN",  # should be skipped as RIGHTS
        "Canara Bank",
        "Ellenbarrie Indl.",
        "Engineers India",
        "Global Health",
        "Gujarat Pipavav Port",
        "Hemisphere Propertie",
        "Housing & Urban Dev.",
        "ICICI Prudential AMC",
        "IDFCBOND12",  # should skip (bond)
        "IIFCBOND16",  # should skip (bond)
        "India Shelter Fin.",
        "Indus Towers",
        "Jio Financial Serv.",
        "Kotak Mahindra Bank",
        "Maha. Scooters",
        "Nexus REIT",
        "NMDC Steel",
        "Noida Toll Bridge",
        "Punj. NationlBak",
        "Sheela Foam",
        "Tata Motors",
        "Tata Motors Passenge",
        "The United Nilgiri",
        "Wonderla Holidays",
    ]

    if not is_db_populated():
        print(
            "❌ ScripMaster DB is empty. Please run the ScripMaster download/upload first."
        )
        sys.exit(1)

    print("\n{:35} {:20} {:20} {}".format("Symbol", "ISIN", "Canonical", "Status"))
    print("-" * 80)

    for sym in symbols:
        isin, canon = test_resolve(sym, sym)
        if isin:
            status = "✅ RESOLVED"
        else:
            status = "❌ UNRESOLVED (or skipped)"
        print(f"{sym:<35} {isin:<20} {canon:<20} {status}")


if __name__ == "__main__":
    main()


# current output:
# (venv) PS E:\API_connect\trading\v7\backend> python test.py
# 2026-07-07 09:05:06,438 - database - INFO - [DB] Database `portfolio_v2` ensured.
# 2026-07-07 09:05:06,761 - database - INFO - [Migration] Changed file_content to LONGBLOB
# 2026-07-07 09:05:06,780 - database - INFO - [Migration] Marked lowest account id as master
# 2026-07-07 09:05:06,853 - database - INFO - [Migration] fno_synthetic_transactions table ensured

# Symbol                              ISIN                 Canonical            Status
# --------------------------------------------------------------------------------
# 2026-07-07 09:05:08,374 - services.symbol_resolver - INFO - [SymbolResolver] Cache built — 15,585 entries
# 2026-07-07 09:05:09,378 - services.scrip_master_db - INFO - [ScripMasterDB] fuzzy prefix (full_name): 'ADITYA BIRLA%' → INE055A01016
# Aditya Birla Fashion                INE055A01016         ABREL                ✅ RESOLVED
# 2026-07-07 09:05:10,358 - services.scrip_master_db - INFO - [ScripMasterDB] fuzzy prefix (full_name): 'BAJAJ HOLDINGS%' → INE118A01012
# Bajaj Hldg. & Inv.                  INE118A01012         BAJAJHLDNG           ✅ RESOLVED
# 2026-07-07 09:05:11,837 - services.scrip_master_db - INFO - [ScripMasterDB] fuzzy prefix (full_name): 'BAJAJ HOUSING%' → INE377Y01014
# Bajaj Housing Fin.                  INE377Y01014         BAJAJHFL             ✅ RESOLVED
# 2026-07-07 09:05:13,352 - services.scrip_master_db - INFO - [ScripMasterDB] fuzzy prefix (full_name): 'BEML LAND%' → INE0N7W01012
# BEML Land Assets                    INE0N7W01012         BLAL                 ✅ RESOLVED
# 2026-07-07 09:05:16,677 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=BHARATIWIN — refreshing session (attempt 1)
# 2026-07-07 09:05:18,303 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=BHARATIWIN — refreshing session (attempt 2)
# 2026-07-07 09:05:20,562 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=BHARATIWIN — refreshing session (attempt 3)
# 2026-07-07 09:05:23,407 - services.nse_data_service - WARNING - [NSE] HTTP 404 for https://www.nseindia.com/api/search/autocomplete?q=BHARATIWIN
# BHARATIWIN                                                                    ❌ UNRESOLVED (or skipped)
# 2026-07-07 09:05:26,267 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=CANARA — refreshing session (attempt 1)
# 2026-07-07 09:05:27,924 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=CANARA — refreshing session (attempt 2)
# 2026-07-07 09:05:29,997 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=CANARA — refreshing session (attempt 3)
# 2026-07-07 09:05:32,689 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=CANARA BANK — refreshing session (attempt 1)
# 2026-07-07 09:05:34,447 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=CANARA BANK — refreshing session (attempt 2)
# 2026-07-07 09:05:36,580 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=CANARA BANK — refreshing session (attempt 3)
# 2026-07-07 09:05:39,419 - services.nse_data_service - WARNING - [NSE] HTTP 404 for https://www.nseindia.com/api/search/autocomplete?q=Canara Bank
# Canara Bank                                                                   ❌ UNRESOLVED (or skipped)
# 2026-07-07 09:05:40,809 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=ELLENBARRIE — refreshing session (attempt 1)
# 2026-07-07 09:05:42,296 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=ELLENBARRIE — refreshing session (attempt 2)
# 2026-07-07 09:05:44,400 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=ELLENBARRIE — refreshing session (attempt 3)
# 2026-07-07 09:05:47,112 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=ELLENBARRIE INDL. — refreshing session (attempt 1)
# 2026-07-07 09:05:48,655 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=ELLENBARRIE INDL. — refreshing session (attempt 2)
# 2026-07-07 09:05:50,822 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=ELLENBARRIE INDL. — refreshing session (attempt 3)
# 2026-07-07 09:05:53,557 - services.nse_data_service - WARNING - [NSE] HTTP 404 for https://www.nseindia.com/api/search/autocomplete?q=Ellenbarrie Indl.
# Ellenbarrie Indl.                                                             ❌ UNRESOLVED (or skipped)
# 2026-07-07 09:05:54,791 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=ENGINEERS INDIA — refreshing session (attempt 1)
# 2026-07-07 09:05:56,368 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=ENGINEERS INDIA — refreshing session (attempt 2)
# 2026-07-07 09:05:58,496 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=ENGINEERS INDIA — refreshing session (attempt 3)
# 2026-07-07 09:06:01,281 - services.nse_data_service - WARNING - [NSE] HTTP 404 for https://www.nseindia.com/api/search/autocomplete?q=Engineers India
# Engineers India                                                               ❌ UNRESOLVED (or skipped)
# 2026-07-07 09:06:02,502 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=GLOBAL HEALTH — refreshing session (attempt 1)
# 2026-07-07 09:06:03,979 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=GLOBAL HEALTH — refreshing session (attempt 2)
# 2026-07-07 09:06:06,094 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=GLOBAL HEALTH — refreshing session (attempt 3)
# 2026-07-07 09:06:08,891 - services.nse_data_service - WARNING - [NSE] HTTP 404 for https://www.nseindia.com/api/search/autocomplete?q=Global Health
# Global Health                                                                 ❌ UNRESOLVED (or skipped)
# 2026-07-07 09:06:09,397 - services.scrip_master_db - INFO - [ScripMasterDB] fuzzy prefix (full_name): 'GUJARAT PIPAVAV%' → INE517F01014
# Gujarat Pipavav Port                INE517F01014         GPPL                 ✅ RESOLVED
# 2026-07-07 09:06:10,573 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=HEMISPHERE PROPERTIE — refreshing session (attempt 1)
# 2026-07-07 09:06:12,067 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=HEMISPHERE PROPERTIE — refreshing session (attempt 2)
# 2026-07-07 09:06:14,227 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=HEMISPHERE PROPERTIE — refreshing session (attempt 3)
# 2026-07-07 09:06:16,982 - services.nse_data_service - WARNING - [NSE] HTTP 404 for https://www.nseindia.com/api/search/autocomplete?q=Hemisphere Propertie
# Hemisphere Propertie                                                          ❌ UNRESOLVED (or skipped)
# 2026-07-07 09:06:17,962 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=HOUSING & URBAN DEV. — refreshing session (attempt 1)
# 2026-07-07 09:06:19,458 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=HOUSING & URBAN DEV. — refreshing session (attempt 2)
# 2026-07-07 09:06:21,553 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=HOUSING & URBAN DEV. — refreshing session (attempt 3)
# 2026-07-07 09:06:24,442 - services.nse_data_service - WARNING - [NSE] HTTP 404 for https://www.nseindia.com/api/search/autocomplete?q=Housing & Urban Dev.
# Housing & Urban Dev.                                                          ❌ UNRESOLVED (or skipped)
# 2026-07-07 09:06:24,870 - services.scrip_master_db - INFO - [ScripMasterDB] fuzzy prefix (full_name): 'ICICI PRUDENTIAL%' → INF109KC11V0
# ICICI Prudential AMC                INF109KC11V0         NV20IETF             ✅ RESOLVED
# 2026-07-07 09:06:25,825 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=IDFCBOND12 — refreshing session (attempt 1)
# 2026-07-07 09:06:27,299 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=IDFCBOND12 — refreshing session (attempt 2)
# 2026-07-07 09:06:29,498 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=IDFCBOND12 — refreshing session (attempt 3)
# 2026-07-07 09:06:32,224 - services.nse_data_service - WARNING - [NSE] HTTP 404 for https://www.nseindia.com/api/search/autocomplete?q=IDFCBOND12
# IDFCBOND12                                                                    ❌ UNRESOLVED (or skipped)
# 2026-07-07 09:06:33,239 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=IIFCBOND16 — refreshing session (attempt 1)
# 2026-07-07 09:06:34,725 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=IIFCBOND16 — refreshing session (attempt 2)
# 2026-07-07 09:06:36,791 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=IIFCBOND16 — refreshing session (attempt 3)
# 2026-07-07 09:06:39,605 - services.nse_data_service - WARNING - [NSE] HTTP 404 for https://www.nseindia.com/api/search/autocomplete?q=IIFCBOND16
# IIFCBOND16                                                                    ❌ UNRESOLVED (or skipped)
# 2026-07-07 09:06:40,189 - services.scrip_master_db - INFO - [ScripMasterDB] fuzzy prefix (full_name): 'INDIA SHELTER%' → INE922K01024
# India Shelter Fin.                  INE922K01024         INDIASHLTR           ✅ RESOLVED
# 2026-07-07 09:06:40,620 - services.scrip_master_db - INFO - [ScripMasterDB] fuzzy prefix (full_name): 'INDUS TOWERS%' → INE121J01017
# Indus Towers                        INE121J01017         INDUSTOWER           ✅ RESOLVED
# 2026-07-07 09:06:41,819 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=JIO FINANCIAL — refreshing session (attempt 1)
# 2026-07-07 09:06:43,373 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=JIO FINANCIAL — refreshing session (attempt 2)
# 2026-07-07 09:06:45,447 - services.nse_data_service - WARNING - [NSE] 403 on https://www.nseindia.com/api/quote-equity?symbol=JIO FINANCIAL — refreshing session (attempt 3)
