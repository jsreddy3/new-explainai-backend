from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from src.core.config import settings
from src.core.logging import setup_logger

logger = setup_logger(__name__)

# Convert postgres:// to postgresql:// for SQLAlchemy
DATABASE_URL = settings.DATABASE_URL
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql+psycopg://', 1)
elif DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgresql+psycopg://', 1)

# Create async database engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_size=20,  # Allow up to 20 concurrent connections
    max_overflow=10,  # Allow up to 10 additional temporary connections
    pool_timeout=30,  # How long to wait for a connection from pool
    pool_pre_ping=True,  # Verify connections are still valid before using
    pool_recycle=3600,  # Recycle connections after 1 hour
)

# Create async sessionmaker
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Create base class for declarative models
Base = declarative_base()

async def drop_db():
    """Drop all tables"""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        logger.info("Database tables dropped successfully")
    except Exception as e:
        logger.error(f"Error dropping database: {str(e)}")
        raise

async def init_db():
    """Initialize database"""
    try:
        # Only create tables if they don't exist
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        raise

async def get_db():
    """Dependency for getting database sessions"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()