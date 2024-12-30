from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.models.database import User
from src.core.exceptions import CostLimitExceededError
from src.core.logging import setup_logger

logger = setup_logger(__name__)

async def check_user_cost_limit(db: AsyncSession, user_id: str):
    """Check if user has exceeded their cost limit"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    logger.info("User cost limit check: User ID: %s, Cost: %s, Limit: %s", user.id, user.user_cost, user.cost_limit)
    
    if user.user_cost >= 0.5:
        raise CostLimitExceededError(user.user_cost, user.cost_limit)
