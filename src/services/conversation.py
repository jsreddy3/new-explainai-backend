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
from ..core.logging import setup_logger
from .ai import AIService
from ..prompts.manager import PromptManager

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
                expire_on_commit=False
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
                        await handler(event, db)
                    except Exception as e:
                        logger.error(f"Handler error: {e}")
                    finally:
                        await db.close()
        except Exception as e:
            logger.error(f"Task execution error: {e}")

    async def handle_create_main_conversation(self, event: Event, db: AsyncSession):
        logger.info("Handling create main conversation")
        try:
            document_id = event.data["document_id"]
            
            # Get first chunk
            first_chunk = await self._get_first_chunk(document_id, db)  # Pass db
            
            # Create conversation
            conversation = await self._create_conversation(
                document_id=document_id,
                conversation_type="main",
                chunk_id=None,
                db=db  # Pass db
            )
            
            # Create initial system message
            system_prompt = self.prompt_manager.create_main_system_prompt(first_chunk.content)
            await self._create_message(
                conversation.id, 
                system_prompt, 
                "system",
                first_chunk.id,
                db=db  # Pass db
            )
            
            await db.commit()
            
            logger.info(f"Main conversation created - Document: {document_id}, Conversation: {conversation.id}")
            await event_bus.emit(Event(
                type="conversation.main.create.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"conversation_id": str(conversation.id)}
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
            document_id = data["document_id"]
            chunk_id = data["chunk_id"]
            highlighted_text = data.get("highlighted_text", "")
            
            # Get chunk
            chunk = await self._get_chunk(chunk_id, db)  # Pass db
            
            # Create conversation
            conversation = await self._create_conversation(
                document_id=document_id,
                conversation_type="highlight",
                chunk_id=chunk_id,
                db=db  # Pass db
            )
            
            # Create initial system message
            system_prompt = self.prompt_manager.create_highlight_system_prompt(
                chunk_text=chunk.content,
                highlighted_text=highlighted_text
            )
            await self._create_message(
                conversation.id, 
                system_prompt, 
                "system",
                chunk_id,
                db=db  # Pass db
            )
            
            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.chunk.create.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"conversation_id": str(conversation.id)}
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
        document_id = event.document_id
        try:
            conversation_id = event.data["conversation_id"]
            content = event.data["content"]
            chunk_id = event.data.get("chunk_id")
            
            logger.info(f"Handling send message - Document: {document_id}, Conversation: {conversation_id}")
            
            # Get conversation
            conversation = await self._get_conversation(conversation_id, db)  # Pass db
            chunk = await self._get_chunk(conversation.chunk_id, db) if conversation.chunk_id else None  # Pass db
            
            # 1. Store raw user message in DB
            message = await self._create_message(
                conversation_id, 
                content, 
                "user",
                chunk_id if chunk_id else conversation.chunk_id,
                db=db  # Pass db
            )
            await db.commit()
            
            # 2. Process the user message based on conversation type
            if conversation.type == "highlight":
                highlighted_text = await self._get_highlighted_text(conversation_id, db)  # Pass db
                processed_content = self.prompt_manager.create_highlight_user_prompt(
                    chunk_text=chunk.content,
                    highlighted_text=highlighted_text,
                    user_message=content
                )
            else:
                processed_content = self.prompt_manager.create_main_user_prompt(
                    user_message=content
                )
            
            # 3. Get all messages including system prompt and chunk switches
            messages = await self._get_messages_with_chunk_switches(conversation, db)  # Pass db
            
            # 4. Replace the last message's content with processed version
            if messages:
                messages[-1]["content"] = processed_content
            
            logger.info(f"Calling AI service chat - Document: {document_id}, Conversation: {conversation_id}")
            
            # 5. Send entire messages array to AI service
            response = await self.ai_service.chat(
                document_id,
                conversation_id,
                messages=messages
            )
            
            logger.info(f"AI service chat response - Document: {document_id}, Conversation: {conversation_id}, Response: {response}")
            
            # Add AI response in a new transaction
            ai_message = await self._create_message(
                conversation_id, 
                response, 
                "assistant",
                chunk_id if chunk_id else conversation.chunk_id,
                db=db  # Pass db
            )
            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.message.send.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"message": response}
            ))
                
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            await event_bus.emit(Event(
                type="conversation.message.send.error",
                document_id=document_id,
                connection_id=event.connection_id,
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
            count = data.get("count", 3)
            
            # Get conversation and chunk
            conversation = await self._get_conversation(conversation_id, db)  # Pass db
            chunk = await self._get_chunk(conversation.chunk_id, db) if conversation.chunk_id else None  # Pass db
            
            # Get previous questions
            previous_questions = await self._get_previous_questions(conversation_id, db)  # Pass db
            
            # Generate questions based on conversation type
            if conversation.type == "highlight":
                highlighted_text = await self._get_highlighted_text(conversation_id, db)  # Pass db
                system_prompt, user_prompt = self.prompt_manager.create_highlight_question_prompts(
                    chunk_text=chunk.content,
                    highlighted_text=highlighted_text,
                    count=count,
                    previous_questions=previous_questions
                )
            else:
                system_prompt, user_prompt = self.prompt_manager.create_main_question_prompts(
                    chunk_text=chunk.content,
                    count=count,
                    previous_questions=previous_questions
                )
            
            questions = await self.ai_service.generate_questions(
                document_id=conversation.document_id,
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
                        meta_data={"chunk_id": conversation.chunk_id}
                    )
                    db.add(question_entry)  # Use passed db
            
            await db.commit()
            
            await event_bus.emit(Event(
                type="conversation.questions.generate.completed",
                document_id=conversation.document_id,
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
            chunk = await self._get_chunk(highlight_conversation.chunk_id, db)
            highlighted_text = await self._get_highlighted_text(highlight_conversation_id, db)
            
            # Format conversation history for summary
            conversation_history = await self._format_conversation_history(highlight_conversation_id, db)
            
            # Generate summary using AI service
            system_prompt, user_prompt = self.prompt_manager.create_summary_prompts(
                chunk_text=chunk.content,
                highlighted_text=highlighted_text,
                conversation_history=conversation_history
            )
            
            summary = await self.ai_service.generate_summary(
                document_id=highlight_conversation.document_id,
                conversation_id=highlight_conversation_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            
            # Get metadata from first message for chunk info
            first_message = await self._get_first_message(highlight_conversation_id, db)
            chunk_id = first_message.meta_data.get("chunk_id") if first_message.meta_data else None
            
            # Create summary message in main conversation
            await self._create_message(
                conversation_id=main_conversation_id,
                content=f"Summary of highlight discussion:\n{summary}",
                role="user",
                chunk_id=chunk_id,
                meta_data={"merged_from": highlight_conversation_id},
                db=db
            )
            
            # Add acknowledgment message
            await self._create_message(
                conversation_id=main_conversation_id,
                content="Acknowledged conversation merge",
                role="assistant",
                chunk_id=chunk_id,
                db=db
            )
            
            await event_bus.emit(Event(
                type="conversation.merge.completed",
                document_id=highlight_conversation.document_id,
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
                    "questions": [q.to_dict() for q in questions]
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

    async def _get_messages_with_chunk_switches(self, conversation: Conversation, db: AsyncSession) -> List[Dict]:
        messages = await self.get_conversation_messages(conversation_id=conversation.id, db=db)
        
        if conversation.type != "main":
            return messages
            
        processed_messages = []
        last_chunk_id = None
        added_content_chunks = set()
        
        for msg in reversed(messages):
            current_chunk_id = msg.meta_data.get("chunk_id") if msg.meta_data else None
            
            if current_chunk_id and current_chunk_id != last_chunk_id:
                switch_msg = {
                    "role": "user",
                    "content": f"<switched to chunk ID {current_chunk_id}>"
                }
                ack_msg = {
                    "role": "assistant",
                    "content": f"<acknowledged switch to chunk ID {current_chunk_id}>"
                }
                
                if current_chunk_id not in added_content_chunks:
                    chunk = await self._get_chunk(current_chunk_id, db)  # Pass db
                    switch_msg["content"] += f", chunkText: {chunk.content}"
                    added_content_chunks.add(current_chunk_id)
                
                processed_messages.insert(0, ack_msg)
                processed_messages.insert(0, switch_msg)
                last_chunk_id = current_chunk_id
            
            processed_messages.insert(0, msg.to_dict())
            
        return processed_messages

    async def _get_current_chunk_id(self, conversation: Conversation, db: AsyncSession) -> Optional[str]:
        if conversation.type != "main":
            return None
            
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
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
            f"{msg.role.upper()}: {msg.content}"
            for msg in messages
        ])

    async def _create_message(
        self,
        conversation_id: str,
        content: str,
        role: str = "user",
        chunk_id: Optional[str] = None,
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
    ) -> List[Message]:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_conversation_questions(
        self,
        conversation_id: str,
        db: AsyncSession
    ) -> List[Question]:
        result = await db.execute(
            select(Question)
            .where(Question.conversation_id == conversation_id)
            .order_by(Question.created_at)
        )
        return result.scalars().all()

    async def _get_first_chunk(self, document_id: str, db: AsyncSession) -> DocumentChunk:
        result = await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.sequence)
            .limit(1)
        )
        return result.scalar_one()

    async def _get_chunk(self, chunk_id: str, db: AsyncSession) -> DocumentChunk:
        logger.info(f"Getting chunk: {chunk_id}")
        return await db.get(DocumentChunk, chunk_id)

    async def _get_conversation(self, conversation_id: str, db: AsyncSession) -> Conversation:
        return await db.get(Conversation, conversation_id)

    async def _get_previous_questions(self, conversation_id: str, db: AsyncSession) -> List[Question]:
        result = await db.execute(
            select(Question)
            .where(Question.conversation_id == conversation_id)
            .order_by(Question.created_at)
        )
        return result.scalars().all()

    async def _get_highlighted_text(self, conversation_id: str, db: AsyncSession) -> str:
        conversation = await self._get_conversation(conversation_id, db)
        return conversation.meta_data.get("highlighted_text", "")

    async def _get_first_message(self, conversation_id: str, db: AsyncSession) -> Message:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .limit(1)
        )
        return result.scalar_one()

    async def _create_conversation(
        self,
        document_id: str,
        conversation_type: str,
        chunk_id: Optional[str] = None,
        db: AsyncSession = None
    ) -> Conversation:
        conversation = Conversation(
            document_id=document_id,
            chunk_id=chunk_id,
            type=conversation_type,
            meta_data={}
        )
        db.add(conversation)
        return conversation