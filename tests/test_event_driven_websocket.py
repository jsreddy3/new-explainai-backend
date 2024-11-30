import pytest
import asyncio
import aiohttp
import json
import logging
import sys
import os
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class WebSocketClient:
    """WebSocket client for testing"""
    def __init__(self, session: aiohttp.ClientSession, url: str):
        self.session = session
        self.url = url
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
            raise
        finally:
            await self.close()
    
    async def send_json(self, data: dict):
        """Send JSON data to the server"""
        if not self.ws:
            await self.connect()
        logger.debug(f"Sending WebSocket message: {data}")
        await self.ws.send_json(data)
    
    async def receive_json(self, timeout: float = 5.0):
        """Receive JSON data from the server"""
        try:
            return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError("Timeout waiting for WebSocket message")
    
    async def close(self):
        """Close the WebSocket connection"""
        if not self._closed:
            self._closed = True
            if self._listener_task and not self._listener_task.done():
                self._listener_task.cancel()
                try:
                    await self._listener_task
                except (asyncio.CancelledError, Exception):
                    pass
            if self.ws and not self.ws.closed:
                await self.ws.close()
            self.ws = None

@pytest.fixture
async def websocket_client():
    """Fixture to provide a WebSocket client"""
    session = aiohttp.ClientSession()
    client = WebSocketClient(session, "ws://localhost:8000/api/conversations/stream")
    try:
        await client.connect()
        yield client
    finally:
        await client.close()
        await session.close()

@pytest.fixture
async def document_websocket():
    """Fixture to provide a document WebSocket client"""
    session = aiohttp.ClientSession()
    
    # First, create a document using the test PDF
    with open("backend/tests/pdfs/pale fire presentation.pdf", "rb") as f:
        form = aiohttp.FormData()
        form.add_field("file", f)
        async with session.post("http://localhost:8000/api/documents/upload", data=form) as response:
            document = await response.json()
            document_id = document["document_id"]
    
    # Create WebSocket client for document events
    client = WebSocketClient(session, f"ws://localhost:8000/api/documents/{document_id}/stream")
    try:
        await client.connect()
        yield client, document_id
    finally:
        await client.close()
        await session.close()

@pytest.mark.asyncio
async def test_document_event_flow(document_websocket):
    """Test document retrieval and navigation flow"""
    async for client, document_id in document_websocket:
        # Test document retrieval
        await client.send_json({
            "action": "get_document",
            "document_id": document_id
        })
        
        # Verify document retrieval response
        response = await client.receive_json()
        assert response["type"] == "document.get.completed"
        assert response["document_id"] == document_id
        assert "content" in response
        
        # Test chunk navigation
        await client.send_json({
            "action": "navigate_chunks",
            "document_id": document_id,
            "chunk_index": 1
        })
        
        # Verify navigation response
        response = await client.receive_json()
        assert response["type"] == "document.chunks.navigate.completed"
        assert response["document_id"] == document_id
        assert "chunk_index" in response
        break  # We only need one iteration

@pytest.mark.asyncio
async def test_conversation_event_flow(websocket_client, document_websocket):
    """Test conversation creation and message flow"""
    async for conv_client in websocket_client:
        async for _, document_id in document_websocket:
            # Test conversation creation
            await conv_client.send_json({
                "action": "create_conversation",
                "document_id": document_id
            })
            
            # Verify conversation creation response
            response = await conv_client.receive_json()
            assert response["type"] == "conversation.chunk.create.completed"
            conversation_id = response["conversation_id"]
            
            # Test message sending
            await conv_client.send_json({
                "action": "send_message",
                "conversation_id": conversation_id,
                "content": "What is this document about?"
            })
            
            # Verify message response
            response = await conv_client.receive_json()
            assert response["type"] == "conversation.message.send.completed"
            assert response["conversation_id"] == conversation_id
            
            # Test question generation
            await conv_client.send_json({
                "action": "generate_questions",
                "conversation_id": conversation_id,
                "count": 3
            })
            
            # Verify question generation response
            response = await conv_client.receive_json()
            assert response["type"] == "conversation.questions.generate.completed"
            assert response["conversation_id"] == conversation_id
            assert "questions" in response
            break  # We only need one iteration
        break  # We only need one iteration

@pytest.mark.asyncio
async def test_concurrent_operations(websocket_client, document_websocket):
    """Test concurrent document and conversation operations"""
    async for conv_client in websocket_client:
        async for doc_client, document_id in document_websocket:
            # Create tasks for concurrent operations
            tasks = [
                asyncio.create_task(doc_client.send_json({
                    "action": "get_document",
                    "document_id": document_id
                })),
                asyncio.create_task(conv_client.send_json({
                    "action": "create_conversation",
                    "document_id": document_id
                }))
            ]
            
            # Wait for all tasks to complete
            await asyncio.gather(*tasks)
            
            # Verify responses in any order
            responses = []
            for _ in range(2):
                try:
                    responses.append(await doc_client.receive_json())
                except TimeoutError:
                    responses.append(await conv_client.receive_json())
            
            # Verify we got both responses
            response_types = {r["type"] for r in responses}
            assert "document.get.completed" in response_types
            assert "conversation.chunk.create.completed" in response_types
            break  # We only need one iteration
        break  # We only need one iteration
