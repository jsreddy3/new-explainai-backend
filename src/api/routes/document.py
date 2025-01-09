from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Dict, Optional, Callable, Awaitable, Union, Dict, Any
import asyncio
import json
import uuid

from pydantic import BaseModel, HttpUrl

from src.db.session import get_db
from src.services.document import DocumentService
from src.services.ai import AIService
from src.services.pdf import PDFService
from src.core.events import event_bus, Event
from src.core.logging import setup_logger
from src.core.websocket_manager import manager
from src.models.database import Document, DocumentChunk, Conversation, Message, Question
from ..routes.auth import get_current_user, get_current_user_or_none, User
from ...services.auth import AuthService
from ...core.config import settings

logger = setup_logger(__name__)
pdf_service = PDFService()
router = APIRouter()

class URLUploadRequest(BaseModel):
    url: HttpUrl

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
            # logger.info(f"[WS] Sending event to client: type={event.type}")
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
        msg_type = message.get("type")
        data = message.get("data", {})
        request_id = data.get("request_id")  # Extract request_id from incoming message
        
        # Add request_id to data if not already present
        if request_id and "request_id" not in data:
            data["request_id"] = request_id

        if msg_type == "document.chunk.list":
            await event_bus.emit(Event(
                type="document.chunk.list.requested",
                document_id=self.document_id,
                connection_id=self.connection_id,
                request_id=request_id,
                data={}  # Empty dict for data
            ))
        elif msg_type == "document.metadata":
            await event_bus.emit(Event(
                type="document.metadata.requested",
                document_id=self.document_id,
                connection_id=self.connection_id,
                request_id=request_id,
                data={}  # Empty dict for data
            ))
        elif msg_type == "document.navigation":
            await event_bus.emit(Event(
                type="document.navigation.requested",
                document_id=self.document_id,
                connection_id=self.connection_id,
                request_id=request_id,
                data=data  # Pass through the navigation data
            ))
        elif msg_type == "document.processing":
            await event_bus.emit(Event(
                type="document.processing.requested",
                document_id=self.document_id,
                connection_id=self.connection_id,
                request_id=request_id,
                data={}  # Empty dict for data
            ))
        else:
            await self.websocket.send_json({
                "error": f"Unknown message type: {msg_type}",
                "request_id": request_id
            })

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
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Stream document events via WebSocket"""
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
        if 'handler' in locals():
            await handler.cleanup()

@router.get("/documents/examples")
async def list_example_documents(
    db: AsyncSession = Depends(get_db)
):
    """List all example documents. No authentication required."""
    print(settings.EXAMPLE_DOCUMENT_IDS)
    result = await db.execute(
        select(Document).where(Document.id.in_(settings.EXAMPLE_DOCUMENT_IDS))
    )
    example_docs = result.scalars().all()
    print(example_docs)
    return [
        {
            "id": str(doc.id),
            "title": doc.title,
            "created_at": doc.created_at.isoformat()
        }
        for doc in example_docs
    ]

@router.get("/documents/upload-progress/{filename}")
async def get_upload_progress(
    filename: str,
    current_user: User = Depends(get_current_user)
):
    """Get the progress of a document being uploaded"""
    tracking_key = f"{current_user.id}:{filename}"
    # progress = pdf_service.upload_progress.get(tracking_key, {})
    
    return {
        "filename": filename,
        "total_chunks": 0,
        "processed_chunks": 0,
        "is_complete": True,
    }

@router.post("/documents/upload/file")
async def upload_document_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Upload a document from a file"""
    try:
        # Get file content
        file_content = await file.read()
        
        # Process PDF content
        result, cost = await pdf_service.process_pdf(file, str(current_user.id))
        display_name = file.filename
        
        if not result.success:
            logger.error(f"Processing failed: {result.text}")
            raise HTTPException(status_code=400, detail=result.text)
        
        # Update user cost without starting a new transaction
        try:
            stmt = select(User).where(User.id == current_user.id)
            result_db = await db.execute(stmt)
            user = result_db.scalar_one()
            old_cost = user.user_cost
            user.user_cost += float(cost)
            await db.flush()
            
            # Upload file to S3
            document_service = DocumentService(db)
            s3_path = await document_service.aws_service.upload_file(
                file_content,
                f"{uuid.uuid4()}_{display_name}",
                content_type=file.content_type
            )
            
            # Create document with S3 path
            document = Document(
                title=display_name,
                content=result.text,
                owner_id=str(current_user.id),
                status="ready",
                s3_file_path=s3_path,  # Store S3 path
                meta_data={
                    "topic_key": result.topicKey,
                    "chunks_count": len(result.chunks),
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
                    "filename": display_name,
                    "document_id": str(document.id),
                    "owner_id": str(current_user.id),
                    # "source_type": "file"
                }
            ))

            # Prepare response
            navigation = {
                "prev": None,
                "next": str(chunks[1].id) if len(chunks) > 1 else None
            }

            await db.commit()  # Final commit for all changes
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
            logger.error(f"Database error: {e}")
            await db.rollback()
            raise HTTPException(status_code=500, detail="Database error")
            
    except Exception as e:
        logger.error(f"Document upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/documents/upload/url")
