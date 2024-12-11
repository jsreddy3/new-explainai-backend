import pytest
import asyncio
import aiohttp
import json
import logging
import uuid
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
    
    async def receive_json(self, timeout: float = 60.0):
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

# @pytest.mark.asyncio
# async def test_document_upload_and_websocket():
#     # Create session
#     async with aiohttp.ClientSession() as session:
#         # Upload document
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#         # Create and connect WebSocket client
#         client = WebSocketClient(session, f"ws://localhost:8000/api/documents/stream/{document_id}")
#         try:
#             await client.connect()
            
#             # Test metadata request
#             await client.send_json({
#                 "type": "document.metadata",
#                 "data": {"document_id": document_id}
#             })
            
#             response = await client.receive_json()
#             print("RESPONSE: ", response, "\n\n")
#             assert response["type"] == "document.metadata.completed"
        
#         finally:
#             await client.close()

# @pytest.mark.asyncio
# async def test_document_upload_and_websocket():
#     async with aiohttp.ClientSession() as session:
#         # Upload document
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#         # Create and test WebSocket
#         client = WebSocketClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}")
#         try:
#             await client.connect()
#             await client.send_json({
#                 "type": "document.metadata",
#                 "data": {"document_id": document_id}
#             })
#             response = await client.receive_json()
#             assert response["type"] == "document.metadata.completed"
#         finally:
#             await client.close()

# @pytest.mark.asyncio
# async def test_create_conversation():
#     async with aiohttp.ClientSession() as session:
#         # Upload document
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#         # Create and test WebSocket
#         client = WebSocketClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}")
#         try:
#             await client.connect()
#             await client.send_json({
#                 "type": "conversation.main.create",
#                 "data": {"document_id": document_id}
#             })
#             response = await client.receive_json()
#             assert response["type"] == "conversation.main.create.completed"
#             assert "conversation_id" in response["data"]
#         finally:
#             await client.close()

# @pytest.mark.asyncio
# async def test_main_conversation_message():
#     async with aiohttp.ClientSession() as session:
#         # Upload document
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#         # Create and test WebSocket
#         client = WebSocketClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}")
#         try:
#             await client.connect()
            
#             # Create conversation
#             await client.send_json({
#                 "type": "conversation.main.create",
#                 "data": {"document_id": document_id}
#             })
#             response = await client.receive_json()
#             print("CREATE RESPONSE:", response)
#             assert response["type"] == "conversation.main.create.completed"
#             conversation_id = response["data"]["conversation_id"]

#             # Send message
#             await client.send_json({
#                 "type": "conversation.message.send",
#                 "data": {
#                     "conversation_id": conversation_id,
#                     "content": "What is the main theme of Pale Fire?",
#                     "conversation_type": "main",
#                     "chunk_id": 0
#                 }
#             })
#             response = await client.receive_json()
#             print("MESSAGE RESPONSE:", response)
#             assert response["type"] == "conversation.message.send.completed"
#         finally:
#             await client.close()

# @pytest.mark.asyncio
# async def test_main_and_highlight_conversation():
#     async with aiohttp.ClientSession() as session:
#         # Upload document
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#         # Create and test WebSocket
#         client = WebSocketClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}")
#         try:
#             await client.connect()
            
#             # Create main conversation
#             await client.send_json({
#                 "type": "conversation.main.create",
#                 "data": {"document_id": document_id}
#             })
#             main_response = await client.receive_json()
#             assert main_response["type"] == "conversation.main.create.completed"
#             main_conversation_id = main_response["data"]["conversation_id"]
            
#             # Create highlight conversation
#             await client.send_json({
#                 "type": "conversation.chunk.create",
#                 "data": {
#                     "document_id": document_id,
#                     "chunk_id": "1",
#                     "highlight_text": "The theme of reality versus fiction"
#                 }
#             })
#             highlight_response = await client.receive_json()
#             assert highlight_response["type"] == "conversation.chunk.create.completed"
#             highlight_conversation_id = highlight_response["data"]["conversation_id"]
            
