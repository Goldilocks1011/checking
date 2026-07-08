# import os
# from sqlalchemy import create_engine
# from sqlalchemy.ext.declarative import declarative_base
# from sqlalchemy.orm import sessionmaker
# from dotenv import load_dotenv
# from sqlalchemy import text as _text
# from sqlalchemy import text
# from dotenv import load_dotenv
# import logging
# logger = logging.getLogger(__name__)
# load_dotenv()

# DB_USER = os.getenv("DB_USER")
# DB_PASSWORD = os.getenv("DB_PASSWORD")
# DB_HOST = os.getenv("DB_HOST")
# DB_PORT = os.getenv("DB_PORT")
# DB_NAME = os.getenv("DB_NAME")

# # --- Ensure the database exists ---
# # Connect without specifying database to create it

# def _ensure_database():
#     url = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}"
#     try:
#         eng = create_engine(url, echo=False)
#         with eng.connect() as conn:
#             conn.execute(_text(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}`"))
#         logger.info(f"[DB] Database `{DB_NAME}` ensured.")
#     except Exception as e:
#         logger.info(f"[DB] Could not create database: {e}")

# _ensure_database()

# DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# engine = create_engine(DATABASE_URL, echo=False)
# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# Base = declarative_base()

# # ── Column migration helpers ─────────────────────────────────────────────────

# # ── Raw connection for legacy code (engine.py, etc.) ──
# def get_connection():
#     """Return a raw MySQL connection (autocommit off)."""
#     return engine.connect()

# # ── Migration helper (convert ? placeholders to %s) ──
# def run_sql(sql: str, params=None):
#     """Execute a SQL statement with proper MySQL placeholders."""
#     with engine.connect() as conn:
#         if params:
#             conn.execute(text(sql), params)
#         else:
#             conn.execute(text(sql))
#         conn.commit()
        
# def _ensure_file_content_is_longblob():
#     """
#     Change file_content to LONGBLOB if it's currently a string type.
#     ADD COLUMN won't work if column already exists — use MODIFY instead.
#     """
#     try:
#         with engine.connect() as conn:
#             # Try ADD first (for fresh DBs without the column)
#             try:
#                 conn.execute(_text(
#                     "ALTER TABLE processed_files ADD COLUMN file_content LONGBLOB"
#                 ))
#                 conn.commit()
#                 logger.info("[Migration] Added file_content LONGBLOB column")
#             except Exception:
#                 # Column already exists — change its type to LONGBLOB
#                 conn.execute(_text(
#                     "ALTER TABLE processed_files MODIFY COLUMN file_content LONGBLOB"
#                 ))
#                 conn.commit()
#                 logger.info("[Migration] Changed file_content to LONGBLOB")
#     except Exception as e:
#         logger.info(f"[Migration] file_content column update failed: {e}")

# _ensure_file_content_is_longblob()


# def _ensure_processed_files_unique_constraint():
#     """
#     Migrate processed_files table:
#       1. Drop any old unique key on (user_id, filename) that blocks IIFL from
#          uploading the same file for both EQ and FNO.
#       2. Add the correct (user_id, filename, file_type) unique key so the same
#          filename can be stored once per file_type.
#     Safe to run on a fresh DB — both steps are no-ops if already correct.
#     """
#     try:
#         with engine.connect() as conn:
#             # ── Step 1: scan for any unique index that does NOT include file_type ──
#             rows = conn.execute(_text("""
#                 SELECT DISTINCT INDEX_NAME
#                 FROM information_schema.STATISTICS
#                 WHERE TABLE_SCHEMA = DATABASE()
#                   AND TABLE_NAME   = 'processed_files'
#                   AND NON_UNIQUE   = 0
#                   AND INDEX_NAME  != 'PRIMARY'
#                   AND INDEX_NAME  != 'uq_user_filename_filetype'
#             """)).fetchall()

