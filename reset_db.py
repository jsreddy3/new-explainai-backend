# reset_db.py
import asyncio
import os
import sys

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.db.session import init_db

async def reset():
    await init_db()

if __name__ == "__main__":
    asyncio.run(reset())