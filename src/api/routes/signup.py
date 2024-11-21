from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from src.core.config import settings
from src.core.logging import setup_logger
from src.db.session import get_db
from src.models.database import User

logger = setup_logger(__name__)
router = APIRouter()

class SignupRequest(BaseModel):
    name: str
    email: EmailStr

class SignupResponse(BaseModel):
    message: str
    email: str
    provider: str

def get_supabase() -> Client:
    """Get Supabase client if configured"""
    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY:
        return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    return None

@router.post("/signup", response_model=SignupResponse)
async def signup(
    request: SignupRequest,
    db: AsyncSession = Depends(get_db)
):
    """Handle user signup with Supabase and local DB fallback"""
    try:
        logger.info(f"Attempting signup for email: {request.email}")
        
        # Try Supabase first
        supabase = get_supabase()
        if supabase:
            try:
                data, error = supabase.auth.admin.create_user({
                    'email': request.email,
                    'email_confirm': True,
                    'user_metadata': {'name': request.name}
                })

                if error:
                    logger.error(f"Supabase error: {error}")
                    # Fall through to local DB
                else:
                    logger.info(f"Supabase signup successful for: {request.email}")
                    return SignupResponse(
                        message="Signup successful",
                        email=request.email,
                        provider="supabase"
                    )
            except Exception as e:
                logger.error(f"Supabase error, falling back to local DB: {str(e)}")
        
        # Local DB fallback
        try:
            user = User(
                name=request.name,
                email=request.email
            )
            db.add(user)
            await db.commit()
            
            logger.info(f"Local DB signup successful for: {request.email}")
            return SignupResponse(
                message="Signup successful",
                email=request.email,
                provider="local"
            )
            
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail="Email already registered"
            )

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during signup: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(e)}"
        )