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
    
    async def receive_json(self, timeout: float = 10.0):
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
async def test_document_upload_and_websocket():
    # Create session
    async with aiohttp.ClientSession() as session:
        # Upload document
        with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename="test.pdf")
            async with session.post("http://localhost:8000/api/upload", data=data) as response:
                assert response.status == 200
                document_id = (await response.json())["document_id"]

        # Create and connect WebSocket client
        client = WebSocketClient(session, f"ws://localhost:8000/api/documents/stream/{document_id}")
        try:
            await client.connect()
            
            # Test metadata request
            await client.send_json({
                "type": "document.metadata",
                "data": {"document_id": document_id}
            })
            
            response = await client.receive_json()
            assert response["type"] == "document.metadata.completed"
        
        finally:
            await client.close()

# @pytest.mark.asyncio
# async def test_create_conversation(websocket_client):
#     """Test 2: Create a conversation after document upload"""
#     # Create a document first
#     async with aiohttp.ClientSession() as session:
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#     # Connect to WebSocket
#     client = None
#     try:
#         async for ws_client in websocket_client:
#             client = ws_client
#             # Create main conversation
#             await client.send_json({
#                 "type": "conversation.main.create",
#                 "data": {
#                     "document_id": document_id
#                 }
#             })
            
#             response = await client.receive_json()
#             assert response["type"] == "conversation.main.create.completed"
#             assert "conversation_id" in response["data"]
#             break
    
#     finally:
#         if client:
#             await client.close()

# @pytest.mark.asyncio
# async def test_main_conversation_message(websocket_client):
#     """Test 3: Send and receive a message in main conversation"""
#     # Create a document first
#     async with aiohttp.ClientSession() as session:
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             form = aiohttp.FormData()
#             form.add_field("file", f)
#             async with session.post("http://localhost:8000/api/upload", data=form) as response:
#                 document = await response.json()
#                 document_id = document["document_id"]
    
#     # Connect websocket client
#     client = WebSocketClient(aiohttp.ClientSession(), f"ws://localhost:8000/api/conversations/stream/{document_id}")
#     try:
#         await client.connect()
        
#         # Create conversation
#         await client.send_json({
#             "type": "conversation.main.create",
#             "data": {
#                 "document_id": document_id
#             }
#         })
        
#         response = await client.receive_json()
#         print("CREATE RESPONSE:", response)
#         assert response["type"] == "conversation.main.create.completed"
#         conversation_id = response["data"]["conversation_id"]
        
#         # Send a message
#         await client.send_json({
#             "type": "conversation.message.send",
#             "data": {
#                 "conversation_id": conversation_id,
#                 "content": "What is the main theme of Pale Fire?"
#             }
#         })
        
#         # Wait for and collect response
#         response = await client.receive_json()
#         print("MESSAGE RESPONSE:", response)
#         assert response["type"] == "conversation.message.send.completed"
    
#     finally:
#         await client.close()

# @pytest.mark.asyncio
# async def test_main_and_highlight_conversation(websocket_client):
#     """Test 4: Test both main and highlight conversations"""
#     # Create a document first
#     async with aiohttp.ClientSession() as session:
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#     # Connect to WebSocket
#     client = None
#     try:
#         async for ws_client in websocket_client:
#             client = ws_client
#             # Create main conversation
#             await client.send_json({
#                 "type": "conversation.main.create",
#                 "data": {
#                     "document_id": document_id
#                 }
#             })
            
#             main_response = await client.receive_json()
#             assert main_response["type"] == "conversation.main.create.completed"
#             main_conversation_id = main_response["data"]["conversation_id"]
            
#             # Create highlight conversation
#             await client.send_json({
#                 "type": "conversation.chunk.create",
#                 "data": {
#                     "document_id": document_id,
#                     "chunk_id": "chunk_1",  # First chunk
#                     "highlight_text": "The theme of reality versus fiction"
#                 }
#             })
            
