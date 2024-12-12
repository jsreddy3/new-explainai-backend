import pytest
import asyncio
import aiohttp
import json
import logging
import os
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class AuthenticatedClient:
    """Client that handles authentication and maintains JWT token"""
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = None
        self.jwt_token = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def login(self, google_token: str):
        """Login with Google token and store JWT"""
        async with self.session.post(
            f"{self.base_url}/api/auth/google/login?token={google_token}"
        ) as response:
            assert response.status == 200
            data = await response.json()
            self.jwt_token = data["access_token"]
            return data["user"]
    
    async def get_me(self):
        """Get current user info"""
        async with self.session.get(
            f"{self.base_url}/api/auth/me",
            headers={"Authorization": f"Bearer {self.jwt_token}"}
        ) as response:
            assert response.status == 200
            return await response.json()
    
    async def get_my_documents(self):
        """Get user's documents"""
        async with self.session.get(
            f"{self.base_url}/api/auth/me/documents",
            headers={"Authorization": f"Bearer {self.jwt_token}"}
        ) as response:
            assert response.status == 200
            return await response.json()
    
    async def upload_document(self, file_path: str):
        """Upload a document"""
        with open(file_path, "rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename=os.path.basename(file_path))
            async with self.session.post(
                f"{self.base_url}/api/documents/upload",
                data=data,
                headers={"Authorization": f"Bearer {self.jwt_token}"}
            ) as response:
                assert response.status == 200
                return await response.json()

class AuthenticatedWebSocketClient:
    """WebSocket client that includes authentication"""
    def __init__(self, session: aiohttp.ClientSession, url: str, jwt_token: str):
        self.session = session
        # Add token to WebSocket URL
        self.url = f"{url}?token={jwt_token}"
        self.ws = None
        self._closed = False
        self._message_queue = asyncio.Queue()
        self._listener_task = None
    
    async def connect(self):
        """Connect to the WebSocket server"""
        if self.ws is None:
            timeout = aiohttp.ClientTimeout(total=60.0, sock_connect=30.0)
            self.ws = await self.session.ws_connect(
                self.url,
                timeout=timeout,
                heartbeat=30.0
            )
            self._listener_task = asyncio.create_task(self._listen())
            self._closed = False
    
    async def _listen(self):
        """Listen for messages from the server"""
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = msg.json()
                    logger.debug(f"Received WebSocket message: {data}")
                    await self._message_queue.put(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket closed")
                    break
        except Exception as e:
            logger.error(f"Error in WebSocket listener: {e}")
    
    async def send_json(self, data: dict):
        """Send JSON data to the server"""
        if not self._closed and self.ws:
            await self.ws.send_json(data)
    
    async def receive_json(self, timeout: float = 180.0, ignore_tokens: bool = True):
        """Receive JSON data from the server"""
        try:
            while True:
                message = await asyncio.wait_for(self._message_queue.get(), timeout)
                if ignore_tokens and message.get("type") in ["chat.token", "chat.completed"]:
                    continue
                return message
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for WebSocket response after {timeout} seconds")
            raise
    
    async def close(self):
        """Close the WebSocket connection"""
        self._closed = True
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            await self.ws.close()
            self.ws = None

@pytest.mark.asyncio
async def test_auth_flow():
    """Test complete authentication flow with document upload and conversations"""
    # New Google ID token
    google_token = "eyJhbGciOiJSUzI1NiIsImtpZCI6IjJjOGEyMGFmN2ZjOThmOTdmNDRiMTQyYjRkNWQwODg0ZWIwOTM3YzQiLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJodHRwczovL2FjY291bnRzLmdvb2dsZS5jb20iLCJhenAiOiIzOTUwOTQ2NTU2NDgtdHBmNWd2dG1rN2gzYzA4NmdwNnViZjJxZ3VwZjh1aWIuYXBwcy5nb29nbGV1c2VyY29udGVudC5jb20iLCJhdWQiOiIzOTUwOTQ2NTU2NDgtdHBmNWd2dG1rN2gzYzA4NmdwNnViZjJxZ3VwZjh1aWIuYXBwcy5nb29nbGV1c2VyY29udGVudC5jb20iLCJzdWIiOiIxMDc3NjQ1NDQwMjQwNTU2NDk0NzMiLCJlbWFpbCI6ImphaWRlbnJlZGR5QGdtYWlsLmNvbSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlLCJhdF9oYXNoIjoiWFdzTm1qSC1IVkZvbjdUNmkxOU0zZyIsIm5hbWUiOiJKYWlkZW4gUmVkZHkiLCJwaWN0dXJlIjoiaHR0cHM6Ly9saDMuZ29vZ2xldXNlcmNvbnRlbnQuY29tL2EvQUNnOG9jS3RpMUttekw1eTY1OHRsV2dwYTFFNG50ZEdnbnFzZE9KVlY3ZDhoZEJJMEhjRFpTRWo9czk2LWMiLCJnaXZlbl9uYW1lIjoiSmFpZGVuIiwiZmFtaWx5X25hbWUiOiJSZWRkeSIsImlhdCI6MTczNDA0Mzc5OCwiZXhwIjoxNzM0MDQ3Mzk4fQ.d50IsIm9h6qylkDKqLuaDYsXeCjIzPlASXMGHDDSYU6ksZD28aT2mmZ5mNd0NzrXvPudto3zLOH8qXbyDwaWwQhIq_O9dFj5w-UDf6pVqgSNdyQzFDFPnGmyBClhY52504ZRGerc5ae7H1_6zu4LvZHud5aG-k8b9SZ7GA1oDkJ22JwVYV6PiNgPME7AVqWs-qYXduHPQb9fsA8CpiQmCSX-haQo9rIsQyYJ3iRz9gnhbERu4dLl_qjz2BVJkRBCMTVKrU_GGxV4ScalhaLYVo9bqWbhGhZhUR9w0AzwlZgqgor4FfTezPLSwGT13zeKzhjawAjh5cbYd7pgna0avQ"
    
    async with AuthenticatedClient() as client:
        # First login - this also handles signup if user doesn't exist
        user = await client.login(google_token)
        assert user["email"] == "jaidenreddy@gmail.com"
        
        # Upload first document
        test_pdf_path = os.path.join(os.path.dirname(__file__), 'pdfs', 'pale fire presentation.pdf')
        doc1_response = await client.upload_document(test_pdf_path)
        doc1_id = doc1_response["document_id"]
        
        # Create conversation WebSocket client
        ws_client1 = AuthenticatedWebSocketClient(
            client.session,
            f"ws://localhost:8000/api/conversations/stream/{doc1_id}",
            client.jwt_token
        )
        
        try:
            await ws_client1.connect()
            
            # Create main conversation
            await ws_client1.send_json({
                "type": "conversation.main.create",
                "data": {
                    "document_id": doc1_id,
                    "content": "What is the main theme of Pale Fire?",
                    "conversation_type": "main",
                    "chunk_id": 0
                }
            })
            
            # Get response
            response = await ws_client1.receive_json()
            assert response["type"] == "conversation.main.create.completed"
            conversation1_id = response["data"]["conversation_id"]
            
            # Close first WebSocket
            await ws_client1.close()
            
            # Verify documents exist
            docs = await client.get_my_documents()
            assert len(docs) == 1
            assert docs[0]["id"] == doc1_id
            
            # Upload second document
            doc2_response = await client.upload_document(test_pdf_path)
            doc2_id = doc2_response["document_id"]
            
            # Create second conversation
            ws_client2 = AuthenticatedWebSocketClient(
                client.session,
                f"ws://localhost:8000/api/conversations/stream/{doc2_id}",
                client.jwt_token
            )
            
            try:
                await ws_client2.connect()
                
                # Create conversation
                await ws_client2.send_json({
                    "type": "conversation.main.create",
                    "data": {
                        "document_id": doc2_id,
                        "content": "Explain the relationship between Shade and Kinbote",
                        "conversation_type": "main",
                        "chunk_id": 0
                    }
                })
                
                # Get response
                response = await ws_client2.receive_json()
                assert response["type"] == "conversation.main.create.completed"
                conversation2_id = response["data"]["conversation_id"]
                
            finally:
                await ws_client2.close()
            
            # Verify both documents exist
            docs = await client.get_my_documents()
            assert len(docs) == 2
            doc_ids = {doc["id"] for doc in docs}
            assert doc1_id in doc_ids
            assert doc2_id in doc_ids
            
            # Test re-login
            user = await client.login(google_token)
            assert user["email"] == "jaidenreddy@gmail.com"
            
            # Verify documents still accessible
            docs = await client.get_my_documents()
            assert len(docs) == 2
            
            # Verify conversations still accessible
            ws_client3 = AuthenticatedWebSocketClient(
                client.session,
                f"ws://localhost:8000/api/conversations/stream/{doc1_id}",
                client.jwt_token
            )
            
            try:
                await ws_client3.connect()
                
                # List conversations
                await ws_client3.send_json({
                    "type": "conversation.list",
                    "data": {"document_id": doc1_id}
                })
                
                response = await ws_client3.receive_json()
                assert response["type"] == "conversation.list.completed"
                conversations = response["data"]["conversations"]
                assert len(conversations) == 1
                assert list(conversations.keys())[0] == conversation1_id
                
            finally:
                await ws_client3.close()
            
        finally:
            if 'ws_client1' in locals():
                await ws_client1.close()
