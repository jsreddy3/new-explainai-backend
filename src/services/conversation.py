import json
from typing import List, Optional, Dict, Tuple, AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import uuid
from datetime import datetime

from ..models.database import Document, DocumentChunk, Conversation, Message, Question
from ..core.events import event_bus, Event
from ..core.logging import setup_logger
from .ai import AIService

logger = setup_logger(__name__)

class ConversationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ai_service = AIService()

    async def get_chunk(self, conversation_id: str) -> dict:
      """Get chunk info for a chunk conversation"""
      # Get conversation
      result = await self.db.execute(
          select(Conversation).where(Conversation.id == conversation_id)
      )
      conversation = result.scalar_one_or_none()
      
      if not conversation or not conversation.chunk_id:
          return {}
          
      # Get chunk
      result = await self.db.execute(
          select(DocumentChunk).where(DocumentChunk.id == conversation.chunk_id)
      )
      chunk = result.scalar_one_or_none()
      
      if not chunk:
          return {}
          
      return {
          "text": chunk.content,
          "highlighted_text": chunk.meta_data.get("highlighted_text", "")
          if chunk.meta_data else ""
      }

    async def _create_conversation(self, document_id: str, chunk_id: str = None, highlight_range: Dict = None, highlighted_text: str = None) -> Conversation:
        """Create a conversation"""
        conversation = Conversation(
            document_id=document_id,
            chunk_id=chunk_id,
            meta_data={
                "type": "chunk" if chunk_id else "main",
                "highlight_range": highlight_range,
                "highlighted_text": highlighted_text
            }
        )
        
        self.db.add(conversation)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(conversation)
        
        return conversation

    async def create_main_conversation(self, document_id: str) -> Conversation:
        """Create a main conversation for a document"""
        conversation = await self._create_conversation(document_id)
        return conversation

    async def create_chunk_conversation(
        self,
        document_id: str,
        chunk_id: str,
        highlight_range: Dict,
        highlighted_text: str
    ) -> Conversation:
        """Create a conversation for a document chunk"""
        conversation = await self._create_conversation(
            document_id,
            chunk_id=chunk_id,
            highlight_range=highlight_range,
            highlighted_text=highlighted_text
        )
        return conversation

    async def get_document_context(self, document_id: str) -> Dict:
        """Get document context"""
        document = await self.db.get(Document, document_id)
        return {
            "id": document.id,
            "title": document.title,
            "content": document.content
        }

    async def get_chat_context(self, conversation_id: str) -> Dict:
        """Get context for chat"""
        logger.info("\n=== Starting get_chat_context for %s ===", conversation_id)
        
        # Get conversation and metadata
        conversation = await self._get_conversation(conversation_id)
        meta_data = conversation.meta_data or {}
        logger.info("1. Raw conversation meta_data: %s", meta_data)
        
        # Extract highlighted text from metadata
        highlighted_text = meta_data.get("highlighted_text", "")
        logger.info("2. Extracted highlighted_text: '%s'", highlighted_text)
        
        # Build conversation context
        context = {
            "document": await self.get_document_context(conversation.document_id),
            "document_id": conversation.document_id,
            "conversation": {
                "id": conversation_id,
                "type": meta_data.get("type", "main"),
                "highlight_range": meta_data.get("highlight_range"),
                "highlighted_text": highlighted_text
            }
        }
        logger.info("3. Built context conversation: %s", context["conversation"])
        
        # Add chunk context if available
        if conversation.chunk_id:
            chunk_info = await self.get_chunk(conversation_id)
            context["chunk"] = {
                "text": chunk_info["text"],
                "highlighted_text": highlighted_text  # Add highlight to chunk context
            }
            logger.info("4. Added chunk context with highlight: '%s'", highlighted_text)
        
        # Add highlighted text at top level for easy access
        context["highlighted_text"] = highlighted_text
        
        logger.info("5. Final context keys: %s", context.keys())
        logger.info("6. Final highlight locations:")
        logger.info("   - conversation: '%s'", context["conversation"].get("highlighted_text", ""))
        logger.info("   - chunk: '%s'", context.get("chunk", {}).get("highlighted_text", ""))
        logger.info("   - top level: '%s'", context.get("highlighted_text", ""))
        
        return context

    async def _create_message(self, conversation_id: str, content: str, role: str = "user", metadata: Dict = None) -> Message:
        """Create a message"""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            meta_data=metadata
        )
        self.db.add(message)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(message)
        
        return message

    async def add_message(
        self,
        conversation_id: str,
        content: str,
        role: str = "user"
    ) -> Message:
        """Add a message to a conversation"""
        message = await self._create_message(conversation_id, content, role)
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

    async def get_conversation_context(self, conversation_id: str) -> Dict:
        """Get context for a conversation"""
        conversation = await self._get_conversation(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")

        # Get document and chunk information
        document = await self.db.get(Document, conversation.document_id)
        if not document:
            raise ValueError(f"Document {conversation.document_id} not found")

        context = {
            "document": {
                "id": document.id,
                "title": document.title,
                "content": document.content if hasattr(document, 'content') else ""
            },
            "conversation": {
                "id": conversation.id,
                "type": conversation.meta_data.get("type", "main")
            }
        }

        # Add chunk-specific context if available
        if conversation.chunk_id:
            chunk = await self.db.get(DocumentChunk, conversation.chunk_id)
            if chunk:
                context["chunk"] = {
                    "id": chunk.id,
                    "text": chunk.text,
                    "highlight_range": conversation.meta_data.get("highlight_range"),
                    "highlighted_text": conversation.meta_data.get("highlighted_text"),
                }

        # Add conversation history
        messages = await self.get_conversation_messages(conversation_id)
        context["messages"] = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]

        return context

    async def _get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Get a conversation by ID"""
        query = select(Conversation).where(Conversation.id == conversation_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_conversation_questions(
        self,
        conversation_id: str
    ) -> List[Question]:
        """Get all questions generated for a conversation"""
        result = await self.db.execute(
            select(Question)
            .where(Question.conversation_id == conversation_id)
            .order_by(Question.created_at)
        )
        return result.scalars().all()

    async def generate_questions(
        self,
        conversation_id: str,
        count: int = 3,
        previous_questions: List[str] = []
    ) -> List[Question]:
        """Generate questions for a conversation"""
        try:
            # Get context for question generation
            context = await self.get_chat_context(conversation_id)
            
            if context["conversation"].get("type") == "chunk":
                chunk_info = await self.get_chunk(conversation_id)
                context["chunk"] = chunk_info
                context["highlighted_text"] = chunk_info.get("highlighted_text", "")

            questions = await self.ai_service.generate_questions(
                document_id=context["document_id"],
                conversation_id=conversation_id,
                context=context,
                count=count
            )
            stored_questions = await self.store_generated_questions(conversation_id, questions, context)
            return stored_questions

        except Exception as e:
            logger.error(f"Error generating questions: {str(e)}")
            raise

    async def store_generated_questions(
      self,
      conversation_id: str,
      questions: List[str],
      context: Dict = None
  ) -> List[Question]:
      """Store generated questions with their context"""
      stored_questions = []
      
      # Extract metadata values before JSON serialization
      generation_type = context.get("conversation", {}).get("type", "main")
      highlight_range = context.get("conversation", {}).get("highlight_range")
      highlighted_text = context.get("highlighted_text") or context.get("conversation", {}).get("highlighted_text")
      
      # Clean context for JSON serialization
      if context:
          # Convert sets to lists in context
          context = json.loads(json.dumps(context, default=list))
      
      for q in questions:
          question = Question(
              conversation_id=conversation_id,
              content=q,
              meta_data={
                  "context": context,
                  "generation_type": generation_type,
                  "highlight_range": highlight_range,
                  "highlighted_text": highlighted_text
              }
          )
          self.db.add(question)
          stored_questions.append(question)
      
      await self.db.commit()
      return stored_questions

    async def send_message(self, conversation_id: str, content: str) -> Message:
        """Send a message in a conversation"""
        try:
            # Get conversation and its messages
            result = await self.db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one()
            if not conversation:
                raise ValueError(f"Conversation {conversation_id} not found")

            messages = await self.get_conversation_messages(conversation_id)
            
            # Prepare chunk context if this is a chunk conversation
            chunk_context = None
            if conversation.meta_data and conversation.meta_data.get('type') == 'chunk':
                chunk_context = {
                    'highlighted_text': conversation.meta_data.get('highlighted_text'),
                    'highlight_range': conversation.meta_data.get('highlight_range')
                }

            # Get merged conversations if this is a main conversation
            merged_conversations = None
            if not chunk_context and conversation.meta_data:
                merged_conversations = conversation.meta_data.get('merged_conversations', [])

            # Add user message
            user_message = await self.add_message(
                conversation_id=conversation_id,
                content=content,
                role='user'
            )

            # Format messages for AI
            ai_messages = [
                {"role": m.role, "content": m.content}
                for m in messages + [user_message]
            ]

            # Get AI response
            full_response = ""
            async for token in self.ai_service.chat(
                document_id=conversation.document_id,
                conversation_id=conversation_id,
                messages=ai_messages
            ):
                full_response += token

            # Add AI message
            ai_message = await self.add_message(
                conversation_id=conversation_id,
                content=full_response,
                role='assistant'
            )
            return ai_message

        except Exception as e:
            logger.error(f"Error sending message: {str(e)}")
            raise

    async def generate_ai_response(self, conversation_id: str) -> AsyncGenerator[str, None]:
        """Generate AI response and stream tokens"""
        context = await self.get_chat_context(conversation_id)
        
        # Prepare AI generation
        async for token in self.ai_service.generate_response(context):
            yield token

    async def _generate_merge_summary(self, messages: List[Message]) -> str:
        """Generate a summary for a conversation"""
        # Format messages for AI summary
        formatted_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        # Get AI summary
        summary = await self.ai_service.summarize_conversation(formatted_messages)
        return summary

    async def _find_insertion_point(self, main_messages: List[Message], highlight_messages: List[Message]) -> int:
        """Find the insertion point for a merged conversation"""
        # Get current message count as insertion point
        insertion_index = len(main_messages)
        return insertion_index

    async def merge_conversations(
        self,
        main_conversation_id: str,
        highlight_conversation_id: str
    ) -> Dict:
        """Merge two conversations"""
        main_conv = await self._get_conversation(main_conversation_id)
        highlight_conv = await self._get_conversation(highlight_conversation_id)
        
        # Get messages from both conversations
        main_messages = await self.get_conversation_messages(main_conversation_id)
        highlight_messages = await self.get_conversation_messages(highlight_conversation_id)
        
        # Generate summary and find insertion point
        summary = await self._generate_merge_summary(highlight_messages)
        insertion_index = await self._find_insertion_point(main_messages, highlight_messages)
        
        # Create merge message
        merge_message = await self._create_message(
            main_conversation_id,
            summary,
            "assistant",
            metadata={
                "merged_from": highlight_conversation_id,
                "insertion_index": insertion_index
            }
        )
        
        return {
            "summary": summary,
            "insertion_index": insertion_index,
            "merge_message": merge_message
        }

    # Handlers that emit events
    async def handle_create_main_conversation(self, event: Event):
        """Handle main conversation creation request"""
        try:
            conversation = await self.create_main_conversation(event.document_id)
            await event_bus.emit(Event(
                type="conversation.main.create.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"conversation_id": str(conversation.id)}
            ))
        except Exception as e:
            logger.error(f"Error creating main conversation: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.main.create.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_create_chunk_conversation(self, event: Event):
        """Handle chunk conversation creation request"""
        try:
            conversation = await self.create_chunk_conversation(
                event.document_id,
                event.data["chunk_id"],
                event.data.get("highlight_range"),
                event.data.get("highlighted_text")
            )
            await event_bus.emit(Event(
                type="conversation.chunk.create.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"conversation_id": str(conversation.id)}
            ))
        except Exception as e:
            logger.error(f"Error creating chunk conversation: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.chunk.create.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_send_message(self, event: Event):
        """Handle message sending request"""
        try:
            message = await self.send_message(
                event.data["conversation_id"],
                event.data["content"],
            )
            await event_bus.emit(Event(
                type="conversation.message.send.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"message": message.to_dict()}
            ))
        except Exception as e:
            logger.error(f"Error sending message: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.message.send.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={
                    "conversation_id": event.data["conversation_id"],
                    "error": str(e)
                }
            ))

    async def handle_generate_questions(self, event: Event):
        """Handle question generation request"""
        try:
            questions = await self.generate_questions(
                event.data["conversation_id"],
                event.data.get("count", 3)
            )
            await event_bus.emit(Event(
                type="conversation.questions.generate.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={"questions": [q.to_dict() for q in questions]}
            ))
        except Exception as e:
            logger.error(f"Error generating questions: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.questions.generate.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={
                    "conversation_id": event.data["conversation_id"],
                    "error": str(e)
                }
            ))

    async def handle_list_questions(self, event: Event):
        """Handle question list request"""
        try:
            questions = await self.get_conversation_questions(event.data["conversation_id"])
            await event_bus.emit(Event(
                type="conversation.questions.list.completed",
                conversation_id=event.data["conversation_id"],
                connection_id=event.connection_id,
                data={"questions": [q.to_dict() for q in questions]}
            ))
        except Exception as e:
            logger.error(f"Error listing questions: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.questions.list.error",
                conversation_id=event.data["conversation_id"],
                connection_id=event.connection_id,
                data={"error": str(e)}
            ))

    async def handle_merge_conversations(self, event: Event):
        """Handle conversation merge request"""
        try:
            result = await self.merge_conversations(
                event.data["main_conversation_id"],
                event.data["highlight_conversation_id"]
            )
            await event_bus.emit(Event(
                type="conversation.merge.completed",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={
                    "main_conversation_id": event.data["main_conversation_id"],
                    "highlight_conversation_id": event.data["highlight_conversation_id"],
                    "result": result
                }
            ))
        except Exception as e:
            logger.error(f"Error merging conversations: {str(e)}")
            await event_bus.emit(Event(
                type="conversation.merge.error",
                document_id=event.document_id,
                connection_id=event.connection_id,
                data={
                    "main_conversation_id": event.data["main_conversation_id"],
                    "highlight_conversation_id": event.data["highlight_conversation_id"],
                    "error": str(e)
                }
            ))

    async def initialize_listeners(self):
        """Initialize event listeners for conversation operations"""
        event_bus.on("conversation.main.create.requested", self.handle_create_main_conversation)
        event_bus.on("conversation.chunk.create.requested", self.handle_create_chunk_conversation)
        event_bus.on("conversation.message.send.requested", self.handle_send_message)
        event_bus.on("conversation.questions.generate.requested", self.handle_generate_questions)
        event_bus.on("conversation.questions.list.requested", self.handle_list_questions)
        event_bus.on("conversation.merge.requested", self.handle_merge_conversations)