#             highlight_response = await client.receive_json()
#             assert highlight_response["type"] == "conversation.chunk.create.completed"
#             highlight_conversation_id = highlight_response["data"]["conversation_id"]
            
#             # Send messages to both conversations
#             await client.send_json({
#                 "type": "conversation.message.send",
#                 "data": {
#                     "conversation_id": main_conversation_id,
#                     "content": "Explain the main themes of the work."
#                 }
#             })
            
#             main_msg_response = await client.receive_json()
#             assert main_msg_response["type"] == "conversation.message.send.completed"
            
#             await client.send_json({
#                 "type": "conversation.message.send",
#                 "data": {
#                     "conversation_id": highlight_conversation_id,
#                     "content": "Why is this theme significant?"
#                 }
#             })
            
#             highlight_msg_response = await client.receive_json()
#             assert highlight_msg_response["type"] == "conversation.message.send.completed"
#             break
    
#     finally:
#         if client:
#             await client.close()

# @pytest.mark.asyncio
# async def test_interleaved_conversations_with_questions(websocket_client):
#     """Test 5: Interleaved conversations with question generation"""
#     # Create a document first
#     async with aiohttp.ClientSession() as session:
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#     # Connect to WebSocket
#     client = None
#     try:
#         async for ws_client in websocket_client:
#             client = ws_client
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
#                     "chunk_id": "chunk_1",
#                     "highlight_text": "The theme of reality versus fiction"
#                 }
#             })
#             highlight_response = await client.receive_json()
#             assert highlight_response["type"] == "conversation.chunk.create.completed"
#             highlight_id = highlight_response["data"]["conversation_id"]
            
#             # Interleave messages and generate questions
#             conversations = [(main_id, "Tell me about the structure."),
#                            (highlight_id, "Explain this theme."),
#                            (main_id, "How does the author develop characters?")]
            
#             for conv_id, message in conversations:
#                 await client.send_json({
#                     "type": "conversation.message.send",
#                     "data": {
#                         "conversation_id": conv_id,
#                         "content": message
#                     }
#                 })
#                 msg_response = await client.receive_json()
#                 assert msg_response["type"] == "conversation.message.send.completed"
                
#                 # Generate questions after each message
#                 await client.send_json({
#                     "type": "conversation.questions.generate",
#                     "data": {
#                         "conversation_id": conv_id
#                     }
#                 })
#                 questions_response = await client.receive_json()
#                 assert questions_response["type"] == "conversation.questions.generate.completed"
#             break
    
#     finally:
#         if client:
#             await client.close()

# @pytest.mark.asyncio
# async def test_repeated_question_generation(websocket_client):
#     """Test 6: Multiple rounds of conversation with question generation"""
#     # Create a document first
#     async with aiohttp.ClientSession() as session:
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#     # Connect to WebSocket
#     client = None
#     try:
#         async for ws_client in websocket_client:
#             client = ws_client
#             # Create main conversation
#             await client.send_json({
#                 "type": "conversation.main.create",
#                 "data": {"document_id": document_id}
#             })
#             response = await client.receive_json()
#             assert response["type"] == "conversation.main.create.completed"
#             conversation_id = response["data"]["conversation_id"]
            
#             # Multiple rounds of conversation and questions
#             messages = [
#                 "What is the relationship between Shade and Kinbote?",
#                 "How does the commentary relate to the poem?",
#                 "What role does unreliable narration play?"
#             ]
            
#             for message in messages:
#                 # Send message
#                 await client.send_json({
#                     "type": "conversation.message.send",
#                     "data": {
#                         "conversation_id": conversation_id,
#                         "content": message
#                     }
#                 })
#                 msg_response = await client.receive_json()
#                 assert msg_response["type"] == "conversation.message.send.completed"
                
#                 # Generate questions
#                 await client.send_json({
#                     "type": "conversation.questions.generate",
#                     "data": {
#                         "conversation_id": conversation_id
#                     }
#                 })
#                 questions_response = await client.receive_json()
#                 assert questions_response["type"] == "conversation.questions.generate.completed"
#             break
    
