from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import json

from src.db.session import get_db
from src.services.conversation import ConversationService
from src.services.ai import AIService
from src.core.logging import setup_logger

logger = setup_logger(__name__)
ai_service = AIService()
router = APIRouter()

class MessageCreate(BaseModel):
    content: str
    role: str = "user"
    context: Optional[Dict] = None

class ConversationCreate(BaseModel):
    chunk_id: Optional[str] = None
    highlight_range: Optional[tuple[int, int]] = None

class QuestionGenerate(BaseModel):
    count: int = 3
    previous_questions: List[str] = []
    context: Optional[Dict] = None

class MessageRequest(BaseModel):
    content: str
    role: str = "user"

class QuestionRequest(BaseModel):
    count: int = 3

class ChunkConversationRequest(BaseModel):
    chunk_id: str
    highlight_range: tuple[int, int]
    highlighted_text: str

class ChunkConversationRequest(BaseModel):
    chunk_id: str
    highlight_range: tuple[int, int]
    highlighted_text: str

async def stream_response(generator):
    """Stream AI responses"""
    try:
        async for chunk in generator:
            if chunk:
                yield f"data: {json.dumps({'content': chunk})}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"

@router.post("/documents/{document_id}/conversations")
async def create_conversation(
    document_id: str,
    db: Session = Depends(get_db)
) -> Dict:
    """Create a new main conversation for a document"""
    try:
        conversation_service = ConversationService(db)
        conversation = await conversation_service.create_main_conversation(document_id)
        
        return {
            "id": conversation.id,
            "document_id": conversation.document_id,
            "created_at": conversation.created_at,
            "meta_data": conversation.meta_data
        }
        
    except Exception as e:
        logger.error(f"Error creating conversation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/documents/{document_id}/conversations/chunk")
async def create_chunk_conversation(
    document_id: str,
    request: ChunkConversationRequest,
    db: Session = Depends(get_db)
) -> Dict:
    """Create a new conversation for a specific document chunk"""
    try:
        conversation_service = ConversationService(db)
        conversation = await conversation_service.create_chunk_conversation(
            document_id=document_id,
            chunk_id=request.chunk_id,
            highlight_range=request.highlight_range,
            highlighted_text=request.highlighted_text
        )
        
        return {
            "id": conversation.id,
            "document_id": conversation.document_id,
            "chunk_id": conversation.chunk_id,
            "created_at": conversation.created_at,
            "meta_data": conversation.meta_data
        }
        
    except Exception as e:
        logger.error(f"Error creating chunk conversation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/conversations/{conversation_id}/messages")
async def add_message(
    conversation_id: str,
    message: MessageCreate,
    db: Session = Depends(get_db)
) -> Dict:
    """Add a message to a conversation"""
    conversation_service = ConversationService(db)
    
    try:
        new_message = await conversation_service.add_message(
            conversation_id=conversation_id,
            content=message.content,
            role=message.role
        )
        
        return {
            "id": new_message.id,
            "conversation_id": new_message.conversation_id,
            "content": new_message.content,
            "role": new_message.role,
            "created_at": new_message.created_at
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
) -> List[Dict]:
    """Get messages for a conversation"""
    conversation_service = ConversationService(db)
    
    try:
        messages = await conversation_service.get_conversation_messages(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset
        )
        
        return [
            {
                "id": msg.id,
                "content": msg.content,
                "role": msg.role,
                "created_at": msg.created_at
            }
            for msg in messages
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents/{document_id}/conversations")
async def get_document_conversations(
    document_id: str,
    db: Session = Depends(get_db)
) -> Dict[str, List[Dict]]:
    """Get all conversations for a document"""
    conversation_service = ConversationService(db)
    
    try:
        conversations = await conversation_service.get_document_conversations(document_id)
        
        def format_conversation(conv):
            return {
                "id": conv.id,
                "created_at": conv.created_at,
                "meta_data": conv.meta_data
            }
        
        return {
            "main": [format_conversation(c) for c in conversations["main"]],
            "chunks": [format_conversation(c) for c in conversations["chunks"]]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/conversations/{conversation_id}/chat")
async def chat(
    conversation_id: str,
    request: MessageRequest,
    db: Session = Depends(get_db)
):
    """Chat with the document context"""
    try:
        conversation_service = ConversationService(db)
        ai_service = AIService()
        
        # Get conversation context
        context = await conversation_service.get_chat_context(conversation_id)
        
        # Add user message
        await conversation_service.add_message(
            conversation_id=conversation_id,
            content=request.content,
            role=request.role
        )
        
        # Get AI response
        async def generate_response():
            async for token in ai_service.stream_chat(
                messages=[{"role": request.role, "content": request.content}],
                context=context
            ):
                yield f"data: {token}\n\n"
                
        return StreamingResponse(
            generate_response(),
            media_type="text/event-stream"
        )
        
    except Exception as e:
        logger.error(f"Error in chat: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/conversations/{conversation_id}/questions")
async def generate_questions(
    conversation_id: str,
    request: QuestionRequest,
    db: Session = Depends(get_db)
) -> List[str]:
    """Generate questions about the document"""
    try:
        conversation_service = ConversationService(db)
        questions = await conversation_service.generate_questions(
            conversation_id=conversation_id,
            count=request.count
        )
        return questions
    except Exception as e:
        logger.error(f"Error generating questions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))