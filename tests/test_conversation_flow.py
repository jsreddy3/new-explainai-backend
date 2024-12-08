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
    
    async def send_json(self, data: dict):
        """Send JSON data to the server"""
        if not self._closed and self.ws:
            await self.ws.send_json(data)
    
    async def receive_json(self, timeout: float = 20.0):
        """Receive JSON data from the server"""
        try:
            return await asyncio.wait_for(self._message_queue.get(), timeout)
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
async def test_complex_conversation_flow():
    """Test a complex conversation flow involving multiple chunks and highlight conversations"""
    async with aiohttp.ClientSession() as session:
        # Upload document
        test_pdf_path = os.path.join(os.path.dirname(__file__), 'pdfs', 'pale fire presentation.pdf')
        with open(test_pdf_path, "rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename="test.pdf")
            async with session.post("http://localhost:8000/api/upload", data=data) as response:
                assert response.status == 200
                document_id = (await response.json())["document_id"]

        # Create and connect WebSocket client
        client = WebSocketClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}")
        try:
            await client.connect()
            
            # Create main conversation
            await client.send_json({
                "type": "conversation.main.create",
                "data": {"document_id": document_id}
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.main.create.completed"
            main_conversation_id = response["data"]["conversation_id"]

            # Send messages about chunk 0
            await client.send_json({
                "type": "conversation.message.send",
                "data": {
                    "conversation_id": main_conversation_id,
                    "content": "What's the main topic discussed in this section?",
                    "chunk_id": "0"
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.message.send.completed"

            # Move to chunk 1
            await client.send_json({
                "type": "conversation.message.send",
                "data": {
                    "conversation_id": main_conversation_id,
                    "content": "What themes are introduced in this next section?",
                    "chunk_id": "1"
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.message.send.completed"

            # Create highlight conversation about an interesting phrase
            await client.send_json({
                "type": "conversation.chunk.create",
                "data": {
                    "document_id": document_id,
                    "chunk_id": "1",
                    "highlight_text": "The theme of reality versus fiction"
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.chunk.create.completed"
            highlight_conversation_id = response["data"]["conversation_id"]

            # Test getting conversations by sequence number
            await client.send_json({
                "type": "conversation.get.by.sequence",
                "data": {
                    "sequence_number": 1  # This should find our highlight conversation since it's on chunk 1
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.chunk.get.completed"
            conversations = response["data"]["conversations"]
            assert len(conversations) > 0
            assert any(conv["id"] == highlight_conversation_id for conv in conversations)
            assert all(conv["chunk_id"] == "1" for conv in conversations)

            # Converse in highlight conversation
            await client.send_json({
                "type": "conversation.message.send",
                "data": {
                    "conversation_id": highlight_conversation_id,
                    "content": "How does this theme of reality versus fiction manifest throughout the work?"
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.message.send.completed"

            await client.send_json({
                "type": "conversation.message.send",
                "data": {
                    "conversation_id": highlight_conversation_id,
                    "content": "Can you provide specific examples from the text?"
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.message.send.completed"

            # Back to main conversation
            await client.send_json({
                "type": "conversation.message.send",
                "data": {
                    "conversation_id": main_conversation_id,
                    "content": "How do these themes connect to the overall narrative structure?",
                    "chunk_id": "1"
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.message.send.completed"

            # Move back to chunk 0
            await client.send_json({
                "type": "conversation.message.send",
                "data": {
                    "conversation_id": main_conversation_id,
                    "content": "Let's return to the beginning. How does this connect to what we discussed earlier?",
                    "chunk_id": "0"
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.message.send.completed"

            # Merge highlight conversation
            await client.send_json({
                "type": "conversation.chunk.merge",
                "data": {
                    "main_conversation_id": main_conversation_id,
                    "highlight_conversation_id": highlight_conversation_id
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.merge.completed"

            # Final reflection
            await client.send_json({
                "type": "conversation.message.send",
                "data": {
                    "conversation_id": main_conversation_id,
                    "content": "Now that we've explored these themes in detail, how do they contribute to our understanding of the work as a whole?",
                    "chunk_id": "0"
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.message.send.completed"

        finally:
            await client.close()
