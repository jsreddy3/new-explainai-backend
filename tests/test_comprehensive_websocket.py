import pytest
import asyncio
import aiohttp
import json
import logging
import sys
import os
from pathlib import Path
from typing import List, Dict, Any
from src.core.events import event_bus, Event

# Ensure logs directory exists
logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Configure logging to show all messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),  # Print to console
        logging.FileHandler(os.path.join(logs_dir, 'websocket_test.log'), mode='w')  # Log to file
    ]
)

# Create a logger for this test module
logger = logging.getLogger(__name__)

# Set specific loggers to DEBUG
logging.getLogger('src.api.routes.conversation').setLevel(logging.DEBUG)
logging.getLogger('src.services.conversation').setLevel(logging.DEBUG)
logging.getLogger('src.core.events').setLevel(logging.DEBUG)

# Get the path to the test PDF
PDF_PATH = Path(__file__).parent / "pdfs" / "pale fire presentation.pdf"

async def conversation_created_listener(event: Event):
    logger.info(f"Test Listener: Conversation created - {event.data}")

async def message_created_listener(event: Event):
    logger.info(f"Test Listener: Message created - {event.data}")

async def message_token_listener(event: Event):
    logger.info(f"Test Listener: Message token received - {event.data}")

async def message_completed_listener(event: Event):
    logger.info(f"Test Listener: Message completed - {event.data}")

# Register listeners before the test
event_bus.on('conversation.created', conversation_created_listener)
event_bus.on('message.created', message_created_listener)
event_bus.on('message.token', message_token_listener)
event_bus.on('message.completed', message_completed_listener)

@pytest.mark.asyncio
async def test_comprehensive_websocket_workflow():
    """
    Comprehensive end-to-end WebSocket test that covers:
    1. Document upload
    2. Conversation creation
    3. Multiple conversation contexts
    4. Message streaming
    5. Context merging
    6. Multiple simultaneous conversations
    7. Detailed event tracking
    8. Error handling and robustness
    """
    async with aiohttp.ClientSession() as session:
        # 1. Upload Multiple Documents
        documents = []
        for pdf_file in [
            Path(__file__).parent / "pdfs" / "pale fire presentation.pdf", 
            Path(__file__).parent / "pdfs" / "pale fire presentation.pdf"  # Using same file twice for test
        ]:
            # Upload document
            data = aiohttp.FormData()
            data.add_field('file', 
                          open(pdf_file, 'rb'),
                          filename=pdf_file.name,
                          content_type='application/pdf')
            
            async with session.post('http://localhost:8000/api/documents/upload', data=data) as response:
                assert response.status == 200
                result = await response.json()
                documents.append(result)
        
        # Print out document details for debugging
        logger.info("Document upload results:")
        for doc in documents:
            logger.info(f"Document details: {doc}")
        
        # 2. Conversation Creation via WebSocket
        conversations = []
        websockets = []
        for doc in documents:
            logger.info(f"Processing document: {doc}")
            
            # Send conversation creation request via WebSocket
            ws = await session.ws_connect(
                'ws://localhost:8000/api/conversations/stream',
                timeout=30,
                heartbeat=30.0
            )
            
            # Create main conversation
            await ws.send_json({
                'type': 'create_conversation',
                'document_id': doc['document_id']
            })
            logger.info(f"Sent create_conversation for document {doc['document_id']}")
            
            # Wait for conversation creation response
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    response = json.loads(msg.data)
                    logger.info(f"Received WebSocket message: {response}")
                    
                    if response['type'] == 'conversation.created':
                        conversations.append({
                            'id': response['data']['conversation_id'],
                            'document_id': response['data']['document_id']
                        })
                        websockets.append({
                            'conversation_id': response['data']['conversation_id'], 
                            'document_id': response['data']['document_id'], 
                            'websocket': ws
                        })
                        break
                    elif response['type'] == 'error':
                        logger.error(f"Received error response: {response}")
                        raise Exception(f"WebSocket error: {response}")
                    
                    logger.warning(f"Unexpected message type: {response['type']}")
                    
                    # Prevent infinite loop
                    break
        
        # 3. Simultaneous Conversations and Message Streaming
        tasks = []
        for ws_info in websockets:
            task = asyncio.create_task(
                interact_with_conversation(
                    session, 
                    ws_info['websocket'], 
                    ws_info['conversation_id']
                )
            )
            tasks.append(task)
        
        # 4. Wait for all conversation interactions
        conversation_results = await asyncio.gather(*tasks)
        
        # 5. Comprehensive Result Validation
        for result in conversation_results:
            # Validate basic metrics
            assert result['messages_count'] > 0, "No messages processed"
            assert result['tokens_received'] > 0, "No tokens received"
            
            # Check for required event types
            required_events = {
                'message.created',  # User message
                'message.token',    # Streaming tokens
                'message.end'       # Message completion
            }
            missing_events = required_events - result['event_types']
            assert not missing_events, f"Missing events: {missing_events}"
            
            # Validate event sequence
            event_sequence = result['event_sequence']
            assert len(event_sequence) > 0, "No event sequence recorded"
            assert event_sequence[0]['type'] == 'message.created', "First event should be message creation"
            assert event_sequence[-1]['type'] == 'message.end', "Last event should be message end"
        
        # 6. Conversation Merging Test
        if len(conversations) > 1:
            # Send merge request via WebSocket
            merge_ws = websockets[0]['websocket']
            await merge_ws.send_json({
                'type': 'merge_conversation',
                'conversation_id': conversations[0]['id'],
                'highlight_conversation_id': conversations[1]['id']
            })
            
            # Wait for merge response
            async for msg in merge_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    merge_result = json.loads(msg.data)
                    
                    # Validate merge result
                    assert merge_result.get('type') == 'conversation.merged', "Incorrect merge event type"
                    assert 'data' in merge_result, "No merge data returned"
                    assert 'summary' in merge_result['data'], "No summary returned in merge result"
                    assert isinstance(merge_result['data']['summary'], str), "Summary should be a string"
                    assert 'insertion_index' in merge_result['data'], "No insertion index returned"
                    assert isinstance(merge_result['data']['insertion_index'], int), "Insertion index should be an integer"
                    
                    # Ensure we break after receiving merge response
                    break
        
        # 7. Stress Test: Multiple Rapid Interactions
        rapid_interaction_tasks = []
        for ws_info in websockets:
            for _ in range(3):  # Multiple rapid interactions
                task = asyncio.create_task(
                    rapid_interaction(
                        session, 
                        ws_info['websocket'], 
                        ws_info['conversation_id']
                    )
                )
                rapid_interaction_tasks.append(task)
        
        # Wait for rapid interactions
        rapid_results = await asyncio.gather(*rapid_interaction_tasks)
        for result in rapid_results:
            assert result['success'], "Rapid interaction failed"
        
        # 8. Close WebSockets
        for ws_info in websockets:
            await ws_info['websocket'].close()
        
        logger.info("Comprehensive WebSocket test completed successfully!")

