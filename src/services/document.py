from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
import asyncio
from sqlalchemy.orm import sessionmaker
from asyncio import TimeoutError
from async_timeout import timeout

from ..models.database import Document, DocumentChunk
from ..core.logging import setup_logger
from ..core.events import Event, event_bus
from ..db.session import engine

logger = setup_logger(__name__)

class DocumentService:
    _instance = None
    _initialized = False

    def __new__(cls, db: AsyncSession = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db: AsyncSession = None):
        if not self._initialized and db is not None:
            self.AsyncSessionLocal = sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=True
            )
            # Task management
            self.task_queue = asyncio.Queue()
            self.active_tasks = set()
            # self.semaphore = asyncio.Semaphore(10)
            self.shutdown_event = asyncio.Event()
            
            # Start processor
            self.processor_task = asyncio.create_task(self._process_tasks())

            # Register event handlers with queue wrapper
            event_bus.on("document.chunk.list.requested", self._queue_task(self.handle_list_chunks))
            event_bus.on("document.metadata.requested", self._queue_task(self.handle_get_metadata))
            event_bus.on("document.navigation.requested", self._queue_task(self.handle_navigate_chunks))
            event_bus.on("document.processing.requested", self._queue_task(self.handle_process_document))

            self.__class__._initialized = True

    def _queue_task(self, handler):
        async def wrapper(event):
            if not self.shutdown_event.is_set():
                await self.task_queue.put((handler, event))
        return wrapper

    async def _process_tasks(self):
      while not self.shutdown_event.is_set():
          try:
              handler, event = await self.task_queue.get()
              task = asyncio.create_task(self._run_task(handler, event))
              self.active_tasks.add(task)
              task.add_done_callback(self._cleanup_task)
              self.task_queue.task_done()
          except asyncio.CancelledError:
              break
          except Exception as e:
              logger.error(f"Task processor error: {e}")
      
      await self._cleanup_all_tasks()

    async def _run_task(self, handler, event):
      try:
          async with timeout(25):  # Changed from asyncio.timeout(25)
              # async with self.semaphore:
                try:
                    async with self.AsyncSessionLocal() as db:
                        try:
                            logger.info(f"Running task: {event.type}")
                            await handler(event, db)
                        except Exception as e:
                            logger.error(f"Handler error: {e}")
                        finally:
                            await db.close()
                except Exception as e:
                    logger.error(f"DB session error: {e}")
                    # self.semaphore.release()
      except TimeoutError:  # Changed from asyncio.TimeoutError
          logger.error(f"Task timed out: {event.type}")
          # self.semaphore.release()
      except Exception as e:
          logger.error(f"Task execution error: {e}")
          # self.semaphore.release()

    async def handle_list_chunks(self, event: Event, db: AsyncSession):
        try:
            chunks = await self.get_document_chunks(event.document_id, db)
            await event_bus.emit(Event(
                type="document.chunk.list.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"chunks": chunks}
            ))
        except Exception as e:
            logger.error(f"Error listing chunks: {str(e)}")
            await event_bus.emit(Event(
                type="document.chunk.list.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def handle_get_metadata(self, event: Event, db: AsyncSession):
        logger.info("Handling request for metadata")
        try:
            document = await self.get_document(event.document_id, db)
            logger.info("Returning get metadata")
            await event_bus.emit(Event(
                type="document.metadata.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"document": document}
            ))
        except Exception as e:
            logger.error(f"Error getting metadata: {str(e)}")
            await event_bus.emit(Event(
                type="document.metadata.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def handle_navigate_chunks(self, event: Event, db: AsyncSession):
        try:
            chunk_data = await self.navigate_chunks(
                event.document_id,
                event.data["chunk_index"],
                db
            )
            await event_bus.emit(Event(
                type="document.navigation.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data=chunk_data
            ))
        except Exception as e:
            logger.error(f"Error navigating chunks: {str(e)}")
            await event_bus.emit(Event(
                type="document.navigation.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def handle_process_document(self, event: Event, db: AsyncSession):
        try:
            document = await self.get_document(event.document_id, db)
            if document:
                await event_bus.emit(Event(
                    type="document.processing.completed",
                    document_id=event.document_id,
                    connection_id=event.connection_id,
                    request_id=event.request_id,
                    data={"document": document}
                ))
            else:
                raise ValueError(f"Document not found: {event.document_id}")
        except Exception as e:
            logger.error(f"Error processing document: {str(e)}")
            await event_bus.emit(Event(
                type="document.processing.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def get_document(self, document_id: str, db: AsyncSession) -> Optional[Dict]:
        try:
            logger.info(f"Getting document: {document_id}")
            result = await db.execute(
                select(Document).where(Document.id == document_id)
            )
            document = result.scalar_one_or_none()
            
            if not document:
                logger.warning(f"Document not found: {document_id}")
                return None

            result = await db.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.sequence)
            )
            chunks = result.scalars().all()
            
            response = {
                "id": str(document.id),
                "title": document.title,
                "content": document.content,
                "created_at": str(document.created_at),
                "status": document.status,
                "owner_id": str(document.owner_id),
                "meta_data": document.meta_data,
                "chunks": [
                    {
                        "id": str(chunk.id),
                        "sequence": chunk.sequence,
                        "content": chunk.content,
                        "meta_data": chunk.meta_data
                    } for chunk in chunks
                ]
            }
            logger.info(f"Successfully got document: {document_id}.")
            logger.debug(f"Successfully got document: {document_id} with response: {response}")
            return response
        except Exception as e:
            logger.error(f"Error getting document: {str(e)}")
            return None

    async def get_document_chunks(self, document_id: str, db: AsyncSession) -> List[Dict]:
        try:
            result = await db.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.sequence)
            )
            chunks = result.scalars().all()
            
            return [
                {
                    "id": str(chunk.id),
                    "sequence": chunk.sequence,
                    "content": chunk.content,
                    "meta_data": chunk.meta_data
                } for chunk in chunks
            ]
        except Exception as e:
            logger.error(f"Error getting document chunks: {str(e)}")
            return []

    async def navigate_chunks(self, document_id: str, chunk_index: int, db: AsyncSession) -> Optional[Dict]:
        try:
            result = await db.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.sequence)
            )
            chunks = result.scalars().all()
            
            if not chunks:
                logger.warning(f"No chunks found for document {document_id}")
                return None
            
            if chunk_index < 0 or chunk_index >= len(chunks):
                logger.warning(f"Invalid chunk index {chunk_index}")
                return None
            
            current_chunk = chunks[chunk_index]
            
            return {
                "current": {
                    "id": str(current_chunk.id),
                    "content": current_chunk.content,
                    "sequence": chunk_index
                },
                "navigation": {
                    "prev": str(chunks[chunk_index - 1].id) if chunk_index > 0 else None,
                    "next": str(chunks[chunk_index + 1].id) if chunk_index < len(chunks) - 1 else None
                }
            }
        except Exception as e:
            logger.error(f"Error navigating chunks: {str(e)}")
            return None

    async def get_chunk_content(self, chunk_id: str, db: AsyncSession) -> Optional[str]:
        result = await db.execute(
            select(DocumentChunk).where(DocumentChunk.id == chunk_id)
        )
        chunk = result.scalar_one_or_none()
        return chunk.content if chunk else None

    async def list_documents(self, db: AsyncSession, skip: int = 0, limit: int = 10) -> List[Dict]:
        try:
            result = await db.execute(
                select(Document)
                .order_by(Document.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            documents = result.scalars().all()

            return [
                {
                    "id": str(doc.id),
                    "title": doc.title,
                    "created_at": str(doc.created_at),
                    "status": doc.status
                } for doc in documents
            ]
        except Exception as e:
            logger.error(f"Error listing documents: {str(e)}")
            return []

    async def shutdown(self):
        self.shutdown_event.set()
        
        if hasattr(self, 'processor_task'):
            self.processor_task.cancel()
            try:
                await self.processor_task
            except asyncio.CancelledError:
                pass

        await self._cleanup_all_tasks()

    def _cleanup_task(self, task):
      try:
          self.active_tasks.discard(task)
          if task.exception():
              logger.error(f"Task failed with: {task.exception()}")
              # self.semaphore.release()
      except Exception as e:
          logger.error(f"Error cleaning up task: {e}")

    async def _cleanup_all_tasks(self):
        logger.info(f"Cleaning up {len(self.active_tasks)} active tasks")
        try:
            # Cancel all active tasks
            for task in self.active_tasks:
                task.cancel()
            
            # Wait for all tasks to complete
            if self.active_tasks:
                await asyncio.gather(*self.active_tasks, return_exceptions=True)
            
            # Clear active tasks set
            self.active_tasks.clear()
            
            # Reset semaphore
            # while True:
            #     try:
            #         self.semaphore.release()
            #     except ValueError:
            #         break
        except Exception as e:
            logger.error(f"Error during task cleanup: {e}")