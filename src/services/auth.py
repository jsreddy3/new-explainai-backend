"""Authentication service for handling Google OAuth and JWT operations"""

from typing import Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
import jwt
from google.oauth2 import id_token
from google.auth.transport import requests
import os

from ..models.database import User
from ..core.config import settings
from ..core.logging import setup_logger

logger = setup_logger(__name__)

class AuthService:
    def __init__(self, db: AsyncSession = None):
        self.db = db
        self.google_client_id = settings.GOOGLE_CLIENT_ID
        self.jwt_secret = settings.JWT_SECRET
        self.jwt_algorithm = "HS256"
        self.jwt_expiration = 24  # hours

    async def verify_google_token(self, token: str) -> Dict:
        """Verify Google OAuth token and return user info"""
        try:
            idinfo = id_token.verify_oauth2_token(
                token, requests.Request(), self.google_client_id)

            if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
                raise ValueError('Invalid issuer')

            return {
                'google_id': idinfo['sub'],
                'email': idinfo['email'],
                'name': idinfo.get('name', '')
            }
        except Exception as e:
            logger.error(f"Error verifying Google token: {e}")
            raise ValueError("Invalid token")

    async def create_or_update_user(self, user_info: Dict) -> User:
        """Create or update user from Google OAuth info"""
        try:
            # Check if user exists
            stmt = select(User).where(User.google_id == user_info['google_id'])
            result = await self.db.execute(stmt)
            user = result.scalar_one_or_none()

            if user:
                # Update existing user
                user.last_login = datetime.utcnow()
                user.name = user_info['name']
                user.email = user_info['email']
            else:
                # Create new user
                user = User(
                    google_id=user_info['google_id'],
                    email=user_info['email'],
                    name=user_info['name']
                )
                self.db.add(user)

            await self.db.commit()
            await self.db.refresh(user)
            return user

        except Exception as e:
            logger.error(f"Error creating/updating user: {e}")
            await self.db.rollback()
            raise

    def create_jwt_token(self, user_id: str) -> Dict[str, str]:
        """Create JWT token for user"""
        try:
            expiration = datetime.utcnow() + timedelta(hours=self.jwt_expiration)
            payload = {
                'user_id': user_id,
                'exp': expiration
            }
            token = jwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)
            
            return {
                'access_token': token,
                'token_type': 'bearer',
                'expires_in': self.jwt_expiration * 3600  # seconds
            }
        except Exception as e:
            logger.error(f"Error creating JWT token: {e}")
            raise

    async def verify_jwt_token(self, token: str) -> Optional[str]:
        """Verify JWT token and return user_id if valid"""
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=[self.jwt_algorithm])
            return payload['user_id']
        except jwt.ExpiredSignatureError:
            raise ValueError("Token has expired")
        except jwt.InvalidTokenError:
            raise ValueError("Invalid token")

    async def get_current_user(self, token: str) -> Optional[User]:
        """Get current user from JWT token"""
        try:
            user_id = await self.verify_jwt_token(token)
            stmt = select(User).where(User.id == user_id)
            result = await self.db.execute(stmt)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error getting current user: {e}")
            raise
