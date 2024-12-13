from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_
from datetime import datetime, timedelta
from typing import Optional, Dict

from ...models.database import User, Document
from ...db.session import get_db
from ...services.auth import AuthService
from ...core.config import settings

router = APIRouter()

# OAuth2 scheme for JWT
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Initialize auth service
def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(db)

@router.post("/auth/google/login")
async def google_login(
    token: str,
    auth_service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db)
):
    """Handle Google OAuth login"""
    try:
        # Verify Google token
        user_info = await auth_service.verify_google_token(token)
        
        # Create or update user
        user = await auth_service.create_or_update_user(user_info)
        
        # Generate JWT token
        token_data = auth_service.create_jwt_token(str(user.id))
        
        return {
            **token_data,
            "user": {
                "id": str(user.id),
                "email": user.email,
                "name": user.name
            }
        }
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    auth_service: AuthService = Depends(get_auth_service)
) -> User:
    """Get current user from JWT token"""
    try:
        user = await auth_service.get_current_user(token)
        if not user:
            raise HTTPException(
                status_code=401,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user
    except ValueError as e:
        raise HTTPException(
            status_code=401,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )

@router.get("/auth/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "name": current_user.name
    }

@router.get("/auth/me/documents")
async def get_user_documents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all documents for the current user"""
    result = await db.execute(
        select(Document).where(Document.owner_id == current_user.id)
    )
    documents = result.scalars().all()
    return [
        {
            "id": str(doc.id),
            "title": doc.title,
            "created_at": doc.created_at.isoformat()
        }
        for doc in documents
    ]

@router.get("/auth/config")
async def get_auth_config():
    """Get authentication configuration for frontend"""
    return {
        "googleClientId": settings.GOOGLE_CLIENT_ID,
        "apiBaseUrl": settings.API_BASE_URL,
        "environment": settings.ENVIRONMENT
    }
