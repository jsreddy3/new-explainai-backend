from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Dict, Optional, Callable, Awaitable, Any
import asyncio
import json
import uuid

from src.db.session import get_db
from src.services.conversation import ConversationService
from src.services.document import DocumentService
from src.services.ai import AIService
from src.core.events import event_bus, Event
from src.core.websocket_manager import manager
from src.core.logging import setup_logger
from src.models.database import Conversation, Document
from ..routes.auth import get_current_user_or_none, User
from ...services.auth import AuthService
from src.core.config import settings

logger = setup_logger(__name__)
router = APIRouter()

class WebSocketHandler:
    def __init__(self, websocket: WebSocket, document_id: str, user: Optional[User], db: AsyncSession):
        self.websocket = websocket
        self.document_id = document_id
        self.user = user
        self.db = db
        self.conversation_service = ConversationService(db)
        self.document_service = DocumentService(db)
        self.connection_id = None
        self.task = None
        self.event_types = [
            # Chat events
            "chat.token", "chat.completed",

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
            "conversation.questions.regenerate.completed", "conversation.questions.regenerate.error",
            
            # Merge events
            "conversation.merge.completed", "conversation.merge.error",
            
            # Document events (needed for chunk operations)
            "document.chunk.list.completed", "document.chunk.list.error",
            "conversation.chunk.get.completed", "conversation.chunk.get.error"
        ]

    async def connect(self):
        """Establish WebSocket connection and set up event listeners"""
        # Verify document access
        document = await self.document_service.get_document(self.document_id, self.db)
        if not document:
            await self.websocket.close(code=4003)  # Document doesn't exist
            return None
            
        # Check if this is an example document
        if self.document_id in settings.EXAMPLE_DOCUMENT_IDS:
            # Allow access to example documents
            pass
        else:
            # For non-example documents, require authenticated user who owns the document
            if not self.user or document["owner_id"] != str(self.user.id):
                await self.websocket.close(code=4003)  # Not authorized
                return None
            
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
        if self.task:
            self.task.cancel()
            
        if self.connection_id:
            # Clean up demo conversations if this was a demo session
            if self.document_id in settings.EXAMPLE_DOCUMENT_IDS:
                await self.conversation_service.cleanup_demo_conversations(self.connection_id, self.db)
            
            # Disconnect from event bus
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
            response = {
                "type": event.type,
                "data": event.data
            }
            if event.request_id is not None:
                response["request_id"] = event.request_id
            await self.websocket.send_json(response)
        except Exception as e:
            logger.error(f"Failed to send event to WebSocket: {e}")

    async def handle_create_conversation(self, data: Dict):
        """Handle conversation creation request"""
        request_id = data.get("request_id")
        await event_bus.emit(Event(
            type="conversation.main.create.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            request_id=request_id,
            data={}
        ))

    async def handle_create_chunk_conversation(self, data: Dict):
        """Handle chunk conversation creation request"""
        chunk_id = data.get("chunk_id")
        highlight_range = data.get("highlight_range", {})
        highlight_text = data.get("highlight_text")
        request_id = data.get("request_id")

        if not chunk_id:
            await self.websocket.send_json({
                "error": "Missing required field: chunk_id",
                "request_id": request_id
            })
            return
        if not highlight_text:
            await self.websocket.send_json({
                "error": "Missing required field: highlight_text",
                "request_id": request_id
            })
            return

        await event_bus.emit(Event(
            type="conversation.chunk.create.requested",
            connection_id=self.connection_id,
            document_id=self.document_id,
            request_id=request_id,
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
        request_id = data.get("request_id")
        question_id = data.get("question_id", "")  # Optional parameter
        use_full_context = data.get("use_full_context", False)

        if not conversation_id:
            await self.websocket.send_json({
                "error": "Missing required field: conversation_id",
                "request_id": request_id
            })
            return
        if not content:
            await self.websocket.send_json({
                "error": "Missing required field: content",
                "request_id": request_id
            })
            return
        if not conversation_type:
            await self.websocket.send_json({
                "error": "Missing required field: conversation_type (must be 'main' or 'highlight')",
                "request_id": request_id
            })
            return
        if conversation_type == "main" and chunk_id is None:
            await self.websocket.send_json({
                "error": "Missing required field: chunk_id (required for main conversations)",
                "request_id": request_id
            })
            return
        if conversation_type not in ["main", "highlight"]:
            await self.websocket.send_json({
                "error": "Invalid conversation_type. Must be 'main' or 'highlight'",
                "request_id": request_id
            })
            return

        await event_bus.emit(Event(
            type="conversation.message.send.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            request_id=request_id,
            data={
                "conversation_id": conversation_id,
                "content": content,
                "role": role,
                "chunk_id": chunk_id,
                "conversation_type": conversation_type,
                "user": self.user,
                "question_id": question_id,
                "use_full_context": use_full_context
            }
        ))

    async def handle_generate_questions(self, data: Dict):
        """Handle question generation request"""
        conversation_id = data.get("conversation_id")
        count = data.get("count", 3)  # Optional
        conversation_type = data.get("conversation_type")
        chunk_id = data.get("chunk_id")  # Optional
        request_id = data.get("request_id")

        if conversation_type not in ["main", "highlight"]:
            await self.websocket.send_json({
                "error": "Invalid conversation_type. Must be 'main' or 'highlight'",
                "request_id": request_id
            })
            return
        if conversation_type == "main" and chunk_id is None:
            await self.websocket.send_json({
                "error": "Missing required field: chunk_id (required for main conversations)",
                "request_id": request_id
            })
            return

        if not conversation_id:
            await self.websocket.send_json({
                "error": "Missing required field: conversation_id",
                "request_id": request_id
            })
            return

        await event_bus.emit(Event(
            type="conversation.questions.generate.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            request_id=request_id,
            data={
                "conversation_id": conversation_id,
                "count": count,
                "chunk_id": chunk_id,
                "user": self.user
            }
        ))

    async def handle_list_conversations(self, data: Dict):
        """Handle conversations list request"""
        request_id = data.get("request_id")
        
        await event_bus.emit(Event(
            type="conversation.list.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            request_id=request_id,
            data={}
        ))

    async def handle_list_messages(self, data: Dict):
        """Handle messages list request"""
        conversation_id = data.get("conversation_id")
        request_id = data.get("request_id")

        if not conversation_id:
            await self.websocket.send_json({
                "error": "Missing required field: conversation_id",
                "request_id": request_id
            })
            return

        await event_bus.emit(Event(
            type="conversation.messages.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            request_id=request_id,
            data={
                "conversation_id": conversation_id
            }
        ))

    async def handle_merge_conversations(self, data: Dict):
        """Handle conversation merge request"""
        main_conversation_id = data.get("main_conversation_id")
        highlight_conversation_id = data.get("highlight_conversation_id")
        request_id = data.get("request_id")

        if not main_conversation_id:
            await self.websocket.send_json({
                "error": "Missing required field: main_conversation_id",
                "request_id": request_id
            })
            return
        if not highlight_conversation_id:
            await self.websocket.send_json({
                "error": "Missing required field: highlight_conversation_id",
                "request_id": request_id
            })
            return

        await event_bus.emit(Event(
            type="conversation.merge.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            request_id=request_id,
            data={
                "main_conversation_id": main_conversation_id,
                "highlight_conversation_id": highlight_conversation_id
            }
        ))

    async def handle_create_main_conversation(self, data: Dict):
        """Handle request to create main conversation"""
        # document_id is already available in self.document_id
        chunk_id = data.get("chunk_id") 
        request_id = data.get("request_id")
        
        await event_bus.emit(Event(
            type="conversation.main.create.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            request_id=request_id,
            data={
                "chunk_id": chunk_id
            }
        ))

    async def handle_list_chunks(self, data: Dict):
        """Handle request to list document chunks"""
        request_id = data.get("request_id")
        
        await event_bus.emit(Event(
            type="document.chunk.list.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            request_id=request_id,
            data={}
        ))

    async def handle_get_conversations_by_sequence(self, data: Dict):
        """Handle request to get conversations by chunk sequence number"""
        sequence_number = data.get("sequence_number")
        request_id = data.get("request_id")
        
        if sequence_number is None:  # explicitly check None since 0 is valid
            await self.websocket.send_json({
                "error": "Missing required field: sequence_number",
                "request_id": request_id
            })
            return
            
        await event_bus.emit(Event(
            type="conversation.chunk.get.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            request_id=request_id,
            data={
                "sequence_number": sequence_number
            }
        ))

    async def handle_list_questions(self, data: Dict):
      """Handle questions list request"""
      conversation_id = data.get("conversation_id")
      request_id = data.get("request_id")
      chunk_id = data.get("chunk_id")
      
      if not chunk_id or not conversation_id:
        await self.websocket.send_json({
            "error": f"Missing required field: {'chunk_id' if not chunk_id else 'conversation_id'}",
            "request_id": request_id,
            "chunk_id": chunk_id
        })
        return


      await event_bus.emit(Event(
          type="conversation.questions.list.requested",
          document_id=self.document_id,
          connection_id=self.connection_id,
          request_id=request_id,
          data={
              "conversation_id": conversation_id,
              "chunk_id": chunk_id
          }
      ))

    async def handle_regenerate_questions(self, data: Dict):
      """Handle question regeneration request"""
      conversation_id = data.get("conversation_id")
      conversation_type = data.get("conversation_type")
      chunk_id = data.get("chunk_id")
      request_id = data.get("request_id")

      if not conversation_id:
          await self.websocket.send_json({
              "error": "Missing required field: conversation_id",
              "request_id": request_id
          })
          return

      await event_bus.emit(Event(
          type="conversation.questions.regenerate.requested",
          document_id=self.document_id,
          connection_id=self.connection_id,
          request_id=request_id,
          data={
              "conversation_id": conversation_id,
              "conversation_type": conversation_type,
              "chunk_id": chunk_id
          }
      ))

    async def process_message(self, message: Dict):
        """Process incoming WebSocket message"""
        msg_type = message.get("type")
        data = message.get("data", {})
        request_id = data.get("request_id")  # Extract request_id from incoming message
        
        # Add request_id to data if not already present
        if request_id and "request_id" not in data:
            data["request_id"] = request_id

        if msg_type == "conversation.main.create":
            await self.handle_create_main_conversation(data)
        elif msg_type == "conversation.chunk.create":
            await self.handle_create_chunk_conversation(data)
        elif msg_type == "conversation.chunk.merge":
            await self.handle_merge_conversations(data)
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
        elif msg_type == "conversation.messages.get":
            await self.handle_list_messages(data)
        elif msg_type == "conversation.questions.list":
            await self.handle_list_questions(data)
        elif msg_type == "conversation.questions.regenerate":
            await self.handle_regenerate_questions(data)
        else:
            await self.websocket.send_json({
                "error": f"Unknown message type: {msg_type}",
                "request_id": request_id
            })
                
@router.websocket("/conversations/stream/{document_id}")
async def conversation_stream(
    websocket: WebSocket,
    document_id: str,
    token: Optional[str] = Query(None),
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
        token (str): JWT token for authentication
        db (AsyncSession): Database session
    """
    try:
        # Get user (might be None for example documents)
        auth_service = AuthService(db)
        user = await get_current_user_or_none(token, document_id, auth_service)
        logger.info("User: %s", user)

        handler = WebSocketHandler(websocket, document_id, user, db)
        connection_id = await handler.connect()
        
        if connection_id:
            while True:
                message = await websocket.receive_json()
                await handler.process_message(message)
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for document {document_id}")
    finally:
        await handler.cleanup()