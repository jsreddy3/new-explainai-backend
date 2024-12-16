import json
from typing import List, Optional, Dict, Tuple, AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
from sqlalchemy.orm import sessionmaker
from ..db.session import engine  # Add this if not already imported
from sqlalchemy import select, and_
import uuid
from datetime import datetime

from ..models.database import Document, DocumentChunk, Conversation, Message, Question, ConversationType
from ..core.events import event_bus, Event
from ..core.config import settings
from ..core.logging import setup_logger
from .ai import AIService
from ..prompts.manager import PromptManager

import psutil
import os
import gc

logger = setup_logger(__name__)

def get_memory_usage():
    """Get current memory usage of the process"""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024  # Convert to MB

def log_memory_stats():
    process = psutil.Process()
    mem = process.memory_info()
    logger.info(f"[MEMORY DETAIL] RSS: {mem.rss/1024/1024:.2f}MB, VMS: {mem.vms/1024/1024:.2f}MB")
    # Log event bus stats
    logger.info(f"[EVENT BUS] Queue size: {event_bus._event_queue.qsize()}")
    logger.info(f"[EVENT BUS] Number of handlers: {len(event_bus.listeners)}")
    # Log object counts
    all_objects = gc.get_objects()
    event_count = sum(1 for obj in all_objects if isinstance(obj, Event))
    logger.info(f"[OBJECTS] Event objects: {event_count}")
    handler_count = sum(1 for obj in all_objects if callable(obj) and hasattr(obj, '__name__'))
    logger.info(f"[OBJECTS] Event handlers: {handler_count}")
    # Log SQLAlchemy stats
    if hasattr(process, '_session_factory'):
        logger.info(f"[SQLALCHEMY] Session pool size: {len(process._session_factory.kw['pool'])}")
    # Log number of objects in memory
    logger.info(f"[OBJECTS] Total Python objects: {len(all_objects)}")
    # Log specific object counts
    from sqlalchemy.orm import Session
    session_count = sum(1 for obj in all_objects if isinstance(obj, Session))
    logger.info(f"[OBJECTS] SQLAlchemy Sessions: {session_count}")

