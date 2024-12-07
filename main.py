# backend/main.py
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from pydantic import BaseModel
from typing import Optional, Dict
from contextlib import asynccontextmanager
from src.core.config import settings
from src.core.logging import setup_logger
from src.db.session import init_db, AsyncSessionLocal
from src.services.conversation import ConversationService
from src.services.document import DocumentService
from src.core.events import event_bus

# Import routes
from src.api.routes import document, conversation, signup

# Setup logging
logger = setup_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan events handler"""
    try:
        logger.info("Initializing application...")
        
        # Initialize event bus
        event_bus.initialize()
        
        logger.info("Initializing database...")
        await init_db()
        
        # Initialize global services and listeners
        async with AsyncSessionLocal() as db:
            conversation_service = ConversationService(db)
            document_service = DocumentService(db)
        
        # Verify required settings
        required_settings = [
            'OPENAI_API_KEY',
            'LLAMA_CLOUD_API_KEY',
            'DATABASE_URL'
        ]
        
        missing_settings = [
            setting for setting in required_settings 
            if not getattr(settings, setting, None)
        ]
        
        if missing_settings:
            raise ValueError(f"Missing required settings: {', '.join(missing_settings)}")
            
        # Optional settings check
        optional_settings = ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY']
        missing_optional = [
            setting for setting in optional_settings 
            if not getattr(settings, setting, None)
        ]
        
        if missing_optional:
            logger.warning(f"Missing optional settings: {', '.join(missing_optional)}")
            
        logger.info("Application startup complete")
        yield
        
        # Shutdown
        logger.info("Application shutting down")
        
    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")
        raise
    finally:
        # Shutdown event bus
        await event_bus.shutdown()

# Create FastAPI app
app = FastAPI(
    title="ExplainAI API",
    description="AI-powered document analysis and conversation platform",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(document.router, prefix="/api", tags=["Documents"])
app.include_router(conversation.router, prefix="/api", tags=["Conversations"])
app.include_router(signup.router, prefix="/api", tags=["Authentication"])

class CreateConversationRequest(BaseModel):
    type: str  # 'main' | 'highlight'
    document_id: str
    full_text: str
    highlight_text: Optional[str] = None

@app.post("/api/legacy/conversations")
async def create_conversation(request: CreateConversationRequest) -> Dict[str, str]:
    """Legacy endpoint for backward compatibility
    
    Returns:
        Dict[str, str]: Dictionary containing the conversation ID
    """
    try:
        conversation_service = ConversationService()
        if request.type == "main":
            conversation = await conversation_service.create_main_conversation(request.document_id)
        else:
            # For highlight conversations
            conversation = await conversation_service.create_chunk_conversation(
                document_id=request.document_id,
                chunk_id=None,  # This will be generated
                highlight_range=(0, len(request.highlight_text)) if request.highlight_text else None
            )
        return {"conversationId": conversation.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Health check endpoint
@app.get("/health")
async def health_check() -> Dict[str, str]:
    """Health check endpoint
    
    Returns:
        Dict[str, str]: Dictionary containing the service status
    """
    return {"status": "healthy"}

print("\nDetailed Route Information:")
for route in app.routes:
    if hasattr(route, "path"):
        route_type = "WebSocket" if str(route.__class__).find("WebSocket") != -1 else "HTTP"
        methods = getattr(route, "methods", None)
        if methods:
            methods = ", ".join(methods)
        print(f"{route_type}: {route.path} {f'[{methods}]' if methods else ''}")

# Keep your if __name__ == "__main__" block for direct python execution
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )