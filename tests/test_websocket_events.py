import pytest
import asyncio
import aiohttp
import json
import logging
from pathlib import Path
from fastapi.testclient import TestClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get the path to the test PDF
PDF_PATH = Path(__file__).parent / "pdfs" / "pale fire presentation.pdf"

@pytest.mark.asyncio
async def test_conversation_events():
    """Test real-time conversation events via WebSocket"""
    logger.info("Starting conversation events test")
    
    async with aiohttp.ClientSession() as session:
        # Upload document first
        logger.info("Uploading document")
        data = aiohttp.FormData()
        data.add_field('file', 
                      open(PDF_PATH, 'rb'),
                      filename='test.pdf',
                      content_type='application/pdf')
        
        async with session.post('http://localhost:8000/api/documents/upload', data=data) as response:
            assert response.status == 200
            result = await response.json()
            document_id = result['document_id']
            conversation_id = result['main_conversation_id']
            logger.info(f"Got document ID: {document_id}, conversation ID: {conversation_id}")
        
        # Connect to conversation WebSocket
        logger.info(f"Connecting to conversation WebSocket")
        async with session.ws_connect(
            f'ws://localhost:8000/api/conversations/stream?conversation_id={conversation_id}',
            timeout=30,
            heartbeat=30.0
        ) as ws:
            logger.info("WebSocket connected")
            
            # Send a message
            logger.info("Sending message")
            async with session.post(
                f'http://localhost:8000/api/conversations/{conversation_id}/chat',
                json={"content": "What is this document about?"}
            ) as response:
                assert response.status == 200
            
            # Wait for message events
            events = []
            required_events = {
                'message.created',  # User message created
                'message.created'   # AI response created
            }
            
            # Collect events with timeout
            try:
                while len(events) < len(required_events):
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=30.0)
                    logger.info(f"Received event: {msg}")
                    events.append(msg)
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for events")
                assert False, "Timeout waiting for events"
            
            # Verify we got all required events
            event_types = {event['type'] for event in events}
            assert event_types == required_events, f"Missing events. Got {event_types}, expected {required_events}"

@pytest.mark.asyncio
async def test_conversation_streaming():
    """Test conversation streaming via WebSocket"""
    # First upload a document to use for the conversation
    async with aiohttp.ClientSession() as session:
        # Upload document first
        data = aiohttp.FormData()
        data.add_field('file', 
                      open(PDF_PATH, 'rb'),
                      filename='pale fire presentation.pdf',
                      content_type='application/pdf')
        
        async with session.post('http://localhost:8000/api/documents/upload', data=data) as response:
            assert response.status == 200
            upload_result = await response.json()
            document_id = upload_result['document_id']
        
        # Wait a bit for document processing
        await asyncio.sleep(2)
        
        # Create conversation
        conversation_data = {
            "document_id": document_id,
            "title": "Test Conversation"
        }
        
        async with session.post(f'http://localhost:8000/api/documents/{document_id}/conversations', json=conversation_data) as response:
            print(f"Create conversation response: {await response.text()}")
            assert response.status == 200
            result = await response.json()
            conversation_id = result['id']
        
        # Connect to conversation WebSocket
        async with session.ws_connect(f'ws://localhost:8000/api/conversations/stream?conversation_id={conversation_id}') as ws:
            # Send a message
            message_data = {
                "type": "user_message",
                "content": "What is this document about?"
            }
            await ws.send_json(message_data)
            
            # Collect streaming tokens
            response_tokens = []
            try:
                while True:
                    msg = await ws.receive_json(timeout=30)
                    print(f"Received message: {msg}")
                    if msg['type'] == 'message.token':
                        response_tokens.append(msg['data']['token'])
                    elif msg['type'] == 'message.end':
                        break
            except asyncio.TimeoutError:
                print("Received tokens:", response_tokens)
                raise
            
            # Verify we got a reasonable response
            full_response = ''.join(response_tokens)
            assert len(full_response) > 0

@pytest.mark.asyncio
async def test_multiple_clients():
    """Test multiple clients receiving the same document events"""
    async with aiohttp.ClientSession() as session:
        # Upload a document first
        data = aiohttp.FormData()
        data.add_field('file', 
                      open(PDF_PATH, 'rb'),
                      filename='pale fire presentation.pdf',
                      content_type='application/pdf')
        
        async with session.post('http://localhost:8000/api/documents/upload', data=data) as response:
            assert response.status == 200
            result = await response.json()
            document_id = result['document_id']
        
        # Connect with two clients
        async with session.ws_connect(f'ws://localhost:8000/api/documents/{document_id}/stream') as ws1, \
                  session.ws_connect(f'ws://localhost:8000/api/documents/{document_id}/stream') as ws2:
            
            # Collect events from both clients
            events1, events2 = [], []
            
            try:
                for _ in range(2):  # Just check first couple of events
                    msg1 = await ws1.receive_json(timeout=30)
                    msg2 = await ws2.receive_json(timeout=30)
                    print(f"Client 1 received: {msg1}")
                    print(f"Client 2 received: {msg2}")
                    events1.append(msg1)
                    events2.append(msg2)
            except asyncio.TimeoutError:
                print("Events from client 1:", events1)
                print("Events from client 2:", events2)
                raise
            
            # Verify both clients received the same events
            assert events1 == events2