#             for row in rows:
#                 idx_name = row[0]
#                 cols = conn.execute(_text("""
#                     SELECT COLUMN_NAME
#                     FROM information_schema.STATISTICS
#                     WHERE TABLE_SCHEMA = DATABASE()
#                       AND TABLE_NAME   = 'processed_files'
#                       AND INDEX_NAME   = :idx
#                     ORDER BY SEQ_IN_INDEX
#                 """), {"idx": idx_name}).fetchall()
#                 col_names = [c[0] for c in cols]
#                 if "file_type" not in col_names:
#                     try:
#                         conn.execute(_text(
#                             f"ALTER TABLE processed_files DROP INDEX `{idx_name}`"
#                         ))
#                         conn.commit()
#                         logger.info(f"[Migration] Dropped stale unique index '{idx_name}' "
#                               f"(cols: {col_names}) from processed_files")
#                     except Exception as drop_err:
#                         conn.rollback()
#                         logger.info(f"[Migration] Could not drop index '{idx_name}': {drop_err}")

#             # ── Step 2: add correct (user_id, filename, file_type) unique key ──
#             try:
#                 conn.execute(_text("""
#                     ALTER TABLE processed_files
#                     ADD UNIQUE KEY `uq_user_filename_filetype` (user_id, filename, file_type)
#                 """))
#                 conn.commit()
#                 logger.info("[Migration] Added uq_user_filename_filetype to processed_files")
#             except Exception:
#                 conn.rollback()   # already exists — fine

#     except Exception as e:
#         logger.info(f"[Migration] processed_files constraint migration failed: {e}")


# _ensure_processed_files_unique_constraint()


# def _ensure_accounts_role_column():
#     """
#     Add role + master_account_id columns to accounts table if missing.
#     Safe on fresh DB (no-op if columns already present).
#     Sets the lowest account id as 'master', all others as 'child'.
#     """
#     try:
#         with engine.connect() as conn:
#             try:
#                 conn.execute(_text(
#                     "ALTER TABLE accounts ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'child'"
#                 ))
#                 conn.commit()
#                 logger.info("[Migration] Added role column to accounts")
#             except Exception:
#                 conn.rollback()

#             try:
#                 conn.execute(_text(
#                     "ALTER TABLE accounts ADD COLUMN master_account_id INT NULL"
#                 ))
#                 conn.commit()
#                 logger.info("[Migration] Added master_account_id column to accounts")
#             except Exception:
#                 conn.rollback()

#             # Mark the first-created account as master
#             try:
#                 conn.execute(_text("""
#                     UPDATE accounts
#                     SET role = 'master'
#                     WHERE id = (SELECT min_id FROM (SELECT MIN(id) AS min_id FROM accounts) AS t)
#                 """))
#                 conn.commit()
#                 logger.info("[Migration] Marked lowest account id as master")
#             except Exception as e:
#                 conn.rollback()
#                 logger.info(f"[Migration] Could not set master role: {e}")
#     except Exception as e:
#         logger.info(f"[Migration] accounts role column migration failed: {e}")


# _ensure_accounts_role_column()


# def _ensure_users_referral_id_column():
#     """
#     Add referral_id column to users table if missing.
#     referral_id points to the master user's ID for child accounts.
#     Safe on fresh DB — no-op if column already present.
#     """
#     try:
#         with engine.connect() as conn:
#             try:
#                 conn.execute(_text(
#                     "ALTER TABLE users ADD COLUMN referral_id INT NULL"
#                 ))
#                 conn.commit()
#                 logger.info("[Migration] Added referral_id column to users")
#             except Exception:
#                 conn.rollback()   # already exists — fine
#     except Exception as e:
#         logger.info(f"[Migration] referral_id migration failed: {e}")


# _ensure_users_referral_id_column()



# # ── paste inside database.py ──────────────────────────────────────────────────

