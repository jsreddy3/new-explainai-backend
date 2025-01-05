import asyncio
import sys
import os
from sqlalchemy import select
from datetime import datetime, timedelta
import jwt

# Add the project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

# Override database URL to use absolute path
os.environ['DATABASE_URL'] = f"sqlite+aiosqlite:///{project_root}/explainai.db"

from src.db.session import AsyncSessionLocal, init_db
from src.models.database import User
from src.core.config import settings

async def create_test_user():
    # Initialize the database
    await init_db()
    
    async with AsyncSessionLocal() as db:
        # Check if test user exists
        stmt = select(User).where(User.email == "test@example.com")
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            # Create test user
            user = User(
                email="test@example.com",
                name="Test User",
                is_approved=True,
                approval_type="manual",
                is_admin=True,
                user_cost=0.0
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        
        # Generate JWT token with 24 hour expiration
        expiration = datetime.utcnow() + timedelta(hours=24)
        payload = {
            'user_id': str(user.id),
            'exp': expiration
        }
        token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
        
        print(f"\nTest user created successfully!")
        print(f"User ID: {user.id}")
        print(f"JWT Token: {token}")
        print("\nYou can use this token in your requests like this:")
        print(f'export EXPLAINAI_VIVEK_AUTH_TOKEN="{token}"')

if __name__ == "__main__":
    asyncio.run(create_test_user()) 