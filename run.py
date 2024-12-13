import logging
import uvicorn
import os
from src.core.config import settings

# Disable all SQL logging before anything else
logging.getLogger('sqlalchemy').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.engine.base.Engine').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.dialects').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.pool').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.orm').setLevel(logging.WARNING)

if __name__ == "__main__":
    # Get port from environment variable (for Heroku) or use default
    port = int(os.environ.get("PORT", 8000))
    
    # Configure host based on environment
    host = "0.0.0.0" if settings.ENVIRONMENT == "production" else "127.0.0.1"
    
    # Only enable reload in development
    reload = settings.ENVIRONMENT == "development"
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload
    )