# def _ensure_fno_dividend_adjustments_table():
#     """
#     Safe migration: add fno_dividend_adjustments table and its columns.
#     No-op if the table already exists.
#     SQLAlchemy's create_all handles the table creation; this helper only
#     adds columns that might be missing in an existing table from an older
#     migration.
#     """
#     try:
#         with engine.connect() as conn:
#             # Add expiry_date column if missing (added after initial schema)
#             try:
#                 conn.execute(_text(
#                     "ALTER TABLE fno_dividend_adjustments "
#                     "ADD COLUMN expiry_date VARCHAR(10) NULL"
#                 ))
#                 conn.commit()
#                 logger.info("[Migration] Added expiry_date to fno_dividend_adjustments")
#             except Exception:
#                 conn.rollback()  # column already exists — fine

#             # Add scenario column if missing
#             try:
#                 conn.execute(_text(
#                     "ALTER TABLE fno_dividend_adjustments "
#                     "ADD COLUMN scenario VARCHAR(2) NOT NULL DEFAULT 'A'"
#                 ))
#                 conn.commit()
#                 logger.info("[Migration] Added scenario to fno_dividend_adjustments")
#             except Exception:
#                 conn.rollback()
#     except Exception as e:
#         logger.info(f"[Migration] fno_dividend_adjustments migration: {e}")


# _ensure_fno_dividend_adjustments_table()


# def _ensure_fno_synthetic_transactions_table():
#     """
#     Safe migration: ensure fno_synthetic_transactions exists.
#     SQLAlchemy create_all handles table creation; this is a safety guard
#     for production DBs where create_all might not be re-run.
#     """
#     try:
#         with engine.connect() as conn:
#             conn.execute(_text("""
#                 CREATE TABLE IF NOT EXISTS fno_synthetic_transactions (
#                     id               INT AUTO_INCREMENT PRIMARY KEY,
#                     user_id          INT          NOT NULL,
#                     adjustment_id    INT          NULL,
#                     underlying       VARCHAR(100) NOT NULL,
#                     instrument_type  VARCHAR(5)   NOT NULL,
#                     expiry_date      VARCHAR(10)  NULL,
#                     strike_price     FLOAT        DEFAULT 0.0,
#                     trade_type       VARCHAR(5)   NOT NULL,
#                     quantity         FLOAT        NOT NULL,
#                     price            FLOAT        DEFAULT 0.0,
#                     trade_date       VARCHAR(10)  NOT NULL,
#                     source           VARCHAR(50)  DEFAULT 'SYNTHETIC_ADJUSTMENT',
#                     notes            TEXT         NULL,
#                     created_at       DATETIME     DEFAULT NOW(),
#                     INDEX idx_fno_syn_user_und    (user_id, underlying),
#                     INDEX idx_fno_syn_user_adj    (user_id, adjustment_id),
#                     INDEX idx_fno_syn_user_expiry (user_id, expiry_date)
#                 )
#             """))
#             conn.commit()
#             logger.info("[Migration] fno_synthetic_transactions table ensured")
#     except Exception as e:
#         logger.info(f"[Migration] fno_synthetic_transactions: {e}")


# _ensure_fno_synthetic_transactions_table()

# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()
        
# ----------------------------------
import os
from urllib.parse import quote_plus
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from sqlalchemy import text as _text
from sqlalchemy import text

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME")

# Encode password for safe use in URLs
DB_PASSWORD_ENCODED = quote_plus(DB_PASSWORD)

