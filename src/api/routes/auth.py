from fastapi import APIRouter, HTTPException, Depends, Body, Query
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_
from datetime import datetime, timedelta
from typing import Optional, Dict
from src.core.logging import setup_logger
import os

logger = setup_logger(__name__)

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
                "name": user.name,
                "is_admin": user.is_admin
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

async def get_current_user_or_none(
    token: Optional[str] = Depends(oauth2_scheme),
    document_id: Optional[str] = None,
    auth_service: AuthService = Depends(get_auth_service)
) -> Optional[User]:
    logger.info(f"get_current_user_or_none called with token: {token[:20] if token else None}")
    logger.info(f"document_id: {document_id}")
    # If document_id provided and it's an example document, allow unauthenticated access
    if document_id is not None and document_id in settings.EXAMPLE_DOCUMENT_IDS:
        return None
    
    # For all other cases, require auth
    try:
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return await auth_service.get_current_user(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

@router.get("/auth/me")
async def get_current_user_info(current_user: User = Depends(get_current_user_or_none)):
    """Get current user information"""
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "name": current_user.name
    }

@router.get("/auth/me/documents")
async def get_user_documents(
    current_user: Optional[User] = Depends(get_current_user_or_none),
    db: AsyncSession = Depends(get_db)
):
    """Get all documents for the current user or example documents if not authenticated"""
    if current_user is None:
        logger.info("No authenticated user, returning example documents")
        # Rest of the code...
        # Get example documents from database
        result = await db.execute(
            select(Document).where(Document.id.in_(settings.EXAMPLE_DOCUMENT_IDS))
        )
        example_docs = result.scalars().all()
        return [
            {
                "id": str(doc.id),
                "title": doc.title,
                "created_at": doc.created_at.isoformat()
            }
            for doc in example_docs
        ]
    
    # Get user's documents
    result = await db.execute(
        select(Document).where(Document.owner_id == str(current_user.id))
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

@router.get("/auth/me/cost")
async def get_user_cost(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get accumulated cost for the current user"""
    try:
        result = await db.execute(
            select(User).where(User.id == current_user.id)
        )
        user = result.scalar_one()
        
        return {
            "user_id": str(user.id),
            "total_cost": float(user.user_cost),
            "formatted_cost": f"${float(user.user_cost):.2f}"  # Changed to 2 decimal places
        }
    except Exception as e:
        logger.error(f"Failed to get user cost: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve user cost information"
        )

# In the routes file, update the approve_user endpoint:
@router.post("/auth/approve-user")
async def approve_user(
    email: str = Body(..., embed=True),  # Changed this line
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Approve a user for access (creates user if they don't exist)"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    result = await db.execute(
        select(User).where(User.email == email)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        # Create new user
        from uuid import uuid4
        user = User(
            id=str(uuid4()),
            email=email,
            name=email.split('@')[0],  # Use email prefix as temporary name
            is_approved=True,
            approval_type="manual",
            user_cost=0.0,
            created_at=datetime.utcnow(),
            is_admin=False
        )
        db.add(user)
    else:
        user.is_approved = True
        user.approval_type = "manual"
        
    await db.commit()
    await db.refresh(user)
    
    return {
        "message": f"User {email} approved",
        "was_created": user is not None,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "is_admin": user.is_admin,
            "approval_type": user.approval_type
        }
    }

@router.get("/auth/approved-users")
async def list_approved_users(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all manually approved users"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    result = await db.execute(
        select(User).where(
            and_(
                User.is_approved == True,
                User.approval_type == "manual"
            )
        )
    )
    users = result.scalars().all()
    
    return [
        {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "created_at": user.created_at.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None
        }
        for user in users
    ]

@router.delete("/auth/approve-user/{email}")
async def remove_user_approval(
    email: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove manual approval for a user"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    result = await db.execute(
        select(User).where(User.email == email)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.approval_type == "manual":
        user.is_approved = False
        user.approval_type = None
        await db.commit()
        return {"message": f"Approval removed for user {email}"}
    else:
        raise HTTPException(
            status_code=400, 
            detail="Can only remove manual approvals"
        )

@router.post("/auth/request-approval")
async def request_approval(
    name: str = Body(...),
    email: str = Body(...),
    reason: str = Body(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Log the user's request for approval in a text file.
    """
    try:
        # Append the request to a log file.
        # Adjust the file path to your liking, or store in a DB if preferred.
        log_path = "approval_requests.txt"
        log_entry = f"{datetime.utcnow().isoformat()} | Name: {name} | Email: {email} | Reason: {reason}\n"
        
        # Make sure the directory exists, if needed
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
        
        return {"message": "Request logged successfully"}

    except Exception as e:
        # If there's any error (e.g., file write permission), return 500
        raise HTTPException(status_code=500, detail=f"Failed to log request: {str(e)}")

@router.get("/auth/config")
async def get_auth_config():
    """Get authentication configuration for frontend"""
    return {
        "googleClientId": settings.GOOGLE_CLIENT_ID,
        "apiBaseUrl": settings.API_BASE_URL,
        "environment": settings.ENVIRONMENT
    }
