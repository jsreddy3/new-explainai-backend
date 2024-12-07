from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Any, TYPE_CHECKING
from datetime import datetime

@dataclass
class DocumentData:
    """Represents document-related information"""
    id: str
    title: str
    content: str
    created_at: datetime
    
    def get_chunks(self, db_session) -> List['DocumentChunk']:
        """Fetch all chunks for this document"""
        return db_session.query(DocumentChunk).filter_by(document_id=self.id).order_by(DocumentChunk.sequence).all()
    
    def get_chunk(self, db_session, chunk_id: str) -> Optional['DocumentChunk']:
        """Fetch a specific chunk by ID"""
        return db_session.query(DocumentChunk).filter_by(document_id=self.id, id=chunk_id).first()

@dataclass
class MainConversationData:
    """Data for conversations with context management"""
    id: str
    document_id: str
    type: ConversationType
    
    # For main conversations
    current_chunks: List[str] = field(default_factory=list)
    chunk_switch_history: List[Dict[str, Any]] = field(default_factory=list)
    
    def get_document(self, db_session) -> Optional['Document']:
        """Fetch the associated document"""
        return db_session.query(Document).filter_by(id=self.document_id).first()
    
    def get_messages(self, db_session, limit: Optional[int] = None) -> List['Message']:
        """Fetch messages for this conversation"""
        query = db_session.query(Message).filter_by(conversation_id=self.id).order_by(Message.created_at)
        if limit:
            query = query.limit(limit)
        return query.all()
    
    def add_chunk_switch(self, old_chunk_id: str, new_chunk_id: str):
        """Track chunk switches for main conversations"""
        if self.type == ConversationType.MAIN:
            self.chunk_switch_history.append({
                'old_chunk_id': old_chunk_id,
                'new_chunk_id': new_chunk_id,
                'timestamp': datetime.utcnow()
            })
            # Update current chunks, keeping most recent first
            if old_chunk_id in self.current_chunks:
                self.current_chunks.remove(old_chunk_id)
            self.current_chunks.insert(0, new_chunk_id)

@dataclass
class HighlightConversationData:
    """Data for conversations focused on specific highlights"""
    id: str
    document_id: str
    
    # Highlight-specific information
    highlighted_text: Optional[str] = None
    highlight_start_index: Optional[int] = None
    highlight_end_index: Optional[int] = None
    chunk_id: Optional[str] = None  # The chunk this highlight is from
    
    def get_document(self, db_session) -> Optional['Document']:
        """Fetch the associated document"""
        return db_session.query(Document).filter_by(id=self.document_id).first()
    
    def get_messages(self, db_session, limit: Optional[int] = None) -> List['Message']:
        """Fetch messages for this conversation"""
        query = db_session.query(Message).filter_by(conversation_id=self.id).order_by(Message.created_at)
        if limit:
            query = query.limit(limit)
        return query.all()

@dataclass
class MessageData:
    """Represents message-specific information"""
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: datetime
    
    # Chunk context for this specific message
    chunk_id: Optional[str] = None
    
    # Optional rich context (only for the most recent message)
    context: Optional[Dict[str, Any]] = None
    
    def get_conversation(self, db_session) -> Optional['Conversation']:
        """Fetch the associated conversation"""
        return db_session.query(Conversation).filter_by(id=self.conversation_id).first()