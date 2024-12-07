"""AI service for document analysis and conversation"""

from typing import Dict, List, Optional, AsyncGenerator, Tuple
import logging
from litellm import acompletion
from src.core.logging import setup_logger
from src.core.events import event_bus, Event
from src.services.document import DocumentService

logger = setup_logger(__name__)

class AIService:
    MODEL = "gpt-4o"
    
    def __init__(self, db):
        self.db = db
        self.document_service = DocumentService(db)
        logger.info(f"Initialized AIService with model: {self.MODEL}")
        
    async def chat(
        self,
        document_id: str,
        conversation_id: str,
        messages: List[Dict[str, str]],
        stream: bool = True
    ) -> str:
        """Chat with the AI model with streaming support
        
        Args:
            document_id: ID of the document
            conversation_id: ID of the conversation
            messages: List of message dictionaries with role and content
            stream: Whether to stream responses
        """
        try:
            # Log context window
            logger.info(f"AI Service Chat - Document: {document_id}, Conversation: {conversation_id}")
            for i, msg in enumerate(messages):
                logger.info(f"Message {i} ({msg['role']}): {msg['content'][:500]}...")
            
            # Call AI model
            response = ""
            completion = await acompletion(
                model=self.MODEL,
                messages=messages,
                stream=stream
            )
            
            async for chunk in completion:                
                # Extract content from the chunk
                content = chunk.choices[0].delta.content if chunk.choices[0].delta.content else ""
                
                # Emit token event
                await event_bus.emit(Event(
                    type="chat.token",
                    document_id=document_id,
                    data={
                        "conversation_id": conversation_id,
                        "token": content
                    }
                ))
                response += content
                
            # Log final response
            logger.info(f"AI Service Chat Response: {response}")
            
            # Emit completion event
            await event_bus.emit(Event(
                type="chat.completed",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "response": response
                }
            ))
            
            return response
            
        except Exception as e:
            logger.error(f"Error in chat: {str(e)}")
            # Emit error event
            await event_bus.emit(Event(
                type="chat.error",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "error": str(e)
                }
            ))
            raise
            
    async def generate_questions(
        self,
        document_id: str,
        conversation_id: str,
        system_prompt: str,
        user_prompt: str,
    ) -> List[str]:
        """Generate questions using the AI model
        
        Args:
            document_id: ID of the document
            conversation_id: ID of the conversation
            system_prompt: Formatted system prompt
            user_prompt: Formatted user prompt
        """
        try:
            response = await acompletion(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                stream=False
            )
            
            # Parse questions from response
            questions = [q.strip() for q in response.split('\n') if q.strip()]
            # Emit completion event
            await event_bus.emit(Event(
                type="questions.completed",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "questions": questions
                }
            ))
            return questions
            
        except Exception as e:
            logger.error(f"Error generating questions: {str(e)}")
            # Emit error event
            await event_bus.emit(Event(
                type="questions.error",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "error": str(e)
                }
            ))
            raise
            
    async def generate_summary(
        self,
        document_id: str,
        conversation_id: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Generate a summary using the AI model
        
        Args:
            document_id: ID of the document
            conversation_id: ID of the conversation
            system_prompt: Formatted system prompt
            user_prompt: Formatted user prompt
        """
        try:
            response = await acompletion(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                stream=False
            )
            summary = response.strip()
            # Emit completion event
            await event_bus.emit(Event(
                type="summary.completed",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "summary": summary
                }
            ))
            return summary
            
        except Exception as e:
            logger.error(f"Error generating summary: {str(e)}")
            # Emit error event
            await event_bus.emit(Event(
                type="summary.error",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "error": str(e)
                }
            ))
            raise