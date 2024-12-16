from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Optional, Callable, Awaitable, Any
import asyncio
import json
import uuid
import psutil
import gc
from loguru import logger

from src.db.session import get_db
from src.services.document import DocumentService
from src.services.ai import AIService
from src.services.pdf import PDFService
from src.core.events import event_bus, Event
from src.core.logging import setup_logger
from src.core.websocket_manager import manager
from src.models.database import Document, DocumentChunk
from ..routes.auth import get_current_user, get_current_user_or_none, User
from ...services.auth import AuthService
from ...core.config import settings

logger = setup_logger(__name__)
pdf_service = PDFService()
router = APIRouter()

def log_memory_stats(context=""):
    process = psutil.Process()
    mem = process.memory_info()
    logger.info(f"[MEMORY DETAIL {context}] RSS: {mem.rss/1024/1024:.2f}MB, VMS: {mem.vms/1024/1024:.2f}MB")
    # Log request stats
    logger.info(f"[REQUESTS] Process memory info: {mem}")
    # Log object counts
    all_objects = gc.get_objects()
    request_count = sum(1 for obj in all_objects if str(type(obj).__name__) == 'Request')
    logger.info(f"[OBJECTS] Request objects: {request_count}")
    response_count = sum(1 for obj in all_objects if str(type(obj).__name__) == 'Response')
    logger.info(f"[OBJECTS] Response objects: {response_count}")

class WebSocketHandler:
    def __init__(self, websocket: WebSocket, document_id: str, user: Optional[User], db: AsyncSession):
        self.websocket = websocket
        self.document_id = document_id
        self.user = user
        self.db = db
        self.document_service = DocumentService(db)
        self.queue = asyncio.Queue()
        self.connection_id = None
        self.task = None
        self.event_types = [
            # Document creation events
            "document.main.create.completed", "document.main.create.error",
            "document.chunk.create.completed", "document.chunk.create.error",
            
            # Chunk events
            "document.chunk.list.completed", "document.chunk.list.error",
            
            # Metadata events
            "document.metadata.completed", "document.metadata.error",
            
            # Processing events
            "document.processing.completed", "document.processing.error",
            
            # Navigation events
            "document.navigation.completed", "document.navigation.error"
        ]

    async def connect(self):
        """Establish WebSocket connection and set up event listeners"""
        log_memory_stats("Before Document Stream Connect")
        try:
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
                scope="document",
                websocket=self.websocket
            )
            
            # Register which event types this connection cares about
            for event_type in self.event_types:
                await manager.register_listener(self.connection_id, event_type)
            
            # Start a background task to process events
            self.task = asyncio.create_task(self.process_events())
            
            log_memory_stats("After Document Stream Setup")
            return self.connection_id

        except Exception as e:
            logger.error(f"Error in document stream connect: {e}")
            raise

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
            logger.info(f"[WS] Sending event to client: type={event.type}")
            logger.debug(f"[WS] Event data: {event.data}")
            
            # Send the event data to the WebSocket client
            await self.websocket.send_json({
                "type": event.type,
                "data": event.data
            })
        except Exception as e:
            logger.error(f"[WS] Failed to send event to WebSocket: {e}")

    async def handle_list_chunks(self, data: Dict):
        """Handle request to list document chunks"""
        await event_bus.emit(Event(
            type="document.chunk.list.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={"document_id": self.document_id}
        ))

    async def handle_get_metadata(self, data: Dict):
        """Handle request to get document metadata"""
        logger.info(f"Requesting metadata for document {self.document_id}")
        await event_bus.emit(Event(
            type="document.metadata.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={"document_id": self.document_id}
        ))

    async def handle_navigate_chunks(self, data: Dict):
        """Handle chunk navigation request"""
        chunk_index = data.get("chunk_index")
        
        if chunk_index is None:
            await self.websocket.send_json({"error": "Missing chunk_index"})
            return

        await event_bus.emit(Event(
            type="document.navigation.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={
                "chunk_index": chunk_index,
                "navigation_type": "direct"
            }
        ))

    async def handle_process_document(self, data: Dict):
        """Handle document processing request"""
        await event_bus.emit(Event(
            type="document.processing.requested",
            document_id=self.document_id,
            connection_id=self.connection_id,
            data={"document_id": self.document_id}
        ))

    async def process_message(self, message: Dict):
        """Process incoming WebSocket message"""
        log_memory_stats("Before Process Document Message")
        try:
            msg_type = message.get("type")
            data = message.get("data", {})
            
            logger.info(f"[WS] Received message from client: type={msg_type}")
            logger.debug(f"[WS] Message data: {data}")

            if msg_type == "document.chunk.list":
                await self.handle_list_chunks(data)
            elif msg_type == "document.metadata":
                logger.info("Handling document metadata request")
                await self.handle_get_metadata(data)
            elif msg_type == "document.navigate":
                await self.handle_navigate_chunks(data)
            elif msg_type == "document.process":
                await self.handle_process_document(data)
            else:
                logger.warning(f"[WS] Unknown message type: {msg_type}")
                await self.websocket.send_json({"error": f"Unknown message type: {msg_type}"})
        finally:
            log_memory_stats("After Process Document Message")

    async def cleanup(self):
        """Cleanup resources when connection is closed"""
        log_memory_stats("Before Document Stream Cleanup")
        try:
            if self.task and not self.task.done():
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
            
            if self.connection_id:
                await manager.disconnect(self.connection_id, self.document_id, "document")
        finally:
            log_memory_stats("After Document Stream Cleanup")

