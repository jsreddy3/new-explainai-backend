from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Optional, Callable, Awaitable, Any
import asyncio
import json
import uuid

from src.db.session import get_db
from src.services.document import DocumentService
from src.services.ai import AIService
from src.services.pdf import PDFService
from src.core.events import event_bus, Event
from src.core.logging import setup_logger
from src.core.websocket_manager import manager
from src.models.database import Document, DocumentChunk

logger = setup_logger(__name__)
pdf_service = PDFService()
router = APIRouter()

class WebSocketHandler:
    def __init__(self, websocket: WebSocket, document_id: str, db: AsyncSession):
        self.websocket = websocket
        self.document_id = document_id
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
        """Process incoming WebSocket message using match-case"""
        msg_type = message.get("type")
        data = message.get("data", {})

        match msg_type:
            case "document.chunk.list":
                await self.handle_list_chunks(data)
            case "document.metadata":
                await self.handle_get_metadata(data)
            case "document.navigate":
                await self.handle_navigate_chunks(data)
            case "document.process":
                await self.handle_process_document(data)
            case _:
                await self.websocket.send_json({"error": f"Unknown message type: {msg_type}"})

    async def cleanup(self):
        """Cleanup resources when connection is closed"""
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        if self.connection_id:
            await manager.disconnect(self.connection_id, self.document_id, "document")

@router.websocket("/documents/stream/{document_id}")
async def document_stream(
    websocket: WebSocket,
    document_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Stream document events via WebSocket
    
    Handles document-related events including:
    - Document creation events
    - Chunk events
    - Metadata events
    - Processing events
    - Navigation events
    
    Args:
        websocket (WebSocket): The WebSocket connection
        document_id (str): The ID of the document to stream events for
        db (AsyncSession): Database session
    """
    handler = WebSocketHandler(websocket, document_id, db)
    connection_id = None
    
    try:
        connection_id = await handler.connect()
        while True:
            message = await websocket.receive_json()
            await handler.process_message(message)
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for document {document_id}")
    finally:
        await handler.cleanup()

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Upload and process a document
    
    Args:
        file (UploadFile): The PDF file to upload
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
    try:
        # Process the PDF
        result = await pdf_service.process_pdf(file)
        if not result.success:
            logger.error(f"PDF processing failed: {result.text}")
            raise HTTPException(status_code=400, detail=result.text)

        # Create document record
        document = Document(
            title=result.display,
            content=result.text,
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
                "document_id": str(document.id)
            }
        ))

        # Prepare response
        navigation = {
            "prev": None,
            "next": str(chunks[1].id) if len(chunks) > 1 else None
        }

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
        await db.commit()