async def interact_with_conversation(
    session, 
    ws, 
    conversation_id: str
) -> Dict[str, Any]:
    """Simulate comprehensive conversation interaction"""
    messages = [
        "What is this document about?", 
        "Can you elaborate on the key points?", 
        "Generate some questions about the content."
    ]
    
    results = {
        'messages_count': 0,
        'tokens_received': 0,
        'event_types': set(),
        'event_sequence': []
    }
    
    try:
        for message in messages:
            # Send user message via WebSocket
            await ws.send_json({
                'type': 'user_message', 
                'conversation_id': conversation_id,
                'content': message
            })
            
            # Collect events
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    event = json.loads(msg.data)
                    results['event_types'].add(event.get('type', ''))
                    results['event_sequence'].append(event)
                    
                    if event.get('type') == 'message.token':
                        results['tokens_received'] += 1
                    
                    if event.get('type') == 'message.end':
                        results['messages_count'] += 1
                        break
        
        return results
    
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Error in conversation interaction: {e}")
        raise

async def rapid_interaction(
    session, 
    ws, 
    conversation_id: str
) -> Dict[str, Any]:
    """Simulate rapid, back-to-back message interactions"""
    try:
        # Send a quick message
        async with session.post(
            f'http://localhost:8000/api/conversations/{conversation_id}/chat',
            json={"content": "Quick test message"}
        ) as response:
            assert response.status == 200
        
        # Wait for response
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                event = json.loads(msg.data)
                if event.get('type') == 'message.end':
                    break
        
        return {'success': True}
    
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Rapid interaction error: {e}")
        return {'success': False}

# Logging configuration for detailed test output
def pytest_configure(config):
    config.addinivalue_line(
        "markers", 
        "asyncio: mark test as an asynchronous coroutine"
    )
