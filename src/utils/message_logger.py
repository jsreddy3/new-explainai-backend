"""Utility for logging AI message exchanges"""

import json
import os
from datetime import datetime
from typing import List, Dict
import asyncio
from pathlib import Path
from src.core.logging import setup_logger

logger = setup_logger(__name__)

class MessageLogger:
    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                                  "logs", "message_logs")
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        logger.info(f"MessageLogger initialized with base_dir: {self.base_dir}")

    def _format_messages(self, messages: List[Dict[str, str]], indent: int = 2) -> str:
        """Format messages array in a readable way"""
        formatted = "[\n"
        for msg in messages:
            formatted += " " * indent + "{\n"
            formatted += " " * (indent + 2) + f'"role": "{msg["role"]}",\n'
            
            # Format content with proper indentation for multiline strings
            content_lines = msg["content"].split("\n")
            if len(content_lines) > 1:
                formatted += " " * (indent + 2) + '"content": "\n'
                for line in content_lines:
                    formatted += " " * (indent + 4) + line + "\n"
                formatted += " " * (indent + 2) + '"\n'
            else:
                formatted += " " * (indent + 2) + f'"content": "{msg["content"]}"\n'
            
            formatted += " " * indent + "},\n"
        formatted += "]\n"
        return formatted

    async def log_exchange(self, 
                          document_id: str,
                          conversation_id: str,
                          messages: List[Dict[str, str]],
                          response: str = None,
                          metadata: Dict = None):
        """Log a message exchange asynchronously
        
        Args:
            document_id: ID of the document
            conversation_id: ID of the conversation
            messages: List of message dictionaries
            response: Optional AI response
            metadata: Optional additional metadata
        """
        logger.info(f"Logging exchange for document {document_id}, conversation {conversation_id}")
        logger.debug(f"Messages to log: {messages}")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{timestamp}_{document_id}_{conversation_id}.log"
        
        # Create conversation directory if needed
        conv_dir = self.base_dir / document_id / conversation_id
        
        async with self._lock:
            conv_dir.mkdir(parents=True, exist_ok=True)
            filepath = conv_dir / filename
            
            log_content = f"Timestamp: {datetime.now().isoformat()}\n"
            log_content += f"Document ID: {document_id}\n"
            log_content += f"Conversation ID: {conversation_id}\n"
            
            if metadata:
                log_content += "\nMetadata:\n"
                log_content += json.dumps(metadata, indent=2) + "\n"
            
            log_content += "\nMessages:\n"
            log_content += self._format_messages(messages)
            
            if response:
                log_content += "\nResponse:\n"
                log_content += response + "\n"
            
            # Write log file
            filepath.write_text(log_content)
            logger.info(f"Wrote log file: {filepath}")
