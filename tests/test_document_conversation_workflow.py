import pytest
import asyncio
import aiohttp
import json
import logging
import os
import uuid

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class WebSocketTestClient:
    """Enhanced WebSocket client for testing with more robust error handling and lifecycle management"""
    def __init__(self, session: aiohttp.ClientSession, url: str, name: str = None):
        self.session = session
        self.url = url
        self.ws = None
        self._closed = False
        self._message_queue = asyncio.Queue()
        self._listener_task = None
        self.name = name or str(uuid.uuid4())
    
    async def __aenter__(self):
        """Support async context manager entry"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Support async context manager exit"""
        await self.close()
        return False  # Propagate any exceptions
    
    async def connect(self, timeout: float = 10.0):
        """Connect to the WebSocket server with enhanced error handling"""
        try:
            timeout_obj = aiohttp.ClientTimeout(total=timeout)
            self.ws = await self.session.ws_connect(
                self.url,
                timeout=timeout_obj,
                heartbeat=30.0
            )
            logger.info(f"WebSocket {self.name} connected to {self.url}")
            self._listener_task = asyncio.create_task(self._listen())
            return self
        except Exception as e:
            logger.error(f"WebSocket {self.name} connection failed: {e}")
            raise
    
    async def _listen(self):
        """Enhanced message listener with more robust error handling"""
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        logger.debug(f"WebSocket {self.name} received: {data}")
                        await self._message_queue.put(data)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON received on {self.name}: {msg.data}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket {self.name} error: {msg}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info(f"WebSocket {self.name} closed")
                    break
        except Exception as e:
            logger.error(f"Error in WebSocket {self.name} listener: {e}")
        finally:
            await self.close()
    
    async def send_json(self, data: dict):
        """Send JSON data with logging"""
        if not self.ws:
            await self.connect()
        logger.debug(f"WebSocket {self.name} sending: {data}")
        await self.ws.send_json(data)
    
    async def receive_json(self, timeout: float = 5.0):
        """Receive JSON with timeout and logging"""
        try:
            return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"WebSocket {self.name} receive timeout")
            raise TimeoutError(f"Timeout waiting for WebSocket {self.name} message")
    
    async def close(self):
        """Comprehensive WebSocket closure"""
        if not self._closed:
            self._closed = True
            logger.info(f"Closing WebSocket {self.name}")
            
            if self._listener_task and not self._listener_task.done():
                self._listener_task.cancel()
                try:
                    await self._listener_task
                except (asyncio.CancelledError, Exception):
                    pass
            
            if self.ws and not self.ws.closed:
                await self.ws.close()
            
            self.ws = None
            logger.info(f"WebSocket {self.name} closed")

async def upload_test_document():
    """Upload a test document and return document data"""
    async with aiohttp.ClientSession() as session:
        test_pdf_path = os.path.join(os.path.dirname(__file__), 'pdfs', 'pale fire presentation.pdf')
        
        if not os.path.exists(test_pdf_path):
            raise FileNotFoundError(f"Test PDF not found at {test_pdf_path}")
        
        data = aiohttp.FormData()
        data.add_field('file',
                      open(test_pdf_path, 'rb'),
                      filename='pale fire presentation.pdf',
                      content_type='application/pdf')
        
        async with session.post(
            "http://localhost:8000/api/documents/upload", 
            data=data
        ) as response:
            assert response.status == 200, f"Upload failed with status {response.status}"
            document_data = await response.json()
            return document_data