#             # Test both conversations
#             conversations = [
#                 (main_conversation_id, "main", 0, "Explain the main themes of the work."),
#                 (highlight_conversation_id, "highlight", None, "Why is this theme significant?")
#             ]
            
#             for conv_id, conv_type, chunk_id, message in conversations:
#                 await client.send_json({
#                     "type": "conversation.message.send",
#                     "data": {
#                         "conversation_id": conv_id,
#                         "content": message,
#                         "conversation_type": conv_type,
#                         "chunk_id": chunk_id
#                     }
#                 })
#                 response = await client.receive_json()
#                 assert response["type"] == "conversation.message.send.completed"
#         finally:
#             await client.close()

# @pytest.mark.asyncio
# async def test_interleaved_conversations_with_questions():
#     async with aiohttp.ClientSession() as session:
#         # Upload document
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#         client = WebSocketClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}")
#         try:
#             await client.connect()
            
#             # Create conversations
#             await client.send_json({
#                 "type": "conversation.main.create",
#                 "data": {"document_id": document_id}
#             })
#             main_response = await client.receive_json()
#             assert main_response["type"] == "conversation.main.create.completed"
#             main_id = main_response["data"]["conversation_id"]
            
#             await client.send_json({
#                 "type": "conversation.chunk.create",
#                 "data": {
#                     "document_id": document_id,
#                     "chunk_id": "1",
#                     "highlight_text": "The theme of reality versus fiction"
#                 }
#             })
#             highlight_response = await client.receive_json()
#             assert highlight_response["type"] == "conversation.chunk.create.completed"
#             highlight_id = highlight_response["data"]["conversation_id"]
            
#             # Test interleaved messages and questions
#             test_sequences = [
#                 (main_id, "Tell me about the structure.", "main", 0),
#                 (highlight_id, "Explain this theme.", "highlight", None),
#                 (main_id, "How does the author develop characters?", "main", 0)
#             ]
            
#             for conv_id, message, conv_type, chunk_id in test_sequences:
#                 # Send message
#                 await client.send_json({
#                     "type": "conversation.message.send",
#                     "data": {
#                         "conversation_id": conv_id,
#                         "content": message,
#                         "conversation_type": conv_type,
#                         "chunk_id": chunk_id
#                     }
#                 })
#                 response = await client.receive_json()
#                 assert response["type"] == "conversation.message.send.completed"
                
#                 # Generate questions
#                 await client.send_json({
#                     "type": "conversation.questions.generate",
#                     "data": {"conversation_id": conv_id}
#                 })
#                 response = await client.receive_json()
#                 assert response["type"] == "conversation.questions.generate.completed"
#         finally:
#             await client.close()

# @pytest.mark.asyncio
# async def test_repeated_question_generation():
#     async with aiohttp.ClientSession() as session:
#         # Upload document
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#         client = WebSocketClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}")
#         try:
#             await client.connect()
            
#             # Create conversation
#             await client.send_json({
#                 "type": "conversation.main.create",
#                 "data": {"document_id": document_id}
#             })
#             response = await client.receive_json()
#             assert response["type"] == "conversation.main.create.completed"
#             conversation_id = response["data"]["conversation_id"]
            
#             # Test messages and question generation
#             test_messages = [
#                 ("What is the relationship between Shade and Kinbote?", 0),
#                 ("How does the commentary relate to the poem?", 1),
#             ]
            
#             for message, chunk_id in test_messages:
#                 # Send message
#                 await client.send_json({
#                     "type": "conversation.message.send",
#                     "data": {
#                         "conversation_id": conversation_id,
#                         "content": message,
#                         "conversation_type": "main",
#                         "chunk_id": chunk_id
#                     }
#                 })
#                 response = await client.receive_json()
#                 assert response["type"] == "conversation.message.send.completed"
                
#                 # Generate questions
#                 await client.send_json({
#                     "type": "conversation.questions.generate",
#                     "data": {"conversation_id": conversation_id}
#                 })
#                 response = await client.receive_json()
#                 assert response["type"] == "conversation.questions.generate.completed"
#         finally:
#             await client.close()

