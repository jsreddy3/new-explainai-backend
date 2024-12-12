import pytest
import aiohttp
import jwt

from src.api.routes.auth import SECRET_KEY, ALGORITHM

pytestmark = pytest.mark.asyncio

@pytest.fixture(scope="session")
async def client():
    async with aiohttp.ClientSession() as client:
        yield client

class TestAuth:
    async def test_signup(self, client):
        """Test user signup endpoint"""
        test_data = {
            "email": "test@example.com",
            "password": "testpassword123"
        }
        async with client.post("http://localhost:8000/api/auth/signup", json=test_data) as response:
            assert response.status == 200
            data = await response.json()
            assert "access_token" in data
            assert "user" in data
            assert data["user"]["email"] == test_data["email"]
            
            # Verify token
            token = data["access_token"]
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            assert "sub" in payload  # sub should contain user ID

    async def test_duplicate_signup(self, client):
        """Test that duplicate email signup fails"""
        test_data = {
            "email": "test2@example.com",
            "password": "testpassword123"
        }
        # First signup
        async with client.post("http://localhost:8000/api/auth/signup", json=test_data) as response:
            assert response.status == 200
        
        # Try to signup again with same email
        async with client.post("http://localhost:8000/api/auth/signup", json=test_data) as response:
            assert response.status == 400
            data = await response.json()
            assert "Email already registered" in data["detail"]

    async def test_login(self, client):
        """Test user login endpoint"""
        test_data = {
            "email": "test3@example.com",
            "password": "testpassword123"
        }
        # First create a user
        async with client.post("http://localhost:8000/api/auth/signup", json=test_data) as response:
            assert response.status == 200
        
        # Try to login
        async with client.post("http://localhost:8000/api/auth/login", json=test_data) as response:
            assert response.status == 200
            data = await response.json()
            assert "access_token" in data
            assert "user" in data
            assert data["user"]["email"] == test_data["email"]

    async def test_login_wrong_password(self, client):
        """Test login with wrong password"""
        test_data = {
            "email": "test4@example.com",
            "password": "testpassword123"
        }
        # First create a user
        async with client.post("http://localhost:8000/api/auth/signup", json=test_data) as response:
            assert response.status == 200
        
        # Try to login with wrong password
        wrong_data = test_data.copy()
        wrong_data["password"] = "wrongpassword"
        async with client.post("http://localhost:8000/api/auth/login", json=wrong_data) as response:
            assert response.status == 400
            data = await response.json()
            assert "Incorrect email or password" in data["detail"]

    async def test_get_user_documents(self, client):
        """Test getting user documents"""
        test_data = {
            "email": "test5@example.com",
            "password": "testpassword123"
        }
        # First create a user and get token
        async with client.post("http://localhost:8000/api/auth/signup", json=test_data) as response:
            assert response.status == 200
            data = await response.json()
            token = data["access_token"]
            user_id = data["user"]["id"]
        
        # Try to get documents
        headers = {"Authorization": f"Bearer {token}"}
        async with client.get(f"http://localhost:8000/api/auth/documents?user_id={user_id}", headers=headers) as response:
            assert response.status == 200
            data = await response.json()
            assert "documents" in data
            # Initially there should be no documents
            assert len(data["documents"]) == 0
