# tests/test_highlight_flow.py
import logging
import asyncio
import pytest
from src.services.conversation import ConversationService
from src.db.session import AsyncSessionLocal
from src.models.database import Document, DocumentChunk  # Add any other needed models

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_highlight_flow():
    async with AsyncSessionLocal() as db:
        try:
            # First create test document and chunk
            document = Document(
                title="Test Doc",
                content="Test content",
                status="processed"
            )
            db.add(document)
            await db.flush()
            
            chunk = DocumentChunk(
                document_id=document.id,
                content="Test chunk content",
                sequence=0
            )
            db.add(chunk)
            await db.flush()
            
            logger.info(f"Created test document {document.id} and chunk {chunk.id}")
            
            # Create conversation service
            conversation_service = ConversationService(db)
            
            # Create conversation with highlight
            conv = await conversation_service.create_chunk_conversation(
                document_id=document.id,
                chunk_id=chunk.id,
                highlight_range=(0, 10),
                highlighted_text="Test chunk"
            )
            
            logger.info(f"Created conversation with metadata: {conv.meta_data}")
            
            # Get context
            context = await conversation_service.get_chat_context(conv.id)
            logger.info(f"Got context with keys: {context.keys()}")
            logger.info(f"Highlight in context: {context.get('highlighted_text')}")
            
            # Generate questions
            questions = await conversation_service.generate_questions(conv.id)
            logger.info(f"Generated questions: {questions}")
            
            await db.commit()
            
        except Exception as e:
            logger.error(f"Test failed: {str(e)}")
            raise

if __name__ == "__main__":
    asyncio.run(test_highlight_flow())