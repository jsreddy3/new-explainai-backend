import json
from typing import List, Optional, Dict, Tuple, AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
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
        # Only initialize once
        if not self._initialized and db is not None:
            self.db = db
            self.ai_service = AIService(db)
            self.prompt_manager = PromptManager()
            
            # Register event handlers
            event_bus.on("conversation.main.create.requested", self.handle_create_main_conversation)
            event_bus.on("conversation.chunk.create.requested", self.handle_create_chunk_conversation)
            event_bus.on("conversation.message.send.requested", self.handle_send_message)
            event_bus.on("conversation.questions.generate.requested", self.handle_generate_questions)
            event_bus.on("conversation.questions.list.requested", self.handle_list_questions)
            event_bus.on("conversation.merge.requested", self.handle_merge_conversations)
            
            self.__class__._initialized = True
    
    def update_db(self, db: AsyncSession):
        """Update the database session"""
        self.db = db
        self.ai_service = AIService(db)

    async def handle_create_main_conversation(self, event: Event):
        logger.info("Handling create main conversation")
        """Create a main conversation with initial system message"""
        try:
            document_id = event.data["document_id"]
            
            # Get first chunk
            first_chunk = await self._get_first_chunk(document_id)
            
            # Create conversation
            conversation = await self._create_conversation(
                document_id=document_id,
                conversation_type="main"
            )
            
            # Create initial system message
            system_prompt = self.prompt_manager.create_main_system_prompt(first_chunk.content)
            await self._create_message(
                conversation.id, 
                system_prompt, 
                "system",
                first_chunk.id
            )
            
            await self.db.commit()
            
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

    async def handle_create_chunk_conversation(self, event: Event):
        """Create a chunk (highlight) conversation with initial system message"""
        try:
            data = event.data
            document_id = data["document_id"]
            chunk_id = data["chunk_id"]
            highlighted_text = data.get("highlighted_text", "")
            
            # Get chunk
            chunk = await self._get_chunk(chunk_id)
            
            # Create conversation
            conversation = await self._create_conversation(
                document_id=document_id,
                conversation_type="highlight",
                chunk_id=chunk_id
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
            )
            
            await self.db.commit()
            
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

    async def handle_send_message(self, event: Event):
        """Process and send a message, handling chunk switches for main conversations"""
        document_id = event.document_id
        try:
            conversation_id = event.data["conversation_id"]
            content = event.data["content"]
            chunk_id = event.data.get("chunk_id")
            
            logger.info(f"Handling send message - Document: {document_id}, Conversation: {conversation_id}")
            
            # Get conversation
            conversation = await self.db.get(Conversation, conversation_id)
            
            # Add user message
            message = await self._create_message(
                conversation_id, 
                content, 
                "user",
                chunk_id if chunk_id else conversation.chunk_id
            )
            await self.db.commit()  # Commit user message first
            
            # Build message history with chunk switches if main conversation
            messages = await self._get_messages_with_chunk_switches(conversation)
            
            # Format prompts
            system_prompt = self.prompt_manager.create_chat_system_prompt(messages)
            user_prompt = content
            
            logger.info(f"Calling AI service chat - Document: {document_id}, Conversation: {conversation_id}")
            
            # Get AI response using context
            response = await self.ai_service.chat(
                document_id,
                conversation_id,
                system_prompt,
                user_prompt
            )
            
            logger.info(f"AI service chat response - Document: {document_id}, Conversation: {conversation_id}, Response: {response}")
            
            # Add AI response in a new transaction
            ai_message = await self._create_message(
                conversation_id, 
                response, 
                "assistant",
                chunk_id if chunk_id else conversation.chunk_id
            )
            await self.db.commit()  # Commit AI message
            
            # Only emit completed after we've fully processed everything
            await event_bus.emit(Event(
                type="conversation.message.send.completed",
                document_id=document_id,
                connection_id=event.connection_id,
                data={
                    "conversation_id": conversation_id,
                    "message": message.to_dict(),
                    "ai_message": ai_message.to_dict()
                }
            ))
                
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            await event_bus.emit(Event(
                type="conversation.message.send.error",
                document_id=document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))
            # Try to rollback if possible
            try:
                await self.db.rollback()
            except:
                pass

    async def handle_generate_questions(self, event: Event):
        """Generate insightful questions based on the conversation"""
        try:
            data = event.data
            conversation_id = data["conversation_id"]
            count = data.get("count", 3)
            
            # Get conversation and chunk
            conversation = await self._get_conversation(conversation_id)
            chunk = await self._get_chunk(conversation.chunk_id) if conversation.chunk_id else None
            
            # Get previous questions
            previous_questions = await self._get_previous_questions(conversation_id)
            
            # Generate questions based on conversation type
            if conversation.type == "highlight":
                highlighted_text = await self._get_highlighted_text(conversation_id)
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
            
            # Generate questions using AI service
            questions = await self.ai_service.generate_questions(
                document_id=conversation.document_id,
                conversation_id=conversation_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            
            # Store generated questions
            for question in questions[:count]:  # Limit to requested count
                if question.strip():
                    question_entry = Question(
                        conversation_id=conversation_id,
                        content=question.strip(),
                        meta_data={"chunk_id": conversation.chunk_id}
                    )
                    self.db.add(question_entry)
            
            await self.db.commit()
            
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

    async def handle_merge_conversations(self, event: Event):
        """Merge highlight conversation into main conversation"""
        try:
            data = event.data
            main_conversation_id = data["main_conversation_id"]
            highlight_conversation_id = data["highlight_conversation_id"]
            
            # Get conversations
            highlight_conversation = await self._get_conversation(highlight_conversation_id)
            chunk = await self._get_chunk(highlight_conversation.chunk_id)
            highlighted_text = await self._get_highlighted_text(highlight_conversation_id)
            
            # Format conversation history for summary
            conversation_history = await self._format_conversation_history(highlight_conversation_id)
            
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
            first_message = await self._get_first_message(highlight_conversation_id)
            chunk_id = first_message.meta_data.get("chunk_id") if first_message.meta_data else None
            
            # Create summary message in main conversation
            await self._create_message(
                conversation_id=main_conversation_id,
                content=f"Summary of highlight discussion:\n{summary}",
                role="user",
                chunk_id=chunk_id,
                meta_data={"merged_from": highlight_conversation_id}
            )
            
            # Add acknowledgment message
            await self._create_message(
                conversation_id=main_conversation_id,
                content="Acknowledged conversation merge",
                role="assistant",
                chunk_id=chunk_id
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

    async def handle_list_questions(self, event: Event):
        """List questions for a conversation"""
        try:
            conversation_id = event.data["conversation_id"]
            document_id = event.document_id
            
            questions = await self.get_conversation_questions(conversation_id)
            
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

    async def _get_messages_with_chunk_switches(self, conversation: Conversation) -> List[Dict]:
        """Get messages with chunk switches inserted for main conversations"""
        messages = await self.get_conversation_messages(conversation.id)
        
        if conversation.type != "main":
            return messages
            
        processed_messages = []
        last_chunk_id = None
        added_content_chunks = set()  # Track which chunks we've already added content for
        
        # Process messages from newest to oldest
        for msg in reversed(messages):
            current_chunk_id = msg.meta_data.get("chunk_id") if msg.meta_data else None
            
            if current_chunk_id and current_chunk_id != last_chunk_id:
                # Add switch messages
                switch_msg = {
                    "role": "user",
                    "content": f"<switched to chunk ID {current_chunk_id}>"
                }
                ack_msg = {
                    "role": "assistant",
                    "content": f"<acknowledged switch to chunk ID {current_chunk_id}>"
                }
                
                # Add chunk text only if we haven't added it before
                if current_chunk_id not in added_content_chunks:
                    chunk = await self._get_chunk(current_chunk_id)
                    switch_msg["content"] += f", chunkText: {chunk.content}"
                    added_content_chunks.add(current_chunk_id)
                
                processed_messages.insert(0, ack_msg)
                processed_messages.insert(0, switch_msg)
                last_chunk_id = current_chunk_id
            
            processed_messages.insert(0, msg.to_dict())
            
        return processed_messages

    async def _get_current_chunk_id(self, conversation: Conversation) -> Optional[str]:
        """Get the most recent chunk ID for a main conversation"""
        if conversation.type != "main":
            return None
            
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_message = result.scalar_one_or_none()
        
        if last_message and last_message.meta_data:
            return last_message.meta_data.get("chunk_id")
        return None

    async def _format_conversation_history(self, conversation_id: str) -> str:
        """Format conversation history for summarization"""
        messages = await self.get_conversation_messages(conversation_id)
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
        meta_data: Optional[Dict] = None
    ) -> Message:
        """Create a message"""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            chunk_id=chunk_id,
            meta_data=meta_data
        )
        self.db.add(message)
        await self.db.flush()
        await self.db.refresh(message)
        return message

    async def get_conversation_messages(
        self, 
        conversation_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Message]:
        """Get messages for a conversation with pagination"""
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_conversation_questions(
        self,
        conversation_id: str
    ) -> List[Question]:
        """Get questions for a conversation"""
        result = await self.db.execute(
            select(Question)
            .where(Question.conversation_id == conversation_id)
            .order_by(Question.created_at)
        )
        return result.scalars().all()

    async def _get_first_chunk(self, document_id: str) -> DocumentChunk:
        """Get the first chunk for a document"""
        result = await self.db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.sequence)
            .limit(1)
        )
        return result.scalar_one()

    async def _get_chunk(self, chunk_id: str) -> DocumentChunk:
        """Get a chunk by ID"""
        logger.info(f"Getting chunk: {chunk_id}")
        return await self.db.get(DocumentChunk, chunk_id)

    async def _get_conversation(self, conversation_id: str) -> Conversation:
        """Get a conversation by ID"""
        return await self.db.get(Conversation, conversation_id)

    async def _get_previous_questions(self, conversation_id: str) -> List[Question]:
        """Get previous questions for a conversation"""
        result = await self.db.execute(
            select(Question)
            .where(Question.conversation_id == conversation_id)
            .order_by(Question.created_at)
        )
        return result.scalars().all()

    async def _get_highlighted_text(self, conversation_id: str) -> str:
        """Get highlighted text for a conversation"""
        conversation = await self._get_conversation(conversation_id)
        return conversation.meta_data.get("highlighted_text", "")

    async def _get_first_message(self, conversation_id: str) -> Message:
        """Get the first message for a conversation"""
        result = await self.db.execute(
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
        chunk_id: Optional[str] = None
    ) -> Conversation:
        """Create a new conversation"""
        conversation = Conversation(
            document_id=document_id,
            chunk_id=chunk_id,
            type=conversation_type,
            meta_data={}
        )
        self.db.add(conversation)
        return conversation