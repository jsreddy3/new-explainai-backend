import json
from typing import List, Optional, Dict, Tuple, AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
from sqlalchemy.orm import sessionmaker
from ..db.session import engine  # Add this if not already imported
from sqlalchemy import select, and_
import uuid
from datetime import datetime

from ..models.database import Document, DocumentChunk, Conversation, Message, Question, ConversationType, User
from ..core.events import event_bus, Event
from ..core.config import settings
from ..core.logging import setup_logger
from .ai import AIService
from ..prompts.manager import PromptManager
from src.services.cost import check_user_cost_limit

logger = setup_logger(__name__)

class ConversationService:
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
            self.ai_service = AIService()  # Remove db dependency
            self.prompt_manager = PromptManager()
                
            # Task management
            self.task_queue = asyncio.Queue()
            self.active_tasks = set()
            self.semaphore = asyncio.Semaphore(10)
            self.shutdown_event = asyncio.Event()
                
            # Start processor
            self.processor_task = asyncio.create_task(self._process_tasks())

            # Register event handlers with queue wrapper
            event_bus.on("conversation.main.create.requested", self._queue_task(self.handle_create_main_conversation))
            event_bus.on("conversation.chunk.create.requested", self._queue_task(self.handle_create_chunk_conversation))
            event_bus.on("conversation.message.send.requested", self._queue_task(self.handle_send_message))
            event_bus.on("conversation.questions.generate.requested", self._queue_task(self.handle_generate_questions))
            event_bus.on("conversation.questions.list.requested", self._queue_task(self.handle_list_questions))
            event_bus.on("conversation.merge.requested", self._queue_task(self.handle_merge_conversations))
            event_bus.on("conversation.chunk.get.requested", self._queue_task(self.handle_get_conversations_by_sequence))
            event_bus.on("conversation.messages.requested", self._queue_task(self.handle_list_messages))
            event_bus.on("conversation.list.requested", self._queue_task(self.handle_list_conversations))
                
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
                task.add_done_callback(self.active_tasks.discard)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Task processor error: {e}")

    async def _run_task(self, handler, event):
        try:
            async with self.semaphore:
                async with self.AsyncSessionLocal() as db:
                    try:
                        logger.info(f"Running task: {event.type}")
                        await handler(event, db)
                    except Exception as e:
                        logger.error(f"Handler error: {e}")
                    finally:
                        await db.close()
        except Exception as e:
            logger.error(f"Task execution error: {e}")

    async def handle_create_main_conversation(self, event: Event, db: AsyncSession):
        try:
            document_id = event.document_id
            is_demo = document_id in settings.EXAMPLE_DOCUMENT_IDS
            
            # First check if a main conversation already exists
            query = select(Conversation).where(
                and_(
                    Conversation.document_id == document_id,
                    Conversation.type == "main"
                )
            )
            
            # For demo docs, only find main conversations from this connection
            if is_demo:
                logger.info(f"Looking for demo main conversation with connection_id: {event.connection_id}")
                query = query.where(
                    and_(
                        Conversation.is_demo == True,
                        Conversation.meta_data['connection_id'].astext == event.connection_id
                    )
                )
            
            result = await db.execute(query)
            existing_conversation = result.scalar_one_or_none()
            
            if existing_conversation:
                # Return the existing conversation ID
                logger.info("Returning existing main conversation")
                await event_bus.emit(Event(
                    type="conversation.main.create.completed",
                    document_id=document_id,
                    connection_id=event.connection_id,
                    request_id=event.request_id,
                    data={"conversation_id": str(existing_conversation.id)}
                ))
                return
            
            # If no main conversation exists, create one
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
                db=db
            )
            
            await db.commit()
            logger.info(f"Created new main conversation for connection {event.connection_id}")
            await event_bus.emit(Event(
                type="conversation.main.create.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"conversation_id": str(conversation["id"])}
            ))
        except Exception as e:
            logger.error(f"Error creating main conversation: {e}")
            await event_bus.emit(Event(
                type="conversation.main.create.error",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
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
                request_id=event.request_id,
                data={"conversation_id": str(conversation["id"])}
            ))
        except Exception as e:
            logger.error(f"Error creating chunk conversation: {e}")
            await event_bus.emit(Event(
                type="conversation.chunk.create.error",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))

    async def handle_send_message(self, event: Event, db: AsyncSession):
        logger.info("Handle send message")
        document_id = event.document_id
        try:
            conversation_id = event.data["conversation_id"]
            content = event.data["content"]
            chunk_id = event.data["chunk_id"]
            user = event.data.get("user")  # Get user from event data
            
            # Check cost limit first if user exists
            if user:
                await check_user_cost_limit(db, user.id)
            
            # Get conversation
            conversation = await self._get_conversation(conversation_id, db)  # Pass db
            chunk = await self._get_chunk(document_id=conversation["document_id"], chunk_id=conversation["chunk_id"], db=db) if conversation["chunk_id"] else None  # Pass db
            
            # 1. Store raw user message in DB
            message = await self._create_message(
                conversation["id"], 
                content, 
                chunk_id if chunk_id else conversation["chunk_id"],
                "user",
                db=db  # Pass db
            )
            await db.commit()
            
            # 2. Process the user message based on conversation type
            messages = []
            if conversation["type"] == "highlight":
                highlight_text = await self._get_highlight_text(conversation["id"], db)  # Pass db
                processed_content = self.prompt_manager.create_highlight_user_prompt(
                    user_message=content
                )
                messages = await self.get_conversation_messages(conversation["id"], db)
            else:
                processed_content = self.prompt_manager.create_main_user_prompt(
                    user_message=content
                )
                messages = await self._get_messages_with_chunk_switches(conversation, db)  # Pass db


            if messages:
                messages[-1]["content"] = processed_content

            # 3. Send entire messages array to AI service
            response, cost = await self.ai_service.chat(
                document_id,
                conversation_id,
                messages=messages,
                connection_id=event.connection_id,  # Pass the WebSocket connection_id
                request_id=event.request_id  # Pass the request_id
            )
                        
            # Add AI response in a new transaction
            ai_message = await self._create_message(
                conversation["id"], 
                response, 
                chunk_id if chunk_id else conversation["chunk_id"],
                "assistant",
                db=db  # Pass db
            )
            if user and cost:
                stmt = select(User).where(User.id == user.id)
                result = await db.execute(stmt)
                db_user = result.scalar_one()
                db_user.user_cost += cost
                logger.info(f"Updated user {user.id} cost to ${db_user.user_cost:.2f}")
        
            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.message.send.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"message": response, "conversation_id": str(conversation["id"]), "cost": cost}
            ))
                
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            await event_bus.emit(Event(
                type="conversation.message.send.error",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
                data={"error": str(e)}
            ))
            try:
                await db.rollback()
            except:
                pass

    async def handle_generate_questions(self, event: Event, db: AsyncSession):
        try:
            data = event.data
            conversation_id = data["conversation_id"]
            document_id = event.data["document_id"]
            user = event.data.get("user")
            
            # Check cost limit first if user exists
            if user:
                await check_user_cost_limit(db, user.id)
            
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
                    count=3,
                    previous_questions=previous_questions
                )
            else:
                system_prompt, user_prompt = self.prompt_manager.create_main_question_prompts(
                    chunk_text=chunk["content"],
                    count=3,
                    previous_questions=previous_questions
                )
            
            questions, cost = await self.ai_service.generate_questions(
                document_id=conversation["document_id"],
                request_id=event.request_id,
                conversation_id=conversation_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            
            # Store generated questions
            for question in questions[:3]:
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
                request_id=event.request_id,
                data={
                    "conversation_id": conversation_id,
                    "questions": [q.strip() for q in questions[:3]],
                    "cost": cost
                }
            ))
            
        except Exception as e:
            logger.error(f"Error generating questions: {e}")
            await event_bus.emit(Event(
                type="conversation.questions.generate.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
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
            
            summary, cost = await self.ai_service.generate_summary(
                document_id=highlight_conversation["document_id"],
                request_id=event.request_id,
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
                request_id=event.request_id,
                data={
                    "main_conversation_id": main_conversation_id,
                    "highlight_conversation_id": highlight_conversation_id,
                    "summary": summary,
                    "cost": cost
                }
            ))
            
        except Exception as e:
            logger.error(f"Error merging conversations: {e}")
            await event_bus.emit(Event(
                type="conversation.merge.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
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
                request_id=event.request_id,
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
                request_id=event.request_id,
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

            conversations_data = {}
            for conv in conversations:
                conv_data = {
                    "document_id": str(conv.document_id),
                    "chunk_id": conv.chunk_id,
                    "created_at": conv.created_at.isoformat(),
                    "highlight_text": conv.meta_data.get("highlight_text", "") if conv.meta_data else ""
                }

                # Add highlight_range if available in meta_data
                if conv.meta_data and "highlight_range" in conv.meta_data:
                    conv_data["highlight_range"] = conv.meta_data["highlight_range"]

                conversations_data[str(conv.id)] = conv_data
            
            # Emit success event
            await event_bus.emit(Event(
                type="conversation.chunk.get.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,  # Preserve request_id
                data={"conversations": conversations_data}
            ))
            
        except Exception as e:
            logger.error(f"Error getting conversations by sequence: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.chunk.get.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,  # Preserve request_id
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
                request_id=event.request_id,
                data={"conversations": conversations_data}
            ))
            
        except Exception as e:
            logger.error(f"Error listing conversations: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.list.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
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
                request_id=event.request_id,
                data={
                    "conversation_id": conversation_id,
                    "messages": message_list
                }
            ))
            
        except Exception as e:
            logger.error(f"Error listing messages: {e}")
            # Emit error event
            await event_bus.emit(Event(
                type="conversation.messages.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                request_id=event.request_id,
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
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            chunk_id=chunk_id,
            meta_data=meta_data
        )
        db.add(message)
        await db.flush()
        await db.refresh(message)
        return message

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
        conversation = await db.get(Conversation, conversation_id)
        return conversation.to_dict() if conversation else None

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
        """Get a chunk by its sequence number within a document"""
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
            return chunk.to_dict() if chunk else None
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