from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from typing import Dict, Optional, Callable, Awaitable
import asyncio
import json
import uuid

from src.db.session import get_db
from src.services.conversation import ConversationService
from src.services.ai import AIService
from src.core.events import event_bus, Event
from src.core.websocket_manager import manager
from src.core.logging import setup_logger

logger = setup_logger(__name__)
ai_service = AIService()
router = APIRouter()

class WebSocketHandler:
    def __init__(self, websocket: WebSocket, document_id: str, db: Session):
        self.websocket = websocket
        self.document_id = document_id
        self.db = db
        self.conversation_service = ConversationService(db)
        self.queue = asyncio.Queue()
        self.connection_id = None
        self.event_types = [
            # Creation events
            "conversation.main.create.completed", "conversation.main.create.error",
            "conversation.chunk.create.completed", "conversation.chunk.create.error",

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
            "conversation.merge.completed", "conversation.merge.error"
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
        asyncio.create_task(self.process_events())
        
        return self.connection_id

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
            data={"document_id": self.document_id}
        ))

    async def handle_create_chunk_conversation(self, data: Dict):
        """Handle chunk conversation creation request"""
        chunk_id = data.get("chunk_id")
        highlight_range = data.get("highlight_range")
        highlighted_text = data.get("highlighted_text")

        if not all([chunk_id, highlight_range, highlighted_text]):
            await self.websocket.send_json({"error": "Missing required parameters"})
            return

        await event_bus.emit(Event(
            type="conversation.chunk.create.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={
                "chunk_id": chunk_id,
                "highlight_range": highlight_range,
                "highlighted_text": highlighted_text
            }
        ))

    async def handle_send_message(self, data: Dict):
        """Handle message sending request"""
        conversation_id = data.get("conversation_id")
        content = data.get("content")
        role = data.get("role", "user")

        if not all([conversation_id, content]):
            await self.websocket.send_json({"error": "Missing conversation_id or content"})
            return

        await event_bus.emit(Event(
            type="conversation.message.send.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={
                "conversation_id": conversation_id,
                "content": content,
                "role": role
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
                "count": count
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

    async def process_message(self, message: Dict):
        """Process incoming WebSocket message using match-case"""
        action = message.get("action")
        data = message.get("data", {})

        match action:
            case "create_conversation":
                await self.handle_create_conversation(data)
            case "create_chunk_conversation":
                await self.handle_create_chunk_conversation(data)
            case "send_message":
                await self.handle_send_message(data)
            case "generate_questions":
                await self.handle_generate_questions(data)
            case "list_conversations":
                await self.handle_list_conversations(data)
            case "list_messages":
                await self.handle_list_messages(data)
            case "merge_conversations":
                await self.handle_merge_conversations(data)
            case _:
                await self.websocket.send_json({"error": f"Unknown action: {action}"})
                
@router.websocket("/stream/{document_id}")
async def conversation_stream(websocket: WebSocket, document_id: str, db: Session = Depends(get_db)):
    handler = WebSocketHandler(websocket, document_id, db)
    connection_id = None

    try:
        connection_id = await handler.connect()
        
        while True:
            try:
                message = await websocket.receive_json()
                await handler.process_message(message)
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON message"})
            except Exception as e:
                await websocket.send_json({"error": str(e)})
                break

    finally:
        if connection_id:
            await manager.disconnect(connection_id, document_id, scope="conversation")