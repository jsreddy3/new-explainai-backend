"""AI service for document analysis and conversation"""

from typing import Dict, List, Optional, AsyncGenerator, Tuple
import logging
from litellm import acompletion
from src.core.logging import setup_logger
from src.core.events import event_bus, Event
from src.utils.message_logger import MessageLogger
from src.utils.memory_tracker import track_memory, log_memory

logger = setup_logger(__name__)

class AIService:
    MODEL = "gpt-4o"
    
    @track_memory("AIService")
    def __init__(self):
        logger.info(f"Initialized AIService with model: {self.MODEL}")
        self.message_logger = MessageLogger()
        
    @track_memory("AIService")
    async def chat(
        self,
        document_id: str,
        conversation_id: str,
        messages: List[Dict[str, str]],
        connection_id: str,
        stream: bool = True
    ) -> str:
        try:
            logger.info(f"AI Service Chat - Document: {document_id}, Conversation: {conversation_id}")
            log_memory("AIService", "before_completion")
            logger.info(f"Message count: {len(messages)}, Total message content size: {sum(len(m.get('content', '')) for m in messages)}")
            logger.debug(f"Messages: {messages}")  # Let's see the full messages
            
            # Call AI model
            logger.info("Calling AI model...")
            response = ""
            log_memory("AIService", "before_litellm")
            completion = await acompletion(
                model=self.MODEL,
                messages=messages,
                stream=stream
            )
            log_memory("AIService", "after_litellm_before_first_chunk")
            
            # Inspect completion object
            logger.debug(f"Completion object type: {type(completion)}")
            logger.debug(f"Completion object dir: {dir(completion)}")
            
            log_memory("AIService", "after_completion_before_streaming")
            response_buffer = []
            buffer_size = 0
            chunk_count = 0
            
            async for chunk in completion:
                chunk_count += 1
                if chunk_count == 1:
                    logger.debug(f"First chunk type: {type(chunk)}")
                    logger.debug(f"First chunk dir: {dir(chunk)}")
                    log_memory("AIService", "after_first_chunk")
                
                # Extract content from the chunk
                content = chunk.choices[0].delta.content if chunk.choices[0].delta.content else ""
                buffer_size += len(content)
                response_buffer.append(content)
                
                # Emit token event
                await event_bus.emit(Event(
                    type="chat.token",
                    document_id=document_id,
                    connection_id=connection_id,
                    data={"token": content}
                ))
                
                # Log memory every 20 tokens and include buffer size
                if buffer_size % 20 == 0:
                    log_memory("AIService", f"streaming_progress_{buffer_size}")
                    logger.debug(f"Response buffer size: {buffer_size} chars, Chunk count: {chunk_count}")
            
            response = "".join(response_buffer)
            log_memory("AIService", "after_streaming")
            logger.info(f"Final response size: {len(response)} chars, Total chunks: {chunk_count}")
            
            # Clear buffers explicitly
            response_buffer.clear()
            del response_buffer
            
            # Emit completion event
            await event_bus.emit(Event(
                type="chat.completed",
                document_id=document_id,
                connection_id=connection_id,
                data={"response": response}
            ))
            
            log_memory("AIService", "after_completion_event")
            return response
            
        except Exception as e:
            logger.error(f"Error in chat: {str(e)}")
            log_memory("AIService", "error_state")
            
            # Log error
            # await self.message_logger.log_exchange(
            #     document_id=document_id,
            #     conversation_id=conversation_id,
            #     messages=messages,
            #     metadata={
            #         "model": self.MODEL,
            #         "stream": stream,
            #         "connection_id": connection_id,
            #         "status": "error",
            #         "error": str(e)
            #     }
            # )
            
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
            
    @track_memory("AIService")
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
            content = response.choices[0].message.content
            questions = [q.strip() for q in content.split('\n') if q.strip()]
            
            # Emit completion event
            await event_bus.emit(Event(
                type="questions.completed",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "questions": questions
                }
            ))
            # logger.info("Generated questions: " + str(questions))
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
            
    @track_memory("AIService")
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
            summary = response.choices[0].message.content.strip()
            # Emit completion event
            await event_bus.emit(Event(
                type="summary.completed",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "summary": summary
                }
            ))
            # logger.info("Generated summary: " + summary)
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