#     finally:
#         if client:
#             await client.close()

# @pytest.mark.asyncio
# async def test_merge_conversation(websocket_client):
#     """Test 7: Merge highlight conversation into main conversation"""
#     # Create a document first
#     async with aiohttp.ClientSession() as session:
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#     # Connect to WebSocket
#     client = None
#     try:
#         async for ws_client in websocket_client:
#             client = ws_client
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
#                     "chunk_id": "chunk_1",
#                     "highlight_text": "The theme of reality versus fiction"
#                 }
#             })
#             highlight_response = await client.receive_json()
#             assert highlight_response["type"] == "conversation.chunk.create.completed"
#             highlight_id = highlight_response["data"]["conversation_id"]
            
#             # Add some messages to highlight conversation
#             await client.send_json({
#                 "type": "conversation.message.send",
#                 "data": {
#                     "conversation_id": highlight_id,
#                     "content": "How does this theme manifest in the text?"
#                 }
#             })
#             msg_response = await client.receive_json()
#             assert msg_response["type"] == "conversation.message.send.completed"
            
#             # Merge conversations
#             await client.send_json({
#                 "type": "conversation.merge",
#                 "data": {
#                     "main_conversation_id": main_id,
#                     "highlight_conversation_id": highlight_id
#                 }
#             })
            
#             merge_response = await client.receive_json()
#             assert merge_response["type"] == "conversation.merge.completed"
#             break
    
#     finally:
#         if client:
#             await client.close()

# @pytest.mark.asyncio
# async def test_multiple_users(websocket_client):
#     """Test 8: Multiple users interacting with the same document"""
#     # Create a document first
#     async with aiohttp.ClientSession() as session:
#         with open("tests/pdfs/pale fire presentation.pdf", "rb") as f:
#             data = aiohttp.FormData()
#             data.add_field("file", f, filename="test.pdf")
#             async with session.post("http://localhost:8000/api/upload", data=data) as response:
#                 assert response.status == 200
#                 document_id = (await response.json())["document_id"]

#     # Create two websocket clients
#     session1 = aiohttp.ClientSession()
#     session2 = aiohttp.ClientSession()
#     client1 = None
#     client2 = None
    
#     try:
#         # Create two conversation clients
#         client1 = WebSocketClient(session1, f"ws://localhost:8000/api/conversations/stream/{document_id}")
#         client2 = WebSocketClient(session2, f"ws://localhost:8000/api/conversations/stream/{document_id}")
        
#         await client1.connect()
#         await client2.connect()
        
#         # Both users create conversations
#         await client1.send_json({
#             "type": "conversation.main.create",
#             "data": {"document_id": document_id}
#         })
#         response1 = await client1.receive_json()
#         assert response1["type"] == "conversation.main.create.completed"
#         conv_id1 = response1["data"]["conversation_id"]
        
#         await client2.send_json({
#             "type": "conversation.main.create",
#             "data": {"document_id": document_id}
#         })
#         response2 = await client2.receive_json()
#         assert response2["type"] == "conversation.main.create.completed"
#         conv_id2 = response2["data"]["conversation_id"]
        
#         # Both users send messages simultaneously
#         await asyncio.gather(
#             client1.send_json({
#                 "type": "conversation.message.send",
#                 "data": {
#                     "conversation_id": conv_id1,
#                     "content": "What is the significance of Zembla?"
#                 }
#             }),
#             client2.send_json({
#                 "type": "conversation.message.send",
#                 "data": {
#                     "conversation_id": conv_id2,
#                     "content": "How reliable is Kinbote as a narrator?"
#                 }
#             })
#         )
        
#         # Wait for both responses
#         responses = await asyncio.gather(
#             client1.receive_json(),
#             client2.receive_json()
#         )
#         assert responses[0]["type"] == "conversation.message.send.completed"
#         assert responses[1]["type"] == "conversation.message.send.completed"
            
#     finally:
#         if client1:
#             await client1.close()
#         if client2:
#             await client2.close()
#         await session1.close()
#         await session2.close()
