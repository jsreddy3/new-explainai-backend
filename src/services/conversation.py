from typing import List, Optional, Dict, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from src.models.database import Document, DocumentChunk, Conversation, Message
from src.core.logging import setup_logger
from src.services.ai import AIService

logger = setup_logger(__name__)

class ConversationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ai_service = AIService()

    async def create_main_conversation(self, document_id: str) -> Conversation:
        """Create a main conversation for a document"""
        conversation = Conversation(
            document_id=document_id,
            meta_data={"type": "main"}
        )
        self.db.add(conversation)
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation

    async def create_chunk_conversation(
        self, 
        document_id: str, 
        chunk_id: str,
        highlight_range: Tuple[int, int],
        highlighted_text: str
    ) -> Conversation:
        """Create a conversation for a specific document chunk"""
        conversation = Conversation(
            document_id=document_id,
            chunk_id=chunk_id,
            meta_data={
                "type": "chunk",
                "highlight_range": highlight_range,
                "highlighted_text": highlighted_text
            }
        )
        self.db.add(conversation)
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation

    async def add_message(
        self, 
        conversation_id: str, 
        content: str, 
        role: str = "user"
    ) -> Message:
        """Add a message to a conversation"""
        message = Message(
            conversation_id=conversation_id,
            content=content,
            role=role
        )
        self.db.add(message)
        await self.db.commit()
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

    async def get_document_conversations(
        self, 
        document_id: str
    ) -> Dict[str, List[Conversation]]:
        """Get all conversations for a document, grouped by type"""
        result = await self.db.execute(
            select(Conversation).where(Conversation.document_id == document_id)
        )
        conversations = result.scalars().all()
        
        return {
            "main": [c for c in conversations if not c.chunk_id],
            "chunks": [c for c in conversations if c.chunk_id]
        }

    async def get_chunk_conversations(
        self, 
        chunk_id: str
    ) -> List[Conversation]:
        """Get all conversations for a specific chunk"""
        result = await self.db.execute(
            select(Conversation).where(Conversation.chunk_id == chunk_id)
        )
        return result.scalars().all()

    async def get_chat_context(self, conversation_id: str) -> Dict:
        """Get context for a conversation"""
        # Use proper async query syntax
        result = await self.db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")
            
        # Get document
        result = await self.db.execute(
            select(Document).where(Document.id == conversation.document_id)
        )
        document = result.scalar_one_or_none()
        
        # Get chunk if exists
        chunk = None
        if conversation.chunk_id:
            result = await self.db.execute(
                select(DocumentChunk).where(DocumentChunk.id == conversation.chunk_id)
            )
            chunk = result.scalar_one_or_none()
            
        # Get messages
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        messages = result.scalars().all()
        
        return {
            "document": {
                "id": document.id,
                "title": document.title,
                "content": document.content if not chunk else chunk.content
            },
            "conversation": {
                "id": conversation.id,
                "type": conversation.meta_data.get("type", "main"),
                "highlight_range": conversation.meta_data.get("highlight_range"),
                "highlighted_text": conversation.meta_data.get("highlighted_text")
            },
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content
                } for msg in messages
            ]
        }

    async def generate_questions(self, conversation_id: str, count: int = 3) -> List[str]:
        """Generate questions based on the document content"""
        # Get document content from context
        context = await self.get_chat_context(conversation_id)
        document_content = context["document"]["content"]
        
        # Use AI service to generate questions
        questions = await self.ai_service.generate_questions(
            document_content=document_content,
            count=count
        )
        
        logger.info(f"Generated {len(questions)} questions for conversation {conversation_id}")
        return questions