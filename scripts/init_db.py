import asyncio
import sys
import os

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.session import init_db
from src.core.logging import setup_logger

logger = setup_logger(__name__)

async def main():
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialization complete!")

if __name__ == "__main__":
    asyncio.run(main())