class ConversationService:
    _instance = None
    _initialized = False
    HANDLED_EVENTS = [
        "conversation.main.create.requested",
        "conversation.chunk.create.requested", 
        "conversation.message.send.requested",
        "conversation.questions.generate.requested",
        "conversation.questions.list.requested",
        "conversation.merge.requested",
        "conversation.chunk.get.requested",
        "conversation.messages.requested",
        "conversation.list.requested"
    ]

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
            self.ai_service = AIService()  # Remove db dependency
            self.prompt_manager = PromptManager()
                
            # Task management
            self.task_queue = asyncio.Queue()
            self.active_tasks = set()
            self.semaphore = asyncio.Semaphore(10)
            self.shutdown_event = asyncio.Event()
                
            # Start processor
            self.processor_task = asyncio.create_task(self._process_tasks())

            # Store handlers so we can remove them later
            self._handlers = []
            
            # Register event handlers with queue wrapper
            self._register_handlers()
                
            self.__class__._initialized = True

    def _register_handlers(self):
        """Register event handlers."""
        # First remove any existing handlers for this instance
        for event_type in self.HANDLED_EVENTS:
            event_bus.listeners[event_type] = [h for h in event_bus.listeners[event_type] 
                                             if not hasattr(h, '__self__') or h.__self__ is not self]

        # Now register our handlers
        handlers = [
            ("conversation.main.create.requested", self.handle_create_main_conversation),
            ("conversation.chunk.create.requested", self.handle_create_chunk_conversation),
            ("conversation.message.send.requested", self.handle_send_message),
            ("conversation.questions.generate.requested", self.handle_generate_questions),
            ("conversation.questions.list.requested", self.handle_list_questions),
            ("conversation.merge.requested", self.handle_merge_conversations),
            ("conversation.chunk.get.requested", self.handle_get_conversations_by_sequence),
            ("conversation.messages.requested", self.handle_list_messages),
            ("conversation.list.requested", self.handle_list_conversations)
        ]
        
        for event_type, handler in handlers:
            wrapped = self._queue_task(handler)
            event_bus.on(event_type, wrapped)
            self._handlers.append((event_type, wrapped))

    def _queue_task(self, handler):
        async def wrapper(event):
            if not self.shutdown_event.is_set():
                await self.task_queue.put((handler, event))
        return wrapper

    async def _process_tasks(self):
        while not self.shutdown_event.is_set():
            try:
                print(f"[CONV SERVICE] Task queue size before get: {self.task_queue.qsize()}")
                handler, event = await self.task_queue.get()
                print(f"[CONV SERVICE] Task queue size after get: {self.task_queue.qsize()}")
                task = asyncio.create_task(self._run_task(handler, event))
                self.active_tasks.add(task)
                task.add_done_callback(self.active_tasks.discard)
                print(f"[CONV SERVICE] Active tasks count: {len(self.active_tasks)}")
                self.task_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Task processor error: {e}")

    async def _run_task(self, handler, event):
        """Run a task with proper session management and cleanup"""
        db = None
        session_id = None
        
        logger.info("=== MEMORY CHECKPOINT: Before Task Start ===")
        log_memory_stats()
        
        try:
            async with self.semaphore:
                db = self.AsyncSessionLocal()
                session_id = id(db)
                logger.info(f"[SESSION] Created session {session_id}")
                
                logger.info("=== MEMORY CHECKPOINT: After Session Creation ===")
                log_memory_stats()
                
                try:
                    await handler(event, db)
                    await db.commit()
                    logger.info("=== MEMORY CHECKPOINT: After Handler Execution ===")
                    log_memory_stats()
                except Exception as e:
                    logger.error(f"[SESSION] Error in handler for session {session_id}: {e}")
                    await db.rollback()
                    raise
                finally:
                    if db:
                        db.expire_all()  # Explicitly expire all objects
                        await db.close()
                        logger.info("=== MEMORY CHECKPOINT: After Session Close ===")
                        log_memory_stats()
        except Exception as e:
            logger.error(f"[SESSION] Task execution error for session {session_id}: {e}")
            if not isinstance(e, ValueError) or "already emitted" not in str(e):
                await self._emit_error_event(event, str(e))
        finally:
            if db and not db.is_active:
                db.expire_all()  # One more expire_all() for good measure
                await db.close()
            
            # Force garbage collection and log memory
            collected = gc.collect()
            logger.info(f"=== MEMORY CHECKPOINT: Final State ===")
            log_memory_stats()
            logger.info(f"[GC] Collected {collected} objects")
            logger.info(f"[MEMORY] Active tasks: {len(self.active_tasks)}")

    async def handle_create_main_conversation(self, event: Event, db: AsyncSession):
        try:
            document_id = event.document_id
            is_demo = document_id in settings.EXAMPLE_DOCUMENT_IDS
            
            # Get first chunk
            first_chunk = await self._get_first_chunk(document_id, db)
            
            # Create conversation with connection_id in meta_data for demo conversations
            meta_data = {}
            if is_demo:
                meta_data["connection_id"] = event.connection_id
                
            conversation = await self._create_conversation(
                document_id=document_id,
                conversation_type="main",
                chunk_id=0,
                db=db,
                meta_data=meta_data,
                is_demo=is_demo
            )

            # Create initial system message
            system_prompt = self.prompt_manager.create_main_system_prompt(first_chunk["content"])
            message_obj = await self._create_message(
                conversation["id"], 
                system_prompt,
                0,
                "system",
                db=db  # Pass db
            )
            
            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.main.create.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"conversation_id": str(conversation["id"])}
            ))
        except Exception as e:
            logger.error(f"Error creating main conversation: {e}")
            await event_bus.emit(Event(
                type="conversation.main.create.error",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_create_chunk_conversation(self, event: Event, db: AsyncSession):
        try:
            data = event.data
            document_id = event.document_id
            chunk_id = data["chunk_id"]
            highlight_text = data.get("highlight_text", "")
            is_demo = document_id in settings.EXAMPLE_DOCUMENT_IDS
            
            # Get chunk
            chunk = await self._get_chunk(document_id=document_id, chunk_id=chunk_id, db=db)  # Pass db
            
            # Create conversation meta_data
            meta_data = {
                "highlight_text": highlight_text,
                "highlight_range": data["highlight_range"]
            }
            if is_demo:
                meta_data["connection_id"] = event.connection_id
                
            # Create conversation
            conversation = await self._create_conversation(
                document_id=document_id,
                conversation_type="highlight",
                chunk_id=chunk_id,
                db=db,
                meta_data=meta_data,
                is_demo=is_demo
            )
            # Create initial system message
            system_prompt = self.prompt_manager.create_highlight_system_prompt(
                chunk_text=chunk["content"],
                highlight_text=highlight_text
            )
            await self._create_message(
                conversation["id"], 
                system_prompt, 
                chunk_id,
                "system",
                db=db  # Pass db
            )
            
            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.chunk.create.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"conversation_id": str(conversation["id"])}
            ))
        except Exception as e:
            logger.error(f"Error creating chunk conversation: {e}")
            await event_bus.emit(Event(
                type="conversation.chunk.create.error",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_send_message(self, event: Event, db: AsyncSession):
        session_id = id(db)
        logger.info(f"[SEND_MESSAGE] Starting message handler with session {session_id}")
        logger.info(f"[MEMORY] Before message handling - Memory usage: {get_memory_usage():.2f}MB")
        
        document_id = event.document_id
        try:
            conversation_id = event.data["conversation_id"]
            content = event.data["content"]
            chunk_id = event.data.get("chunk_id")  # Make chunk_id optional
                    
            # Get conversation with explicit error handling
            conversation = await self._get_conversation(conversation_id, db)
            if not conversation:
                raise ValueError(f"Conversation {conversation_id} not found")

            # Get chunk with explicit error handling
            chunk = None
            if conversation["chunk_id"]:
                chunk = await self._get_chunk(
                    document_id=conversation["document_id"], 
                    chunk_id=conversation["chunk_id"], 
                    db=db
                )
                if not chunk:
                    raise ValueError(f"Chunk {conversation['chunk_id']} not found")
            
            # 1. Store raw user message in DB
            message = await self._create_message(
                conversation["id"], 
                content, 
                chunk_id if chunk_id else conversation["chunk_id"],
                "user",
                db=db  # Pass db
            )
            await db.commit()
            await db.refresh(message)  # Ensure message is loaded
            
            # 2. Process the user message based on conversation type
            messages = []
            if conversation["type"] == "highlight":
                highlight_text = await self._get_highlight_text(conversation["id"], db)
                if not highlight_text:
                    raise ValueError("Highlight text not found")
                processed_content = self.prompt_manager.create_highlight_user_prompt(
                    user_message=content
                )
                messages = await self.get_conversation_messages(conversation["id"], db)
            else:
                processed_content = self.prompt_manager.create_main_user_prompt(
                    user_message=content
                )
                messages = await self._get_messages_with_chunk_switches(conversation, db)

            if not messages:
                raise ValueError("No messages found for conversation")

            messages[-1]["content"] = processed_content
            
            # 5. Send entire messages array to AI service
            response = await self.ai_service.chat(
                document_id,
                conversation_id,
                messages=messages,
                connection_id=event.connection_id
            )
                    
            # Add AI response in a new transaction
            ai_message = await self._create_message(
                conversation["id"], 
                response, 
                chunk_id if chunk_id else conversation["chunk_id"],
                "assistant",
                db=db  # Pass db
            )
            await db.commit()
            await db.refresh(ai_message)  # Ensure message is loaded
            
            # Expire all objects to free memory
            db.expire_all()
            
            await event_bus.emit(Event(
                type="conversation.message.send.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"message": response, "conversation_id": str(conversation["id"])}
            ))
                
        except Exception as e:
            logger.error(f"[SEND_MESSAGE] Error in session {session_id}: {e}")
            logger.info(f"[MEMORY] After error - Memory usage: {get_memory_usage():.2f}MB")
            await self._emit_error_event(event, str(e))

    async def handle_generate_questions(self, event: Event, db: AsyncSession):
        try:
            data = event.data
            conversation_id = data["conversation_id"]
            count = data.get("count", 3)
            
            # Get conversation and chunk
            conversation = await self._get_conversation(conversation_id, db)  # Pass db
            chunk = await self._get_chunk(document_id=conversation["document_id"], chunk_id=conversation["chunk_id"], db=db) if conversation["chunk_id"] else None  # Pass db
            
            # Get previous questions
            previous_questions = await self._get_previous_questions(conversation_id, db)  # Pass db
            
            # Generate questions based on conversation type
            if conversation["type"] == "highlight":
                highlight_text = await self._get_highlight_text(conversation_id, db)  # Pass db
                system_prompt, user_prompt = self.prompt_manager.create_highlight_question_prompts(
                    chunk_text=chunk["content"],
                    highlight_text=highlight_text,
                    count=count,
                    previous_questions=previous_questions
                )
            else:
                system_prompt, user_prompt = self.prompt_manager.create_main_question_prompts(
                    chunk_text=chunk["content"],
                    count=count,
                    previous_questions=previous_questions
                )
            
            questions = await self.ai_service.generate_questions(
                document_id=conversation["document_id"],
                conversation_id=conversation_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            
            # Store generated questions
            for question in questions[:count]:
                if question.strip():
                    question_entry = Question(
                        conversation_id=conversation_id,
                        content=question.strip(),
                        meta_data={"chunk_id": conversation["chunk_id"]}
                    )
                    db.add(question_entry)  # Use passed db
            
            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.questions.generate.completed",
                document_id=conversation["document_id"],
                connection_id=event.connection_id,
                data={
                    "conversation_id": conversation_id,
                    "questions": [q.strip() for q in questions[:count]]
                }
            ))
            
        except Exception as e:
            logger.error(f"Error generating questions: {e}")
            await event_bus.emit(Event(
                type="conversation.questions.generate.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_merge_conversations(self, event: Event, db: AsyncSession):
        try:
            data = event.data
            main_conversation_id = data["main_conversation_id"]
            highlight_conversation_id = data["highlight_conversation_id"]
            
            # Get conversations
            highlight_conversation = await self._get_conversation(highlight_conversation_id, db)
            chunk = await self._get_chunk(document_id=highlight_conversation["document_id"], chunk_id=highlight_conversation["chunk_id"], db=db)
            highlight_text = await self._get_highlight_text(highlight_conversation_id, db)
            
            # Format conversation history for summary
            conversation_history = await self._format_conversation_history(highlight_conversation_id, db)
            
            # Generate summary using AI service
            system_prompt, user_prompt = self.prompt_manager.create_summary_prompts(
                chunk_text=chunk["content"],
                highlight_text=highlight_text,
                conversation_history=conversation_history
            )
            
            summary = await self.ai_service.generate_summary(
                document_id=highlight_conversation["document_id"],
                conversation_id=highlight_conversation_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            
            # Get metadata from first message for chunk info
            first_message = await self._get_first_message(highlight_conversation_id, db)
            chunk_id = first_message.get("meta_data", {}).get("chunk_id") if first_message.get("meta_data") else None
            
            # Create summary message in main conversation
            await self._create_message(
                conversation_id=main_conversation_id,
                content=f"Summary of highlight discussion:\n{summary}",
                chunk_id=chunk_id,
                role="user",
                meta_data={"merged_from": highlight_conversation_id},
                db=db
            )
            
            # Add acknowledgment message
            await self._create_message(
                conversation_id=main_conversation_id,
                content="Acknowledged conversation merge",
                chunk_id=chunk_id,
                role="assistant",
                db=db
            )

            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.merge.completed",
                document_id=highlight_conversation["document_id"],
                connection_id=event.connection_id,
                data={
                    "main_conversation_id": main_conversation_id,
                    "highlight_conversation_id": highlight_conversation_id,
                    "summary": summary
                }
            ))
            
        except Exception as e:
            logger.error(f"Error merging conversations: {e}")
            await event_bus.emit(Event(
                type="conversation.merge.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_list_questions(self, event: Event, db: AsyncSession):
        try:
            conversation_id = event.data["conversation_id"]
            document_id = event.document_id
            
            questions = await self.get_conversation_questions(conversation_id, db)
            
            await event_bus.emit(Event(
                type="conversation.questions.list.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={
                    "conversation_id": conversation_id,
                    "questions": [q.strip() for q in questions]
                }
            ))
        except Exception as e:
            logger.error(f"Error listing questions: {e}")
            await event_bus.emit(Event(
                type="conversation.questions.list.error",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_get_conversations_by_sequence(self, event: Event, db: AsyncSession):
        """Get all conversations for a document that have a specific chunk sequence number"""
        try:
            document_id = event.document_id
            sequence_number = event.data["sequence_number"]
            
            # Query conversations with the given document_id and chunk_id (sequence number)
            stmt = select(Conversation).where(
                and_(
                    Conversation.document_id == document_id,
                    Conversation.chunk_id == sequence_number
                )
            )
            
            result = await db.execute(stmt)
            conversations = result.scalars().all()
            
            # Convert conversations to dict format
            conversations_data = {
                str(conv.id): {
                    "document_id": str(conv.document_id),
                    "chunk_id": conv.chunk_id,
                    "created_at": conv.created_at.isoformat(),
                    "highlight_text": conv.meta_data.get("highlight_text") if conv.meta_data else ""
                }
                for conv in conversations
            }
            
            # Emit success event
            await event_bus.emit(Event(
                type="conversation.chunk.get.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"conversations": conversations_data}
            ))
            
        except Exception as e:
            logger.error(f"Error getting conversations by sequence: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.chunk.get.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_list_conversations(self, event: Event, db: AsyncSession):
        """List all conversations for a document"""
        try:
            document_id = event.document_id
            is_demo = document_id in settings.EXAMPLE_DOCUMENT_IDS
            
            # Build query
            query = select(Conversation).where(
                Conversation.document_id == document_id
            )
            
            # For demo documents, only show conversations for this connection
            if is_demo:
                query = query.where(
                    and_(
                        Conversation.is_demo == True,
                        Conversation.meta_data['connection_id'].astext == event.connection_id
                    )
                )
            
            result = await db.execute(query)
            conversations = result.scalars().all()
            
            # Convert conversations to dict format with ID as key
            conversations_data = {
                str(conv.id): {
                    "document_id": str(conv.document_id),
                    "chunk_id": conv.chunk_id,
                    "created_at": conv.created_at.isoformat(),
                    "highlight_text": conv.meta_data.get("highlight_text") if conv.meta_data else ""
                }
                for conv in conversations
            }

            await event_bus.emit(Event(
                type="conversation.list.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"conversations": conversations_data}
            ))
            
        except Exception as e:
            logger.error(f"Error listing conversations: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.list.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_list_messages(self, event: Event, db: AsyncSession):
        """Handle request to list messages for a conversation
        
        Args:
            event: Event containing conversation_id
            db: Database session
        """
        try:
            conversation_id = event.data.get("conversation_id")
            if not conversation_id:
                raise ValueError("Missing conversation_id")

            # Query messages for the conversation, ordered by timestamp
            stmt = select(Message).where(
                Message.conversation_id == conversation_id
            ).order_by(Message.created_at)
            
            result = await db.execute(stmt)
            messages = result.scalars().all()
            
            # Convert messages to dict format
            message_list = [{
                "id": str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at.isoformat(),
                "conversation_id": str(msg.conversation_id)
            } for msg in messages]
            
            # Emit completion event with messages
            await event_bus.emit(Event(
                type="conversation.messages.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"message": message_list, "conversation_id": str(conversation_id)}
            ))
            
        except Exception as e:
            logger.error(f"Error listing messages: {e}")
            # Emit error event
            await event_bus.emit(Event(
                type="conversation.messages.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={
                    "conversation_id": conversation_id,
                    "error": str(e)
                }
            ))

    async def _get_messages_with_chunk_switches(self, conversation: Dict, db: AsyncSession) -> List[Dict]:
        messages = await self.get_conversation_messages(conversation["id"], db)
        
        if conversation["type"] != "main":
            return messages
            
        # First pass: Create basic structure with switches but no chunk content
        processed_messages = []
        last_chunk_id = None
        system_message = None
        chunk_switches = []  # Track all switch messages for second pass
        
        # First find and add system message if it exists
        for msg in messages:
            if msg.get("role") == "system":
                system_message = msg
                break
                
        if system_message:
            processed_messages.append(system_message)
        
        # Process remaining messages
        for msg in messages:
            if msg.get("role") == "system":
                continue
                
            current_chunk_id = msg.get("chunk_id", "")
            
            if current_chunk_id and current_chunk_id != last_chunk_id:
                switch_msg = {
                    "role": "user",
                    "content": f"<switched to chunk ID {current_chunk_id}>"
                }
                ack_msg = {
                    "role": "assistant", 
                    "content": f"<acknowledged switch to chunk ID {current_chunk_id}>"
                }
                
                chunk_switches.append((len(processed_messages), current_chunk_id, switch_msg))
                processed_messages.append(switch_msg)
                processed_messages.append(ack_msg)
            
            processed_messages.append(msg)
            last_chunk_id = current_chunk_id

        # Second pass: Add chunk content only to the most recent switch for each chunk ID
        seen_chunks = set()
        for idx, chunk_id, switch_msg in reversed(chunk_switches):
            if chunk_id not in seen_chunks:
                chunk = await self._get_chunk(document_id=conversation["document_id"], chunk_id=chunk_id, db=db)
                if chunk:
                    processed_messages[idx]["content"] += f", chunkText: {chunk['content']}"
                seen_chunks.add(chunk_id)

        return processed_messages

    async def _get_current_chunk_id(self, conversation: Dict, db: AsyncSession) -> Optional[str]:
        if conversation["type"] != "main":
            return None
            
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation["id"])
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_message = result.scalar_one_or_none()
        
        if last_message and last_message.meta_data:
            return last_message.meta_data.get("chunk_id")
        return None

    async def _format_conversation_history(self, conversation_id: str, db: AsyncSession) -> str:
        messages = await self.get_conversation_messages(conversation_id=conversation_id, db=db)
        return "\n".join([
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in messages
        ])

    async def _create_message(
        self,
        conversation_id: str,
        content: str,
        chunk_id: str,
        role: str = "user",
        meta_data: Optional[Dict] = None,
        db: AsyncSession = None
    ) -> Message:
        logger.info(f"[DB] Creating message for conversation {conversation_id}")
        try:
            message = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                content=content,
                chunk_id=chunk_id,
                role=role,
                created_at=datetime.utcnow()
            )
            db.add(message)
            logger.info(f"[DB] Added message to session")
            return message
        except Exception as e:
            logger.error(f"[DB] Error creating message: {e}")
            raise

    async def get_conversation_messages(
        self, 
        conversation_id: str,
        db: AsyncSession,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .offset(offset)
            .limit(limit)
        )
        messages = []
        for msg in result.scalars().all():
            messages.append(msg.to_dict())
        return messages

    async def get_conversation_questions(
        self,
        conversation_id: str,
        db: AsyncSession
    ) -> List[Dict]:
        result = await db.execute(
            select(Question)
            .where(Question.conversation_id == conversation_id)
            .order_by(Question.created_at)
        )
        return [q.to_dict() for q in result.scalars().all()]

    async def _get_conversation(self, conversation_id: str, db: AsyncSession) -> Dict:
        logger.info(f"[DB] Getting conversation {conversation_id}")
        conversation = await db.get(Conversation, conversation_id)
        if conversation:
            logger.info(f"[DB] Found conversation {conversation_id}")
            return conversation.to_dict()
        logger.error(f"[DB] Conversation {conversation_id} not found")
        return None

    async def _get_previous_questions(self, conversation_id: str, db: AsyncSession) -> List[Dict]:
        result = await db.execute(
            select(Question)
            .where(Question.conversation_id == conversation_id)
            .order_by(Question.created_at)
        )
        return [q.to_dict() for q in result.scalars().all()]

    async def _get_highlight_text(self, conversation_id: str, db: AsyncSession) -> str:
        conversation = await self._get_conversation(conversation_id, db)
        return conversation.get("meta_data", {}).get("highlight_text", "")

    async def _get_first_message(self, conversation_id: str, db: AsyncSession) -> Dict:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .limit(1)
        )
        return result.scalar_one().to_dict()

    async def _get_first_chunk(self, document_id: str, db: AsyncSession) -> Dict:
        result = await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.sequence)
            .limit(1)
        )
        chunk = result.scalar_one()
        return chunk.to_dict() if chunk else None

    async def _get_chunk(self, document_id: str, chunk_id: int, db: AsyncSession) -> Optional[Dict]:
        logger.info(f"[DB] Getting chunk {chunk_id} for document {document_id}")
        try:
            # Convert chunk_id to integer if it's a string
            sequence = int(chunk_id) if isinstance(chunk_id, str) else chunk_id
            result = await db.execute(
                select(DocumentChunk)
                .where(
                    and_(
                        DocumentChunk.document_id == document_id,
                        DocumentChunk.sequence == sequence
                    )
                )
            )
            chunk = result.scalar_one_or_none()
            if chunk:
                logger.info(f"[DB] Found chunk {chunk_id}")
                return chunk.to_dict()
            logger.error(f"[DB] Chunk {chunk_id} not found")
            return None
        except ValueError:
            # Handle case where chunk_id can't be converted to int
            logger.error(f"Invalid chunk_id format: {chunk_id}")
            return None

    async def _create_conversation(
        self,
        document_id: str,
        conversation_type: str,
        chunk_id: Optional[str] = None,
        db: AsyncSession = None,
        meta_data: Optional[Dict] = None,
        is_demo: bool = False
    ):
        """Create a new conversation"""
        conversation = Conversation(
            document_id=document_id,
            type=conversation_type,
            chunk_id=chunk_id,
            meta_data=meta_data or {},
            is_demo=is_demo
        )
        db.add(conversation)
        await db.flush()
        return conversation.to_dict()

    async def cleanup_demo_conversations(self, connection_id: str, db: AsyncSession):
        """Clean up demo conversations for a connection when it disconnects"""
        try:
            # Find all demo conversations created during this connection
            result = await db.execute(
                select(Conversation).where(
                    and_(
                        Conversation.is_demo == True,
                        Conversation.meta_data['connection_id'].astext == connection_id
                    )
                )
            )
            conversations = result.scalars().all()

            # Delete conversations and their messages
            for conversation in conversations:
                await db.delete(conversation)
            
            await db.commit()
        except Exception as e:
            logger.error(f"Error cleaning up demo conversations: {e}")

    async def _emit_error_event(self, original_event: Event, error_message: str):
        """Helper to emit error events"""
        logger.info(f"[ERROR] Emitting error event for {original_event.type}: {error_message}")
        await event_bus.emit(Event(
            type=f"{original_event.type.replace('.requested', '')}.error",
            document_id=original_event.document_id,
            connection_id=original_event.connection_id,
            data={"error": error_message}
        ))
        raise ValueError("Error already emitted")

    def __del__(self):
        """Clean up resources when instance is destroyed."""
        try:
            # Remove all handlers registered by this instance
            for event_type in self.HANDLED_EVENTS:
                event_bus.listeners[event_type] = [h for h in event_bus.listeners[event_type] 
                                                 if not hasattr(h, '__self__') or h.__self__ is not self]
            # Clean up task queue
            if hasattr(self, '_task_queue'):
                self._task_queue = None
        except:
            pass  # Ignore errors during cleanup

    async def shutdown(self):
        """Cleanup when shutting down"""
        self.shutdown_event.set()
        
        # Remove all event handlers
        for handler in self._handlers:
            event_bus.remove_listener(handler[0], handler[1])
        
        # Clear handlers list
        self._handlers = []
        
        # Cancel processor task
        if self.processor_task:
            self.processor_task.cancel()
            try:
                await self.processor_task
            except asyncio.CancelledError:
                pass