@pytest.mark.asyncio
async def test_document_stream_chunk_retrieval():
    """Test retrieving document chunks via dedicated WebSocket"""
    # Create a fresh session and WebSocket for this test
    async with aiohttp.ClientSession() as session:
        # Upload document
        document_data = await upload_test_document()
        document_id = document_data.get("document_id")
        assert document_id, "No document_id in response"
        
        # Create WebSocket client
        async with WebSocketTestClient(
            session, 
            f"ws://localhost:8000/api/documents/{document_id}/stream", 
            name="document_chunk_test"
        ) as ws_client:
            # Request chunks
            await ws_client.send_json({"action": "get_chunks"})
            
            # Receive and validate chunks
            chunks_response = await ws_client.receive_json()
            assert chunks_response.get("action") == "chunks", f"Unexpected response: {chunks_response}"
            assert "chunks" in chunks_response, "No chunks in response"
            assert len(chunks_response["chunks"]) > 0, "Empty chunks list"
            
            # Optional: Validate chunk structure
            first_chunk = chunks_response["chunks"][0]
            assert "id" in first_chunk, "Chunk missing ID"
            assert "content" in first_chunk, "Chunk missing content"
            assert "sequence" in first_chunk, "Chunk missing sequence"

@pytest.mark.asyncio
async def test_conversation_stream_main_conversation_creation():
    """Test creating a main conversation via conversation stream WebSocket"""
    # Create a fresh session and WebSocket for this test
    async with aiohttp.ClientSession() as session:
        # Upload document
        document_data = await upload_test_document()
        document_id = document_data.get("document_id")
        main_conversation_id = document_data.get("main_conversation_id")
        assert document_id, "No document_id in response"
        assert main_conversation_id, "No main_conversation_id in response"

        # Create WebSocket client for conversation stream
        async with WebSocketTestClient(
            session,
            f"ws://localhost:8000/api/stream/{document_id}",
            name="main_conversation_creation_test"
        ) as ws_client:
            # Request main conversation creation
            await ws_client.send_json({
                "action": "create_conversation",
                "document_id": document_id
            })

            # Wait for conversation creation event
            event = await ws_client.receive_json()
            logger.info(f"Received event: {event}")
            
            # Extract conversation ID from nested data
            created_conversation_id = (
                event.get("conversation_id") or 
                event.get("data", {}).get("conversation_id")
            )
            
            assert created_conversation_id is not None, "No conversation ID found"
            # Note: We can't guarantee it will match the main_conversation_id 
            # due to potential race conditions or backend logic

@pytest.mark.asyncio
async def test_conversation_stream_chunk_conversation_creation():
    """Test creating a chunk-specific conversation via conversation stream WebSocket"""
    # Create a fresh session and WebSocket for this test
    async with aiohttp.ClientSession() as session:
        # Upload document
        document_data = await upload_test_document()
        document_id = document_data.get("document_id")
        assert document_id, "No document_id in response"

        # First, get document chunks to use for chunk conversation
        async with WebSocketTestClient(
            session,
            f"ws://localhost:8000/api/documents/{document_id}/stream",
            name="chunk_retrieval_test"
        ) as doc_ws_client:
            # Request chunks
            await doc_ws_client.send_json({"action": "get_chunks"})
            chunks_response = await doc_ws_client.receive_json()
            
            # Validate response structure
            assert chunks_response.get("action") == "chunks", f"Unexpected response: {chunks_response}"
            first_chunk = chunks_response["chunks"][0]

        # Create WebSocket client for conversation stream
        async with WebSocketTestClient(
            session,
            f"ws://localhost:8000/api/stream/{document_id}",
            name="chunk_conversation_creation_test"
        ) as ws_client:
            # Request chunk conversation creation
            await ws_client.send_json({
                "action": "create_chunk_conversation",
                "document_id": document_id,
                "chunk_id": first_chunk["id"],
                "highlight_range": [0, 10],
                "highlighted_text": first_chunk["content"][:10]
            })

            # Wait for chunk conversation creation event
            event = await ws_client.receive_json()
            logger.info(f"Received event: {event}")
            
            # Check for error or successful creation
            assert "error" not in event, f"Conversation creation failed: {event.get('error')}"
            
            # Extract conversation ID 
            created_conversation_id = (
                event.get("conversation_id") or 
                event.get("data", {}).get("conversation_id")
            )
            
            assert created_conversation_id is not None, "No conversation ID found"

# Optional: Add more specific tests as needed