@router.websocket("/documents/stream/{document_id}")
async def document_stream(
    websocket: WebSocket,
    document_id: str,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Stream document events via WebSocket"""
    log_memory_stats("Before Upload Document")
    try:
        # Get user (might be None for example documents)
        auth_service = AuthService(db)
        user = await get_current_user_or_none(token, document_id, auth_service)

        handler = WebSocketHandler(websocket, document_id, user, db)
        connection_id = await handler.connect()
        
        if connection_id:
            while True:
                message = await websocket.receive_json()
                await handler.process_message(message)
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for document {document_id}")
    finally:
        log_memory_stats("After Upload Complete")
        if 'handler' in locals():
            await handler.cleanup()

@router.get("/documents/examples")
async def list_example_documents(
    db: AsyncSession = Depends(get_db)
):
    """List all example documents. No authentication required."""
    log_memory_stats("Before Get Chunks")
    try:
        result = await db.execute(
            select(Document).where(Document.id.in_(settings.EXAMPLE_DOCUMENT_IDS))
        )
        example_docs = result.scalars().all()
        log_memory_stats("After Get Chunks")
        return [
            {
                "id": str(doc.id),
                "title": doc.title,
                "created_at": doc.created_at.isoformat()
            }
            for doc in example_docs
        ]
    except Exception as e:
        logger.error(f"Error getting chunks: {e}")
        raise

@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Upload and process a document
    
    Args:
        file (UploadFile): The PDF file to upload
        current_user (User): The authenticated user
        db (AsyncSession): Database session
    
    Returns:
        Dict[str, Any]: {
            "document_id": str,
            "current_chunk": {
                "id": str,
                "content": str,
                "sequence": int,
                "navigation": {
                    "prev": str | None,
                    "next": str | None
                }
            }
        }
    
    Raises:
        HTTPException: If document processing fails
    """
    log_memory_stats("Before Upload Document")
    try:
        # Process the PDF
        contents = await file.read()
        log_memory_stats("After File Read")
        
        result = await pdf_service.process_pdf(file)
        if not result.success:
            logger.error(f"PDF processing failed: {result.text}")
            raise HTTPException(status_code=400, detail=result.text)

        # Create document record with owner
        document = Document(
            title=result.display,
            content=result.text,
            owner_id=str(current_user.id),  # Associate with user
            status="ready",  # Document is ready immediately since we process synchronously
            meta_data={
                "topic_key": result.topicKey,
                "chunks_count": len(result.chunks)
            }
        )
        db.add(document)
        await db.flush()
        
        logger.info(f"Created document with ID: {document.id}")

        # Create chunks
        chunks = []
        for idx, chunk_content in enumerate(result.chunks):
            chunk = DocumentChunk(
                document_id=document.id,
                content=chunk_content,
                sequence=idx,
                meta_data={
                    "length": len(chunk_content),
                    "index": idx
                }
            )
            db.add(chunk)
            chunks.append(chunk)
        
        await db.flush()
        
        # Emit document creation event
        await event_bus.emit(Event(
            type="document.created",
            document_id=str(document.id),
            data={
                "filename": file.filename,
                "document_id": str(document.id),
                "owner_id": str(current_user.id)
            }
        ))

        # Prepare response
        navigation = {
            "prev": None,
            "next": str(chunks[1].id) if len(chunks) > 1 else None
        }

        log_memory_stats("After Document Process")
        return {
            "document_id": str(document.id),
            "current_chunk": {
                "id": str(chunks[0].id),
                "content": chunks[0].content,
                "sequence": chunks[0].sequence,
                "navigation": navigation
            }
        }
    
    except Exception as e:
        logger.error(f"Document upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        log_memory_stats("After Upload Complete")
        await db.commit()