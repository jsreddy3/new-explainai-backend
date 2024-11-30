"""Prompt manager for handling different types of prompts"""

from typing import Dict, Optional, List, Tuple
from .base import (
    MAIN_SYSTEM_PROMPT,
    HIGHLIGHT_SYSTEM_PROMPT,
    CHUNK_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    MAIN_USER_PROMPT,
    HIGHLIGHT_USER_PROMPT,
    CHUNK_USER_PROMPT,
    HIGHLIGHT_QUESTION_USER_PROMPT,
    CHUNK_QUESTION_USER_PROMPT,
    MAIN_QUESTION_USER_PROMPT,
    SUMMARY_USER_PROMPT
)

class PromptManager:
    def get_prompts(self, context: Dict) -> Tuple[str, Optional[str]]:
        """Get the appropriate system and user prompts based on context.
        Returns (system_prompt, user_prompt)"""
        
        # Handle question generation
        if context.get("question_generation"):
            return self._get_question_prompts(context)
            
        # Handle summary generation
        elif context.get("summary_generation"):
            return (
                SUMMARY_SYSTEM_PROMPT,
                SUMMARY_USER_PROMPT.format(
                    highlighted_text=context.get("highlighted_text", ""),
                    conversation_history=self._format_messages(context.get("messages", []))
                )
            )
            
        # Handle highlight analysis
        elif context.get("highlighted_text"):
            return (
                HIGHLIGHT_SYSTEM_PROMPT,
                HIGHLIGHT_USER_PROMPT.format(
                    highlighted_text=context["highlighted_text"],
                    chunk_text=context.get("chunk", {}).get("text", "")
                )
            )
            
        # Handle chunk analysis
        elif context["conversation"]["type"] == "chunk":
            chunk = context.get("chunk", {})
            return (
                CHUNK_SYSTEM_PROMPT,
                CHUNK_USER_PROMPT.format(
                    sequence=chunk.get("sequence", "?"),
                    chunk_text=chunk.get("text", "")
                )
            )
            
        # Handle main document analysis
        else:
            return (
                MAIN_SYSTEM_PROMPT,
                MAIN_USER_PROMPT.format(
                    content=context["document"]["content"],
                    conversation_history=self._format_messages(
                        context.get("messages", [])
                    )
                )
            )
    
    def _get_question_prompts(self, context: Dict) -> Tuple[str, str]:
        """Get prompts specifically for question generation"""
        previous_questions = self._format_previous_questions(context)
        count = context.get("count", 3)
        
        # Questions about highlights
        if context.get("highlighted_text"):
            return (
                QUESTION_SYSTEM_PROMPT,
                HIGHLIGHT_QUESTION_USER_PROMPT.format(
                    highlighted_text=context["highlighted_text"],
                    previous_questions=previous_questions,
                    count=count
                )
            )
            
        # Questions about chunks
        elif context["conversation"]["type"] == "chunk":
            return (
                QUESTION_SYSTEM_PROMPT,
                CHUNK_QUESTION_USER_PROMPT.format(
                    chunk_text=context.get("chunk", {}).get("text", ""),
                    previous_questions=previous_questions,
                    count=count
                )
            )
            
        # Questions about main document
        else:
            return (
                QUESTION_SYSTEM_PROMPT,
                MAIN_QUESTION_USER_PROMPT.format(
                    content=context["document"]["content"],
                    previous_questions=previous_questions,
                    count=count
                )
            )
    
    def _format_messages(self, messages: List[Dict], limit: int = 3) -> str:
        """Format recent messages into a readable conversation history"""
        if not messages:
            return "No previous messages"
        return "\n".join([
            f"{msg['role'].title()}: {msg['content']}"
            for msg in messages[-limit:]
        ])
        
    def _format_previous_questions(self, context: Dict) -> str:
        """Format previous questions from the conversation"""
        messages = context.get("messages", [])
        questions = [
            msg["content"] 
            for msg in messages 
            if msg["role"] == "assistant" and "?" in msg["content"]
        ]
        if not questions:
            return "No previous questions"
        return "\n".join([f"- {q}" for q in questions[-3:]])
