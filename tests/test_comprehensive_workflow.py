import pytest
import asyncio
import aiohttp
import json
import logging
import os
import uuid
from typing import Dict, List

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class WebSocketTestClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, name: str):
        self.session = session
        self.url = url
        self.name = name
        self._ws = None

    async def __aenter__(self):
        self._ws = await self.session.ws_connect(self.url)
        logger.debug(f"{self.name}: Connected to {self.url}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._ws:
            await self._ws.close()
            logger.debug(f"{self.name}: Disconnected from {self.url}")

    async def send_json(self, data: Dict):
        await self._ws.send_json(data)
        logger.debug(f"{self.name}: Sent message: {data}")

    async def receive_json(self, timeout: float = 5.0) -> Dict:
        try:
            msg = await asyncio.wait_for(self._ws.receive_json(), timeout)
            logger.debug(f"{self.name}: Received message: {msg}")
            return msg
        except asyncio.TimeoutError:
            logger.error(f"{self.name}: Timeout waiting for message")
            raise

async def upload_test_document(session: aiohttp.ClientSession) -> Dict:
    """Upload a test document and return document data"""
    test_pdf_path = os.path.join(os.path.dirname(__file__), 'pdfs', 'pale fire presentation.pdf')
    
    if not os.path.exists(test_pdf_path):
        raise FileNotFoundError(f"Test PDF not found at {test_pdf_path}")
    
    data = aiohttp.FormData()
    data.add_field('file',
                  open(test_pdf_path, 'rb'),
                  filename='pale fire presentation.pdf',
                  content_type='application/pdf')
    
    async with session.post(
        "http://localhost:8000/api/upload",  
        data=data
    ) as response:
        assert response.status == 200, f"Upload failed with status {response.status}"
        document_data = await response.json()
        return document_data

@pytest.mark.asyncio
async def test_document_upload():
    """Test 1: Simple document upload"""
    async with aiohttp.ClientSession() as session:
        document_data = await upload_test_document(session)
        
        assert document_data["document_id"] is not None
        assert document_data["current_chunk"] is not None
        assert document_data["current_chunk"]["content"] is not None

@pytest.mark.asyncio
async def test_document_upload_and_websocket():
    """Test 2: Document upload and WebSocket connection"""
    async with aiohttp.ClientSession() as session:
        # Upload document
        document_data = await upload_test_document(session)
        document_id = document_data["document_id"]
        
        # Connect to document stream
        async with WebSocketTestClient(
            session,
            f"ws://localhost:8000/api/documents/stream/{document_id}",
            "doc_stream"
        ) as ws_doc:
            # Test connection by requesting chunk list
            await ws_doc.send_json({
                "type": "document.chunk.list",
                "data": {"document_id": document_id}
            })
            
            response = await ws_doc.receive_json()
            assert response["type"] == "document.chunk.list.completed"
            assert len(response["data"]["chunks"]) > 0

@pytest.mark.asyncio
async def test_document_upload_conversation_and_chat():
    """Test 3: Document upload, WebSocket connection, main conversation creation and chat"""
    async with aiohttp.ClientSession() as session:
        # Upload document
        document_data = await upload_test_document(session)
        document_id = document_data["document_id"]
        
        # Connect to conversation stream
        async with WebSocketTestClient(
            session,
            f"ws://localhost:8000/api/conversations/stream/{document_id}",
            "conv_stream"
        ) as ws_conv:
            # Create main conversation
            await ws_conv.send_json({
                "type": "conversation.main.create",
                "data": {
                    "document_id": document_id,
                    "chunk_id": document_data["current_chunk"]["id"]
                }
            })
            
            response = await ws_conv.receive_json()
            assert response["type"] == "conversation.main.create.completed"
            conversation_id = response["data"]["conversation_id"]
            
            # Send a test message
            await ws_conv.send_json({
                "type": "conversation.message.send",
                "data": {
                    "content": "What is this document about?",
                    "document_id": document_id,
                    "conversation_id": conversation_id
                }
            })
            
            # Wait for AI response
            response = await ws_conv.receive_json()
            assert response["type"] == "conversation.message.send.completed"
            assert "message" in response["data"]

@pytest.mark.asyncio
async def test_comprehensive_conversation_workflow():
    """Test 4: Complete workflow with main conversation, questions, and chunk conversation"""
    async with aiohttp.ClientSession() as session:
        # Upload document
        document_data = await upload_test_document(session)
        document_id = document_data["document_id"]
        
        # Connect to conversation stream
        async with WebSocketTestClient(
            session,
            f"ws://localhost:8000/api/conversations/stream/{document_id}",
            "conv_stream"
        ) as ws_conv:
            # Create main conversation
            await ws_conv.send_json({
                "type": "conversation.main.create",
                "data": {
                    "document_id": document_id,
                    "chunk_id": document_data["current_chunk"]["id"]
                }
            })
            
            response = await ws_conv.receive_json()
            assert response["type"] == "conversation.main.create.completed"
            main_conversation_id = response["data"]["conversation_id"]
            
            # Generate questions
            await ws_conv.send_json({
                "type": "conversation.questions.generate",
                "data": {
                    "document_id": document_id,
                    "conversation_id": main_conversation_id
                }
            })
            
            response = await ws_conv.receive_json()
            assert response["type"] == "conversation.questions.generate.completed"
            
            # Create chunk conversation
            chunk_id = document_data["current_chunk"]["id"]
            await ws_conv.send_json({
                "type": "conversation.chunk.create",
                "data": {
                    "document_id": document_id,
                    "chunk_id": chunk_id
                }
            })
            
            response = await ws_conv.receive_json()
            assert response["type"] == "conversation.chunk.create.completed"
            chunk_conversation_id = response["data"]["conversation_id"]
            
            # Generate questions for chunk conversation
            await ws_conv.send_json({
                "type": "conversation.questions.generate",
                "data": {
                    "document_id": document_id,
                    "conversation_id": chunk_conversation_id
                }
            })
            
            response = await ws_conv.receive_json()
            assert response["type"] == "conversation.questions.generate.completed"

@pytest.mark.asyncio
async def test_multiple_chunks_and_merge():
    """Test 5: Multiple chunk conversations and merge workflow"""
    async with aiohttp.ClientSession() as session:
        # Upload document
        document_data = await upload_test_document(session)
        document_id = document_data["document_id"]
        
        # Connect to conversation stream
        async with WebSocketTestClient(
            session,
            f"ws://localhost:8000/api/conversations/stream/{document_id}",
            "conv_stream"
        ) as ws_conv:
            # Create main conversation
            await ws_conv.send_json({
                "type": "conversation.main.create",
                "data": {
                    "document_id": document_id,
                    "chunk_id": document_data["current_chunk"]["id"]
                }
            })
            
            response = await ws_conv.receive_json()
            assert response["type"] == "conversation.main.create.completed"
            main_conversation_id = response["data"]["conversation_id"]
            
            # Get list of chunks
            await ws_conv.send_json({
                "type": "document.chunk.list",
                "data": {"document_id": document_id}
            })
            
            response = await ws_conv.receive_json()
            chunks = response["data"]["chunks"]
            
            # Create multiple chunk conversations
            chunk_conversations = []
            for chunk in chunks[:2]:  # Create conversations for first two chunks
                await ws_conv.send_json({
                    "type": "conversation.chunk.create",
                    "data": {
                        "document_id": document_id,
                        "chunk_id": chunk["id"]
                    }
                })
                
                response = await ws_conv.receive_json()
                assert response["type"] == "conversation.chunk.create.completed"
                chunk_conversations.append(response["data"]["conversation_id"])
            
            # Chat in each chunk conversation
            for conv_id in chunk_conversations:
                await ws_conv.send_json({
                    "type": "conversation.message.send",
                    "data": {
                        "content": "What are the key points in this section?",
                        "document_id": document_id,
                        "conversation_id": conv_id
                    }
                })
                
                response = await ws_conv.receive_json()
                assert response["type"] == "conversation.message.send.completed"
            
            # Merge chunk conversations
            await ws_conv.send_json({
                "type": "conversation.chunk.merge",
                "data": {
                    "document_id": document_id,
                    "conversation_ids": chunk_conversations
                }
            })
            
            response = await ws_conv.receive_json()
            assert response["type"] == "conversation.chunk.merge.completed"
            
            # Generate questions about merged conversation
            await ws_conv.send_json({
                "type": "conversation.questions.generate",
                "data": {
                    "document_id": document_id,
                    "conversation_id": main_conversation_id
                }
            })
            
            response = await ws_conv.receive_json()
            assert response["type"] == "conversation.questions.generate.completed"
            assert len(response["data"]["questions"]) > 0

@pytest.mark.asyncio
async def test_multi_user_workflow():
    """Test 6: Multiple users interacting with the same document simultaneously"""
    async with aiohttp.ClientSession() as session:
        # Upload document that will be shared between users
        document_data = await upload_test_document(session)
        document_id = document_data["document_id"]
        
        # Create multiple WebSocket connections simulating different users
        async with \
            WebSocketTestClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}", "user1") as ws_user1, \
            WebSocketTestClient(session, f"ws://localhost:8000/api/conversations/stream/{document_id}", "user2") as ws_user2, \
            WebSocketTestClient(session, f"ws://localhost:8000/api/documents/stream/{document_id}", "user3") as ws_doc:
            
            # User 1: Create main conversation
            await ws_user1.send_json({
                "type": "conversation.main.create",
                "data": {
                    "document_id": document_id,
                    "chunk_id": document_data["current_chunk"]["id"]
                }
            })
            response = await ws_user1.receive_json()
            assert response["type"] == "conversation.main.create.completed"
            main_conversation_id = response["data"]["conversation_id"]

            # User 2: Create chunk conversation
            await ws_user2.send_json({
                "type": "conversation.chunk.create",
                "data": {
                    "document_id": document_id,
                    "chunk_id": document_data["current_chunk"]["id"]
                }
            })
            response = await ws_user2.receive_json()
            assert response["type"] == "conversation.chunk.create.completed"
            chunk_conversation_id = response["data"]["conversation_id"]

            # User 3: Request chunk list while others are chatting
            await ws_doc.send_json({
                "type": "document.chunk.list",
                "data": {"document_id": document_id}
            })
            response = await ws_doc.receive_json()
            assert response["type"] == "document.chunk.list.completed"
            chunks = response["data"]["chunks"]

            # Simulate concurrent chat messages
            chat_tasks = []
            
            # User 1: Multiple messages in main conversation
            chat_tasks.append(asyncio.create_task(ws_user1.send_json({
                "type": "conversation.message.send",
                "data": {
                    "content": "What is the main topic of this document?",
                    "document_id": document_id,
                    "conversation_id": main_conversation_id
                }
            })))
            
            # User 2: Multiple messages in chunk conversation
            chat_tasks.append(asyncio.create_task(ws_user2.send_json({
                "type": "conversation.message.send",
                "data": {
                    "content": "Can you explain this section in detail?",
                    "document_id": document_id,
                    "conversation_id": chunk_conversation_id
                }
            })))

            # Wait for all chat messages to be sent
            await asyncio.gather(*chat_tasks)

            # Verify responses for both users
            response = await ws_user1.receive_json()
            assert response["type"] == "conversation.message.send.completed"
            assert "message" in response["data"]

            response = await ws_user2.receive_json()
            assert response["type"] == "conversation.message.send.completed"
            assert "message" in response["data"]

            # Generate questions concurrently for both conversations
            question_tasks = []
            
            # User 1: Generate questions for main conversation
            question_tasks.append(asyncio.create_task(ws_user1.send_json({
                "type": "conversation.questions.generate",
                "data": {
                    "document_id": document_id,
                    "conversation_id": main_conversation_id
                }
            })))
            
            # User 2: Generate questions for chunk conversation
            question_tasks.append(asyncio.create_task(ws_user2.send_json({
                "type": "conversation.questions.generate",
                "data": {
                    "document_id": document_id,
                    "conversation_id": chunk_conversation_id
                }
            })))

            # Wait for question generation to complete
            await asyncio.gather(*question_tasks)

            # Verify question generation responses
            response = await ws_user1.receive_json()
            assert response["type"] == "conversation.questions.generate.completed"

            response = await ws_user2.receive_json()
            assert response["type"] == "conversation.questions.generate.completed"

            # Finally, merge the conversations
            await ws_user1.send_json({
                "type": "conversation.chunk.merge",
                "data": {
                    "document_id": document_id,
                    "conversation_ids": [chunk_conversation_id]
                }
            })

            response = await ws_user1.receive_json()
            assert response["type"] == "conversation.chunk.merge.completed"