# @pytest.mark.asyncio
# async def test_merge_conversation():
#     async with aiohttp.ClientSession() as session:
#         # Upload document
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#         client = WebSocketClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}")
#         try:
#             await client.connect()
            
#             # Create main conversation
#             await client.send_json({
#                 "type": "conversation.main.create",
#                 "data": {"document_id": document_id}
#             })
#             main_response = await client.receive_json()
#             assert main_response["type"] == "conversation.main.create.completed"
#             main_id = main_response["data"]["conversation_id"]
            
#             # Create highlight conversation
#             await client.send_json({
#                 "type": "conversation.chunk.create",
#                 "data": {
#                     "document_id": document_id,
#                     "chunk_id": "1",
#                     "highlight_text": "The theme of reality versus fiction"
#                 }
#             })
#             highlight_response = await client.receive_json()
#             assert highlight_response["type"] == "conversation.chunk.create.completed"
#             highlight_id = highlight_response["data"]["conversation_id"]
            
#             # Add message to highlight conversation
#             await client.send_json({
#                 "type": "conversation.message.send",
#                 "data": {
#                     "conversation_id": highlight_id,
#                     "content": "How does this theme manifest in the text?",
#                     "conversation_type": "highlight",
#                     "chunk_id": None
#                 }
#             })
#             msg_response = await client.receive_json()
#             assert msg_response["type"] == "conversation.message.send.completed"
            
#             # Merge conversations
#             await client.send_json({
#                 "type": "conversation.chunk.merge",
#                 "data": {
#                     "main_conversation_id": main_id,
#                     "highlight_conversation_id": highlight_id
#                 }
#             })
#             merge_response = await client.receive_json()
#             print(merge_response)
#             assert merge_response["type"] == "conversation.merge.completed"
#         finally:
#             await client.close()

@pytest.mark.asyncio
async def test_list_conversation_messages():
    async with aiohttp.ClientSession() as session:
        # Upload document
        with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename="test.pdf")
            async with session.post("http://localhost:8000/api/upload", data=data) as response:
                assert response.status == 200
                document_id = (await response.json())["document_id"]

        # Create and test WebSocket
        client = WebSocketClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}")
        try:
            await client.connect()
            
            # Create conversation
            await client.send_json({
                "type": "conversation.main.create",
                "data": {"document_id": document_id}
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.main.create.completed"
            conversation_id = response["data"]["conversation_id"]

            # Send first message
            await client.send_json({
                "type": "conversation.message.send",
                "data": {
                    "conversation_id": conversation_id,
                    "content": "What is the main theme of Pale Fire?",
                    "conversation_type": "main",
                    "chunk_id": 0
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.message.send.completed"
            
            # Send second message
            await client.send_json({
                "type": "conversation.message.send",
                "data": {
                    "conversation_id": conversation_id,
                    "content": "Can you elaborate on the relationship between Shade and Kinbote?",
                    "conversation_type": "main",
                    "chunk_id": 1
                }
            })
            response = await client.receive_json()
            assert response["type"] == "conversation.message.send.completed"

            # List all messages
            await client.send_json({
                "type": "conversation.messages.list",
                "data": {
                    "conversation_id": conversation_id
                }
            })
            response = await client.receive_json()
            print("List response:", response)
            assert response["type"] == "conversation.messages.completed"
            messages = response["data"]["messages"]
            
            # Verify messages
            assert len(messages) >= 4  # Should have at least 4 messages (2 user messages + 2 AI responses)
            assert any(msg["content"] == "What is the main theme of Pale Fire?" for msg in messages)
            assert any(msg["content"] == "Can you elaborate on the relationship between Shade and Kinbote?" for msg in messages)
            
            # Verify message structure
            for msg in messages:
                assert "id" in msg
                assert "role" in msg
                assert "content" in msg
                assert "created_at" in msg
                assert "conversation_id" in msg
                assert msg["conversation_id"] == conversation_id
                assert msg["role"] in ["user", "assistant", "system"]

        finally:
            await client.close()