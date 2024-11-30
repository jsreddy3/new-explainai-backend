"""AI service for document analysis and conversation"""

from typing import Dict, List, Optional, AsyncGenerator
import logging
from litellm import acompletion
import json
from ..core.config import settings
from ..core.logging import setup_logger, log_with_context
from ..core.events import event_bus, Event
from ..prompts import PromptManager

# Set up logger with explicit level
logger = setup_logger(__name__)
logger.setLevel(logging.DEBUG)  # Ensure we're logging at DEBUG level

class AIService:
    MODEL = "gpt-4o"  # Class variable for model selection
    
    def __init__(self):
        self.prompt_manager = PromptManager()
        # logger.info(f"Initialized AIService with model: {self.MODEL}")
        
    async def chat(
        self,
        document_id: str,
        conversation_id: str,
        messages: List[Dict],
        stream: bool = True
    ) -> AsyncGenerator[str, None]:
        """Chat with the AI model with streaming support"""
        try:
            response = await acompletion(
                model=self.MODEL,
                messages=messages,
                stream=True,  # Always stream for real-time updates
                temperature=0.7,
            )
            
            full_response = ""
            async for chunk in response:
                if not chunk or not chunk.choices:
                    continue
                    
                content = chunk.choices[0].delta.content
                if content:
                    full_response += content
                    # Emit token event
                    await event_bus.emit(Event(
                        type="message.token",
                        document_id=document_id,
                        data={
                            "conversation_id": conversation_id,
                            "token": content
                        }
                    ))
                    # Only yield if streaming is requested
                    if stream:
                        yield content
            
            # Emit completion event
            await event_bus.emit(Event(
                type="message.completed",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "content": full_response
                }
            ))
            
            # If not streaming, return full response at once
            if not stream:
                yield full_response
                
        except Exception as e:
            error_msg = f"Error in chat: {str(e)}"
            logger.error(error_msg)
            # Emit error event
            await event_bus.emit(Event(
                type="message.error",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "error": error_msg
                }
            ))
            raise

    async def generate_questions(
        self,
        document_id: str,
        conversation_id: str,
        context: Dict,
        count: int = 3
    ) -> List[str]:
        """Generate questions based on context"""
        try:
            print("Starting question generation")  # Debug print
            logger.info("Starting question generation", extra={
                "model": self.MODEL,
                "context_type": context.get("conversation", {}).get("type"),
                "chunk_id": context.get("chunk", {}).get("id")
            })
            
            context["question_generation"] = True
            context["count"] = count
            messages = self._build_messages(context)
            print(f"Built messages: {len(messages)} messages")  # Debug print
            logger.debug(f"Built messages for question generation: {len(messages)} messages")
            
            async for question in self.chat(document_id, conversation_id, messages, stream=False):
                full_response = question
                
            # Parse questions from response
            questions = [
                q.strip() for q in full_response.split("\n")
                if "?" in q
            ]
            # Emit completion event
            await event_bus.emit(Event(
                type="questions.completed",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "questions": questions[:count]
                }
            ))
            
            return questions[:count]
        except Exception as e:
            print(f"Error generating questions: {str(e)}")  # Debug print
            logger.error(f"Error generating questions: {str(e)}", extra={
                "error_type": type(e).__name__,
                "context_type": context.get("conversation", {}).get("type")
            })
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
            
    async def summarize_conversation(
        self,
        document_id: str,
        conversation_id: str,
        context: Dict
    ) -> str:
        """Summarize a conversation about a highlight
        
        Args:
            context: Dictionary containing:
                - messages: List of conversation messages
                - highlighted_text: The text being discussed
                - document_content: Full document content (optional)
                - chunk_content: Content of the current chunk (optional)
        """
        try:
            print("Starting conversation summarization")  # Debug print
            logger.info("Starting conversation summarization", extra={
                "model": self.MODEL,
                "context_type": context.get("conversation", {}).get("type"),
                "chunk_id": context.get("chunk", {}).get("id")
            })
            
            # Ensure we have the minimum required context
            if "messages" not in context or "highlighted_text" not in context:
                raise ValueError("Missing required context for summarization")
                
            # Format conversation history
            conversation_history = "\n".join([
                f"{msg['role'].upper()}: {msg['content']}"
                for msg in context["messages"]
            ])
            
            # Build summarization context
            summary_context = {
                "summary_generation": True,
                "highlighted_text": context["highlighted_text"],
                "conversation_history": conversation_history,
                "document_content": context.get("document_content", ""),
                "chunk_content": context.get("chunk_content", "")
            }
            
            # Get AI completion
            messages = self._build_messages(summary_context)
            print(f"Built messages: {len(messages)} messages")  # Debug print
            logger.debug(f"Built messages for conversation summarization: {len(messages)} messages")
            
            async for summary in self.chat(document_id, conversation_id, messages, stream=False):
                full_response = summary
                
            # Emit completion event
            await event_bus.emit(Event(
                type="summary.completed",
                document_id=document_id,
                data={
                    "conversation_id": conversation_id,
                    "summary": full_response.strip()
                }
            ))
            
            return full_response.strip()
            
        except Exception as e:
            print(f"Error summarizing conversation: {str(e)}")  # Debug print
            logger.error(f"Error summarizing conversation: {str(e)}", extra={
                "error_type": type(e).__name__,
                "context_type": context.get("conversation", {}).get("type")
            })
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
            
    def _build_messages(self, context: Dict) -> List[Dict]:
        """Build the messages list for the AI, including system and user prompts"""
        try:
            print("Building messages for AI")  # Debug print
            logger.debug("Building messages for AI", extra={
                "context_type": context.get("conversation", {}).get("type"),
                "chunk_id": context.get("chunk", {}).get("id")
            })
            
            # Get appropriate prompts
            system_prompt, user_prompt = self.prompt_manager.get_prompts(context)
            
            # Start with system message
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add conversation history if any
            if context.get("messages"):
                messages.extend(context["messages"])
                
            # Add user prompt if provided
            if user_prompt:
                messages.append({"role": "user", "content": user_prompt})
                
            return messages
        except Exception as e:
            print(f"Error building messages: {str(e)}")  # Debug print
            logger.error(f"Error building messages: {str(e)}", extra={
                "error_type": type(e).__name__,
                "context_type": context.get("conversation", {}).get("type")
            })
            raise