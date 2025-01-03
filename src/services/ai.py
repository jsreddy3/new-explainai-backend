"""AI service for document analysis and conversation"""

from typing import Dict, List, Optional, AsyncGenerator, Tuple
import logging
from litellm import acompletion, completion_cost
from src.core.logging import setup_logger
from src.core.events import event_bus, Event
from src.utils.message_logger import MessageLogger
from src.utils.memory_tracker import track_memory
from src.services.cost import check_user_cost_limit

logger = setup_logger(__name__)

class AIService:
    # MODEL = "gpt-4o"
    MODEL = "anthropic/claude-3-5-sonnet-20241022"
    
    def __init__(self):
        logger.info(f"Initialized AIService with model: {self.MODEL}")
        self.message_logger = MessageLogger()
        
    async def chat(
        self,
        document_id: str,
        conversation_id: str,
        messages: List[Dict[str, str]],
        connection_id: str,
        request_id: Optional[str] = None,
        stream: bool = True,
        user_id: Optional[str] = None
    ) -> str:
        """Chat with the AI model with streaming support
        
        Args:
            document_id: ID of the document
            conversation_id: ID of the conversation
            messages: List of message dictionaries with role and content
            connection_id: ID of the WebSocket connection
            request_id: Optional request ID for correlation
            stream: Whether to stream responses
            user_id: Optional user ID for cost limit checks
        """
        if user_id:
            await check_user_cost_limit(self.db, user_id)
        
        try:            
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

                # print("Emitting chat token with request id: ", request_id)
                
                await event_bus.emit(Event(
                    type="chat.token",
                    document_id=document_id,
                    connection_id=connection_id,
                    request_id=request_id,
                    data={"token": content}
                ))
                response += content

            # Calculate cost using the input messages and final response
            cost = completion_cost(
                model=self.MODEL,
                messages=messages,  # Your input messages
                completion=response  # The complete response we built
            )

            await self.message_logger.log_exchange(
                document_id=document_id,
                conversation_id=conversation_id,
                messages=messages,
                response=response,
                metadata={
                    "model": self.MODEL,
                    "cost": cost,
                    "stream": stream,
                    "connection_id": connection_id,
                    "request_id": request_id
                }
            )


            # Emit completion event
            await event_bus.emit(Event(
                type="chat.completed",
                document_id=document_id,
                connection_id=connection_id,
                request_id=request_id,
                data={"response": response}
            ))
            
            return response, cost
            
        except Exception as e:
            logger.error(f"Error in chat: {str(e)}")
            
            # Emit error event
            await event_bus.emit(Event(
                type="chat.error",
                document_id=document_id,
                connection_id=connection_id,
                request_id=request_id,
                data={
                    "conversation_id": conversation_id,
                    "error": str(e)
                }
            ))
            raise
            
    async def generate_questions(
        self,
        document_id: str,
        request_id: str,
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
            content = response.choices[0].message.content
            questions = [q.strip() for q in content.split('\n') if q.strip()]
            cost = completion_cost(
                model=self.MODEL,
                messages=messages,
                completion=content
            )

            await self.message_logger.log_exchange(
                document_id=document_id,
                conversation_id=conversation_id,
                messages=messages,
                response=content,
                metadata={
                    "model": self.MODEL,
                    "cost": cost,
                    "type": "question_generation"
                }
            )
            
            # Emit completion event
            await event_bus.emit(Event(
                type="questions.completed",
                document_id=document_id,
                request_id=request_id,
                data={
                    "conversation_id": conversation_id,
                    "questions": questions
                }
            ))
            # logger.info("Generated questions: " + str(questions))
            return questions, cost
            
        except Exception as e:
            logger.error(f"Error generating questions: {str(e)}")
            # Emit error event
            await event_bus.emit(Event(
                type="questions.error",
                document_id=document_id,
                request_id=request_id,
                data={
                    "conversation_id": conversation_id,
                    "error": str(e)
                }
            ))
            raise
            
    async def generate_summary(
        self,
        document_id: str,
        request_id: str,
        conversation_id: str,
        system_prompt: str,
        user_prompt: str,
        user_id: Optional[str] = None
    ) -> str:
        """Generate a summary using the AI model
        
        Args:
            document_id: ID of the document
            conversation_id: ID of the conversation
            system_prompt: Formatted system prompt
            user_prompt: Formatted user prompt
            user_id: Optional user ID for cost limit checks
        """
        if user_id:
            await check_user_cost_limit(self.db, user_id)
        
        try:
            response = await acompletion(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                stream=False
            )
            summary = response.choices[0].message.content.strip()

            cost = completion_cost(
                model=self.MODEL,
                messages=messages,
                completion=summary
            )
            # Emit completion event
            await event_bus.emit(Event(
                type="summary.completed",
                document_id=document_id,
                request_id=request_id,
                data={
                    "conversation_id": conversation_id,
                    "summary": summary
                }
            ))
            # logger.info("Generated summary: " + summary)
            return summary, cost
            
        except Exception as e:
            logger.error(f"Error generating summary: {str(e)}")
            # Emit error event
            await event_bus.emit(Event(
                type="summary.error",
                document_id=document_id,
                request_id=request_id,
                data={
                    "conversation_id": conversation_id,
                    "error": str(e)
                }
            ))
            raise
