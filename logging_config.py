import logging
import os
from logging.handlers import TimedRotatingFileHandler

# Log directory – will be created automatically
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def setup_logging():
    # Root logger – captures all modules
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)   # Set to DEBUG for more detail

    # Console handler (optional – for dev)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console_fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console.setFormatter(console_fmt)
    root_logger.addHandler(console)

    # File handler – rotates daily, keeps 30 days
    log_file = os.path.join(LOG_DIR, "portfolio_tracker.log")
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",          # rotate at midnight
        interval=1,               # daily
        backupCount=30,           # keep last 30 days
        encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)

    # Optional: log SQLAlchemy queries (set to INFO to see all SQL)
    logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)