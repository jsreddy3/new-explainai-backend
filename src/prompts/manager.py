"""Prompt manager for handling different types of prompts"""

from typing import Dict, Optional, List, Tuple 
from .base import (
    MAIN_SYSTEM_PROMPT,
    MAIN_USER_PROMPT,
    HIGHLIGHT_SYSTEM_PROMPT,
    HIGHLIGHT_USER_PROMPT,
    HIGHLIGHT_QUESTION_SYSTEM_PROMPT,
    HIGHLIGHT_QUESTION_USER_PROMPT,
    MAIN_QUESTION_SYSTEM_PROMPT,
    MAIN_QUESTION_USER_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT
)

from src.core.logging import setup_logger

logger = setup_logger(__name__)

class PromptManager:
    def __init__(self):
        pass
        
    def create_main_system_prompt(self, chunk_text: str) -> str:
        """Create system prompt for main conversation"""
        return MAIN_SYSTEM_PROMPT.format(chunk_text=chunk_text)
        
    def create_highlight_system_prompt(self, chunk_text: str, highlight_text: str) -> str:
        """Create system prompt for highlight conversation"""
        return HIGHLIGHT_SYSTEM_PROMPT.format(
            chunk_text=chunk_text,
            highlight_text=highlight_text
        )
        
    def create_main_user_prompt(self, user_message: str) -> str:
        """Create user prompt for main conversation
        
        Args:
            user_message: Original user message
        """
        return MAIN_USER_PROMPT.format(user_message=user_message)
        
    def create_highlight_user_prompt(self, user_message: str) -> str:
        """Create user prompt for highlight conversation
        
        Args:
            user_message: Original user message
        """
        return HIGHLIGHT_USER_PROMPT.format(user_message=user_message)
        
    def create_main_question_prompts(self, chunk_text: str, count: int, previous_questions: str = "") -> Tuple[str, str]:
        """Create prompts for main conversation question generation"""
        return (
            MAIN_QUESTION_SYSTEM_PROMPT.format(
                chunk_text=chunk_text,
                previous_questions=previous_questions
            ),
            MAIN_QUESTION_USER_PROMPT.format(count=count)
        )
        
    def create_highlight_question_prompts(
        self, 
        chunk_text: str,
        highlight_text: str,
        count: int,
        previous_questions: str = ""
    ) -> Tuple[str, str]:
        """Create prompts for highlight conversation question generation"""
        return (
            HIGHLIGHT_QUESTION_SYSTEM_PROMPT.format(
                chunk_text=chunk_text,
                highlight_text=highlight_text,
                previous_questions=previous_questions
            ),
            HIGHLIGHT_QUESTION_USER_PROMPT.format(count=count)
        )
        
    def create_summary_prompts(
        self,
        chunk_text: str,
        highlight_text: str,
        conversation_history: str
    ) -> Tuple[str, str]:
        """Create prompts for conversation summarization"""
        return (
            SUMMARY_SYSTEM_PROMPT.format(chunk_text=chunk_text),
            SUMMARY_USER_PROMPT.format(
                highlight_text=highlight_text,
                conversation_history=conversation_history
            )
        )

    def create_chat_system_prompt(self, messages: List[Dict]) -> str:
        """Create system prompt for chat"""
        # Format conversation history
        history = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in messages
        ])
        return f"""You are an AI assistant helping a user understand a document.
Here is the conversation history:

{history}

Please provide a helpful response to the user's next message."""
