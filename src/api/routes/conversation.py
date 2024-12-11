from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Dict, Optional, Callable, Awaitable, Any
import asyncio
import json
import uuid

from src.db.session import get_db
from src.services.conversation import ConversationService
from src.services.ai import AIService
from src.core.events import event_bus, Event
from src.core.websocket_manager import manager
from src.core.logging import setup_logger
from src.models.database import Conversation

logger = setup_logger(__name__)
router = APIRouter()

class WebSocketHandler:
    def __init__(self, websocket: WebSocket, document_id: str, db: AsyncSession):
        self.websocket = websocket
        self.document_id = document_id
        self.db = db
        self.conversation_service = ConversationService(db)
        self.queue = asyncio.Queue()
        self.connection_id = None
        self.task = None
        self.event_types = [
            # Creation events
            "conversation.main.create.completed", "conversation.main.create.error",
            "conversation.chunk.create.completed", "conversation.chunk.create.error",
            "conversation.chunk.merge.completed", "conversation.chunk.merge.error",
            
            # Message events
            "conversation.message.send.completed", "conversation.message.send.error",

            # List events
            "conversation.list.completed", "conversation.list.error",
            "conversation.chunk.list.completed", "conversation.chunk.list.error",
            "conversation.messages.completed", "conversation.messages.error",

            # Question events
            "conversation.questions.generate.completed", "conversation.questions.generate.error",
            "conversation.questions.list.completed", "conversation.questions.list.error",
            
            # Merge events
            "conversation.merge.completed", "conversation.merge.error",
            
            # Document events (needed for chunk operations)
            "document.chunk.list.completed", "document.chunk.list.error",
            "conversation.chunk.get.completed", "conversation.chunk.get.error"
        ]

    async def connect(self):
        """Establish WebSocket connection and set up event listeners"""
        # Generate a unique connection ID
        self.connection_id = str(uuid.uuid4())
        
        # Register with the WebSocket manager, which now handles event queuing
        await manager.connect(
            connection_id=self.connection_id,
            document_id=self.document_id,
            scope="conversation",
            websocket=self.websocket
        )
        
        # Register which event types this connection cares about
        for event_type in self.event_types:
            await manager.register_listener(self.connection_id, event_type)
        
        # Start a background task to process events
        self.task = asyncio.create_task(self.process_events())
        
        return self.connection_id

    async def cleanup(self):
        """Cleanup resources when connection is closed"""
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        if self.connection_id:
            await manager.disconnect(self.connection_id, self.document_id, "conversation")

    async def process_events(self):
        """Process events received from the WebSocket manager"""
        try:
            while True:
                event = await manager.get_events(self.connection_id)
                try:
                    await self.handle_event(event)
                except Exception as e:
                    logger.error(f"Error processing event {event.type}: {e}")
        except asyncio.CancelledError:
            pass

    async def handle_event(self, event: Event):
        """Handle different types of events"""
        try:
            # Send the event data to the WebSocket client
            await self.websocket.send_json({
                "type": event.type,
                "data": event.data
            })
        except Exception as e:
            logger.error(f"Failed to send event to WebSocket: {e}")

    async def handle_create_conversation(self, data: Dict):
        """Handle conversation creation request"""
        await event_bus.emit(Event(
            type="conversation.main.create.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={}
        ))

    async def handle_create_chunk_conversation(self, data: Dict):
        """Handle chunk conversation creation request"""
        chunk_id = data.get("chunk_id")
        highlight_range = data.get("highlight_range", {})
        highlight_text = data.get("highlight_text", "")

        if not chunk_id:
            await self.websocket.send_json({"error": "Missing chunk_id"})
            return

        await event_bus.emit(Event(
            type="conversation.chunk.create.requested",
            connection_id=self.connection_id,
            document_id=self.document_id,
            data={
                "chunk_id": chunk_id,
                "highlight_range": highlight_range,
                "highlight_text": highlight_text
            }
        ))

    async def handle_send_message(self, data: Dict):
        """Handle message sending request"""
        conversation_id = data.get("conversation_id")
        content = data.get("content")
        role = data.get("role", "user")
        chunk_id = data.get("chunk_id")
        conversation_type = data.get("conversation_type")

        if not all([conversation_id, content, conversation_type]):
            await self.websocket.send_json({"error": "Missing conversation_id or content or conversation_type (main or highlight)"})
            return
        if conversation_type == "main" and chunk_id is None:
            logger.info("Missing chunk_id: {chunk_id}, must be included for main conversation")
            await self.websocket.send_json({"error": "Missing chunk_id, must be included for main conversation"})
            return
        await event_bus.emit(Event(
            type="conversation.message.send.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={
                "conversation_id": conversation_id,
                "content": content,
                "role": role,
                "chunk_id": chunk_id,
                "conversation_type": conversation_type
            }
        ))

    async def handle_generate_questions(self, data: Dict):
        """Handle question generation request"""
        conversation_id = data.get("conversation_id")
        count = data.get("count", 3)

        if not conversation_id:
            await self.websocket.send_json({"error": "Missing conversation_id"})
            return

        await event_bus.emit(Event(
            type="conversation.questions.generate.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={
                "conversation_id": conversation_id,
                "count": count,
                "chunk_id": data.get("chunk_id")
            }
        ))

    async def handle_list_conversations(self, data: Dict):
        """Handle conversations list request"""
        await event_bus.emit(Event(
            type="conversation.list.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={}
        ))

    async def handle_list_messages(self, data: Dict):
        """Handle messages list request"""
        conversation_id = data.get("conversation_id")

        if not conversation_id:
            await self.websocket.send_json({"error": "Missing conversation_id"})
            return

        await event_bus.emit(Event(
            type="conversation.messages.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={
                "conversation_id": conversation_id
            }
        ))

    async def handle_merge_conversations(self, data: Dict):
        """Handle conversation merge request"""
        main_conversation_id = data.get("main_conversation_id")
        highlight_conversation_id = data.get("highlight_conversation_id")

        if not all([main_conversation_id, highlight_conversation_id]):
            await self.websocket.send_json({"error": "Missing main_conversation_id or highlight_conversation_id"})
            return

        await event_bus.emit(Event(
            type="conversation.merge.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={
                "main_conversation_id": main_conversation_id,
                "highlight_conversation_id": highlight_conversation_id
            }
        ))

    async def handle_create_main_conversation(self, data: Dict):
        """Handle request to create main conversation"""
        await event_bus.emit(Event(
            type="conversation.main.create.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={
                "chunk_id": data.get("chunk_id")
            }
        ))

    async def handle_list_chunks(self, data: Dict):
        """Handle request to list document chunks"""
        await event_bus.emit(Event(
            type="document.chunk.list.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={}
        ))

    async def handle_get_conversations_by_sequence(self, data: Dict):
        """Handle request to get conversations by chunk sequence number"""
        sequence_number = data.get("sequence_number")
        
        if sequence_number is None:  # explicitly check None since 0 is valid
            await self.websocket.send_json({"error": "Missing sequence_number"})
            return
            
        await event_bus.emit(Event(
            type="conversation.chunk.get.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={
                "sequence_number": sequence_number
            }
        ))

    async def process_message(self, message: Dict):
        """Process incoming WebSocket message"""
        print("Processing message: ", message)
        msg_type = message.get("type")
        data = message.get("data", {})

        if msg_type == "conversation.main.create":
            await self.handle_create_main_conversation(data)
        elif msg_type == "conversation.chunk.create":
            await self.handle_create_chunk_conversation(data)
        elif msg_type == "conversation.chunk.merge":
            await self.handle_merge_conversations({
                "main_conversation_id": data.get("main_conversation_id"),
                "highlight_conversation_id": data.get("highlight_conversation_id")
            })
        elif msg_type == "conversation.message.send":
            await self.handle_send_message(data)
        elif msg_type == "conversation.list":
            await self.handle_list_conversations(data)
        elif msg_type == "conversation.questions.generate":
            await self.handle_generate_questions(data)
        elif msg_type == "document.chunk.list":
            await self.handle_list_chunks(data)
        elif msg_type == "conversation.get.by.sequence":
            await self.handle_get_conversations_by_sequence(data)
        elif msg_type == "conversation.messages.list":
            await self.handle_list_messages(data)
        else:
            await self.websocket.send_json({"error": f"Unknown message type: {msg_type}"})
                
@router.websocket("/conversations/stream/{document_id}")
async def conversation_stream(
    websocket: WebSocket,
    document_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Stream conversation events via WebSocket
    
    Handles conversation-related events including:
    - Creation events
    - Message events
    - List events
    - Question generation events
    - Merge events
    
    Args:
        websocket (WebSocket): The WebSocket connection
        document_id (str): The ID of the document to stream events for
        db (AsyncSession): Database session
    """
    handler = WebSocketHandler(websocket, document_id, db)
    
    try:
        connection_id = await handler.connect()
        print("Connected with connection ID: ", connection_id)
        while True:
            message = await websocket.receive_json()
            await handler.process_message(message)
    except WebSocketDisconnect:
        print("WebSocket disconnected")
        logger.info(f"WebSocket disconnected for document {document_id}")
    finally:
        await handler.cleanup()