async def upload_document_url(
    url_data: URLUploadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Upload a document from a URL"""
    try:
        url_string = str(url_data.url)
        logger.info(f"Using process_url: {url_string}")  # Fixed string formatting
        result, cost = await pdf_service.process_url(url_string)
        display_name = result.display
        
        if not result.success:
            logger.error(f"Processing failed: {result.text}")
            raise HTTPException(status_code=400, detail=result.text)
        
        # Update user cost without starting a new transaction
        try:
            stmt = select(User).where(User.id == current_user.id)
            result_db = await db.execute(stmt)
            user = result_db.scalar_one()
            old_cost = user.user_cost
            user.user_cost += float(cost)
            await db.flush()
            
            # Create document
            document = Document(
                title=display_name,
                content=result.text,
                owner_id=str(current_user.id),
                status="ready",
                meta_data={
                    "topic_key": result.topicKey,
                    "chunks_count": len(result.chunks),
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
                    "filename": display_name,
                    "document_id": str(document.id),
                    "owner_id": str(current_user.id),
                    # "source_type": "url"
                }
            ))

            # Prepare response
            navigation = {
                "prev": None,
                "next": str(chunks[1].id) if len(chunks) > 1 else None
            }

            await db.commit()  # Final commit for all changes
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
            logger.error(f"Database error: {e}")
            await db.rollback()
            raise HTTPException(status_code=500, detail="Database error")
            
    except Exception as e:
        logger.error(f"Document upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents/{document_id}/pdf")
async def get_document_pdf(
    document_id: str,
    current_user: User = Depends(get_current_user_or_none),
    db: AsyncSession = Depends(get_db)
) -> bytes:
    """Get the PDF file for a document
    
    Args:
        document_id (str): The ID of the document
        current_user (User): The authenticated user (optional for example documents)
        db (AsyncSession): Database session
    
    Returns:
        bytes: The PDF file content
        
    Raises:
        HTTPException: If document not found, user not authorized, or file not found
    """
    try:
        # Get document
        query = select(Document).where(Document.id == document_id)
        result = await db.execute(query)
        document = result.scalar_one_or_none()
        
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
            
        # Check if this is an example document
        if document_id in settings.EXAMPLE_DOCUMENT_IDS:
            # Allow access to example documents
            pass
        else:
            # For non-example documents, require authenticated user who owns the document
            if not current_user or document.owner_id != str(current_user.id):
                raise HTTPException(
                    status_code=403,
                    detail="Not authorized to access this document"
                )
        
        # Check if document has S3 file path
        if not document.s3_file_path:
            raise HTTPException(
                status_code=404,
                detail="PDF file not found for this document"
            )
            
        # Get file from S3
        document_service = DocumentService(db)
        try:
            file_content = await document_service.aws_service.get_file(document.s3_file_path)
            return Response(
                content=file_content,
                media_type="application/pdf"
            )
        except Exception as e:
            logger.error(f"Error retrieving file from S3: {e}")
            raise HTTPException(
                status_code=500,
                detail="Error retrieving PDF file"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document PDF: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    """Delete a document if owned by the current user
    
    Args:
        document_id (str): The ID of the document to delete
        current_user (User): The authenticated user
        db (AsyncSession): Database session
    
    Returns:
        Dict[str, str]: {"status": "success"}
    
    Raises:
        HTTPException: If document not found or user not authorized
    """
    try:
        # Get document with owner info
        query = select(Document).where(Document.id == document_id)
        result = await db.execute(query)
        document = result.scalar_one_or_none()
        
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
            
        # Check ownership
        if document.owner_id != str(current_user.id):
            raise HTTPException(
                status_code=403, 
                detail="Not authorized to delete this document"
            )
            
        # Delete file from S3 if it exists
        if document.s3_file_path:
            document_service = DocumentService(db)
            try:
                await document_service.aws_service.delete_file(document.s3_file_path)
            except Exception as e:
                logger.error(f"Error deleting file from S3: {e}")
                # Continue with deletion even if S3 deletion fails
            
        # Delete associated questions first
        await db.execute(
            delete(Question).where(
                Question.conversation_id.in_(
                    select(Conversation.id).where(Conversation.document_id == document_id)
                )
            )
        )

        # Delete associated messages
        await db.execute(
            delete(Message).where(
                Message.conversation_id.in_(
                    select(Conversation.id).where(Conversation.document_id == document_id)
                )
            )
        )

        # Delete conversations
        await db.execute(
            delete(Conversation).where(Conversation.document_id == document_id)
        )

        # Delete document chunks
        await db.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )

        # Delete the document
        await db.execute(
            delete(Document).where(Document.id == document_id)
        )
                
        # Emit document deletion event
        await event_bus.emit(Event(
            type="document.deleted",
            document_id=document_id,
            data={
                "document_id": document_id,
                "owner_id": str(current_user.id)
            }
        ))
        
        await db.commit()
        return {"status": "success"}
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Document deletion error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))