# --- Ensure the database exists ---
def _ensure_database():
    url = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD_ENCODED}@{DB_HOST}:{DB_PORT}"
    try:
        eng = create_engine(url, echo=False)
        with eng.connect() as conn:
            conn.execute(_text(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}`"))
        print(f"[DB] Database `{DB_NAME}` ensured.")
    except Exception as e:
        print(f"[DB] Could not create database: {e}")

_ensure_database()

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD_ENCODED}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Raw connection for legacy code ---
def get_connection():
    """Return a raw MySQL connection (autocommit off)."""
    return engine.connect()

# --- Migration helper ---
def run_sql(sql: str, params=None):
    """Execute a SQL statement with proper MySQL placeholders."""
    with engine.connect() as conn:
        if params:
            conn.execute(text(sql), params)
        else:
            conn.execute(text(sql))
        conn.commit()

def _ensure_file_content_is_longblob():
    """
    Change file_content to LONGBLOB if it's currently a string type.
    ADD COLUMN won't work if column already exists -- use MODIFY instead.
    """
    try:
        with engine.connect() as conn:
            # Try ADD first (for fresh DBs without the column)
            try:
                conn.execute(_text(
                    "ALTER TABLE processed_files ADD COLUMN file_content LONGBLOB"
                ))
                conn.commit()
                print("[Migration] Added file_content LONGBLOB column")
            except Exception:
                # Column already exists -- change its type to LONGBLOB
                conn.execute(_text(
                    "ALTER TABLE processed_files MODIFY COLUMN file_content LONGBLOB"
                ))
                conn.commit()
                print("[Migration] Changed file_content to LONGBLOB")
    except Exception as e:
        print(f"[Migration] file_content column update failed: {e}")

_ensure_file_content_is_longblob()

def _ensure_processed_files_unique_constraint():
    """
    Migrate processed_files table:
      1. Drop any old unique key on (user_id, filename) that blocks IIFL from
         uploading the same file for both EQ and FNO.
      2. Add the correct (user_id, filename, file_type) unique key so the same
         filename can be stored once per file_type.
    Safe to run on a fresh DB -- both steps are no-ops if already correct.
    """
    try:
        with engine.connect() as conn:
            # Step 1: scan for any unique index that does NOT include file_type
            rows = conn.execute(_text("""
                SELECT DISTINCT INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'processed_files'
                  AND NON_UNIQUE   = 0
                  AND INDEX_NAME  != 'PRIMARY'
                  AND INDEX_NAME  != 'uq_user_filename_filetype'
            """)).fetchall()

            for row in rows:
                idx_name = row[0]
                cols = conn.execute(_text("""
                    SELECT COLUMN_NAME
                    FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME   = 'processed_files'
                      AND INDEX_NAME   = :idx
                    ORDER BY SEQ_IN_INDEX
                """), {"idx": idx_name}).fetchall()
                col_names = [c[0] for c in cols]
                if "file_type" not in col_names:
                    try:
                        conn.execute(_text(
                            f"ALTER TABLE processed_files DROP INDEX `{idx_name}`"
                        ))
                        conn.commit()
                        print(f"[Migration] Dropped stale unique index '{idx_name}' "
                              f"(cols: {col_names}) from processed_files")
                    except Exception as drop_err:
                        conn.rollback()
                        print(f"[Migration] Could not drop index '{idx_name}': {drop_err}")

            # Step 2: add correct (user_id, filename, file_type) unique key
            try:
                conn.execute(_text("""
                    ALTER TABLE processed_files
                    ADD UNIQUE KEY `uq_user_filename_filetype` (user_id, filename, file_type)
                """))
                conn.commit()
                print("[Migration] Added uq_user_filename_filetype to processed_files")
            except Exception:
                conn.rollback()   # already exists -- fine

    except Exception as e:
        print(f"[Migration] processed_files constraint migration failed: {e}")

_ensure_processed_files_unique_constraint()

def _ensure_accounts_role_column():
    """
    Add role + master_account_id columns to accounts table if missing.
    Safe on fresh DB (no-op if columns already present).
    Sets the lowest account id as 'master', all others as 'child'.
    """
    try:
        with engine.connect() as conn:
            try:
                conn.execute(_text(
                    "ALTER TABLE accounts ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'child'"
                ))
                conn.commit()
                print("[Migration] Added role column to accounts")
            except Exception:
                conn.rollback()

            try:
                conn.execute(_text(
                    "ALTER TABLE accounts ADD COLUMN master_account_id INT NULL"
                ))
                conn.commit()
                print("[Migration] Added master_account_id column to accounts")
            except Exception:
                conn.rollback()

            # Mark the first-created account as master
            try:
                conn.execute(_text("""
                    UPDATE accounts
                    SET role = 'master'
                    WHERE id = (SELECT min_id FROM (SELECT MIN(id) AS min_id FROM accounts) AS t)
                """))
                conn.commit()
                print("[Migration] Marked lowest account id as master")
            except Exception as e:
                conn.rollback()
                print(f"[Migration] Could not set master role: {e}")
    except Exception as e:
        print(f"[Migration] accounts role column migration failed: {e}")

_ensure_accounts_role_column()

def _ensure_users_referral_id_column():
    """
    Add referral_id column to users table if missing.
    referral_id points to the master user's ID for child accounts.
    Safe on fresh DB -- no-op if column already present.
    """
    try:
        with engine.connect() as conn:
            try:
                conn.execute(_text(
                    "ALTER TABLE users ADD COLUMN referral_id INT NULL"
                ))
                conn.commit()
                print("[Migration] Added referral_id column to users")
            except Exception:
                conn.rollback()   # already exists -- fine
    except Exception as e:
        print(f"[Migration] referral_id migration failed: {e}")

_ensure_users_referral_id_column()

# ---- New migrations for FNO tables ----

def _ensure_fno_dividend_adjustments_table():
    """
    Safe migration: add fno_dividend_adjustments table and its columns.
    No-op if the table already exists.
    SQLAlchemy's create_all handles the table creation; this helper only
    adds columns that might be missing in an existing table from an older
    migration.
    """
    try:
        with engine.connect() as conn:
            # Add expiry_date column if missing (added after initial schema)
            try:
                conn.execute(_text(
                    "ALTER TABLE fno_dividend_adjustments "
                    "ADD COLUMN expiry_date VARCHAR(10) NULL"
                ))
                conn.commit()
                print("[Migration] Added expiry_date to fno_dividend_adjustments")
            except Exception:
                conn.rollback()  # column already exists -- fine

            # Add scenario column if missing
            try:
                conn.execute(_text(
                    "ALTER TABLE fno_dividend_adjustments "
                    "ADD COLUMN scenario VARCHAR(2) NOT NULL DEFAULT 'A'"
                ))
                conn.commit()
                print("[Migration] Added scenario to fno_dividend_adjustments")
            except Exception:
                conn.rollback()
    except Exception as e:
        print(f"[Migration] fno_dividend_adjustments migration: {e}")

_ensure_fno_dividend_adjustments_table()

def _ensure_fno_synthetic_transactions_table():
    """
    Safe migration: ensure fno_synthetic_transactions exists.
    SQLAlchemy create_all handles table creation; this is a safety guard
    for production DBs where create_all might not be re-run.
    """
    try:
        with engine.connect() as conn:
            conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS fno_synthetic_transactions (
                    id               INT AUTO_INCREMENT PRIMARY KEY,
                    user_id          INT          NOT NULL,
                    adjustment_id    INT          NULL,
                    underlying       VARCHAR(100) NOT NULL,
                    instrument_type  VARCHAR(5)   NOT NULL,
                    expiry_date      VARCHAR(10)  NULL,
                    strike_price     FLOAT        DEFAULT 0.0,
                    trade_type       VARCHAR(5)   NOT NULL,
                    quantity         FLOAT        NOT NULL,
                    price            FLOAT        DEFAULT 0.0,
                    trade_date       VARCHAR(10)  NOT NULL,
                    source           VARCHAR(50)  DEFAULT 'SYNTHETIC_ADJUSTMENT',
                    notes            TEXT         NULL,
                    created_at       DATETIME     DEFAULT NOW(),
                    INDEX idx_fno_syn_user_und    (user_id, underlying),
                    INDEX idx_fno_syn_user_adj    (user_id, adjustment_id),
                    INDEX idx_fno_syn_user_expiry (user_id, expiry_date)
                )
            """))
            conn.commit()
            print("[Migration] fno_synthetic_transactions table ensured")
    except Exception as e:
        print(f"[Migration] fno_synthetic_transactions: {e}")

_ensure_fno_synthetic_transactions_table()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()