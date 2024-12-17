import logging
import logging.handlers
from pathlib import Path
from datetime import datetime
import json
import os
from typing import Optional
import gzip
import shutil
from datetime import datetime, timedelta

# Disable all SQLAlchemy logging
logging.getLogger('sqlalchemy').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.engine.base.Engine').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.dialects').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.pool').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.orm').setLevel(logging.WARNING)

# Create logs directory structure
LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Create subdirectories for different components
COMPONENTS = ["ai", "api", "db", "general", "pdf"]
for component in COMPONENTS:
    (LOGS_DIR / component).mkdir(exist_ok=True)

# Log levels based on environment and component
def get_log_level(component: str) -> int:
    env = os.getenv("ENVIRONMENT", "production")
    
    # Default levels for production
    PROD_LEVELS = {
        "ai": logging.INFO,
        "api": logging.WARNING,
        "db": logging.WARNING,
        "general": logging.INFO,
        "pdf": logging.INFO
    }
    
    # More verbose levels for development
    DEV_LEVELS = {
        "ai": logging.DEBUG,
        "api": logging.DEBUG,
        "db": logging.INFO,
        "general": logging.DEBUG,
        "pdf": logging.DEBUG
    }
    
    levels = DEV_LEVELS if env == "development" else PROD_LEVELS
    return levels.get(component, logging.INFO)

class StructuredFormatter(logging.Formatter):
    """Format logs in a structured way for easier parsing"""
    def format(self, record):
        # Basic log data
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields if available
        if hasattr(record, "extra_data"):
            log_data.update(record.extra_data)
            
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_data)

def compress_old_logs():
    """Compress log files older than 1 day"""
    yesterday = datetime.now() - timedelta(minutes=60)
    
    for component_dir in LOGS_DIR.iterdir():
        if not component_dir.is_dir():
            continue
            
        for log_file in component_dir.glob("*.log"):
            # Skip if already compressed or currently in use
            if log_file.name.endswith(".gz") or log_file.name == f"{component_dir.name}.log":
                continue
                
            # Check if file is old enough
            if datetime.fromtimestamp(log_file.stat().st_mtime) < yesterday:
                # Compress the file
                with open(log_file, 'rb') as f_in:
                    gz_path = log_file.with_suffix('.log.gz')
                    with gzip.open(gz_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                # Remove original file
                log_file.unlink()

def cleanup_old_logs(max_days: int = 30):
    """Delete compressed logs older than max_days"""
    cutoff = datetime.now() - timedelta(days=max_days)
    
    for component_dir in LOGS_DIR.iterdir():
        if not component_dir.is_dir():
            continue
            
        for gz_file in component_dir.glob("*.log.gz"):
            if datetime.fromtimestamp(gz_file.stat().st_mtime) < cutoff:
                gz_file.unlink()

def get_component_from_name(name: str) -> str:
    """Extract component name from logger name"""
    for component in COMPONENTS:
        if component in name.lower():
            return component
    return "general"

def setup_logger(name: str) -> logging.Logger:
    """Set up a logger with file and console handlers"""
    
    # Determine component and create logger
    component = get_component_from_name(name)
    logger = logging.getLogger(name)
    logger.setLevel(get_log_level(component))
    
    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger
    
    # Create formatters
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_formatter = StructuredFormatter()
    
    # Console Handler - human readable
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(get_log_level(component))
    logger.addHandler(console_handler)
    
    # Component-specific log file with daily rotation
    log_file = LOGS_DIR / component / f"{component}.log"
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        backupCount=7,  # Keep a week of logs
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(get_log_level(component))
    
    # Set rotation filename pattern to include date
    file_handler.namer = lambda name: name.replace(".log", "") + datetime.now().strftime("_%Y%m%d.log")
    
    # Compress logs when rotating
    def rotator(source, dest):
        with open(source, 'rb') as f_in:
            with gzip.open(dest + '.gz', 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(source)
    
    file_handler.rotator = rotator
    logger.addHandler(file_handler)
    
    # Run maintenance tasks
    compress_old_logs()
    cleanup_old_logs()
    
    return logger

def log_with_context(logger: logging.Logger, level: int, msg: str, **kwargs):
    """Helper function to log with additional context"""
    extra = {"extra_data": kwargs}
    logger.log(level, msg, extra=extra)