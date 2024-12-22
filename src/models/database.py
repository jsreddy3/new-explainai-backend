from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Text, Boolean, Float
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSON  
from datetime import datetime
from enum import Enum
import uuid

from src.db.session import Base

class ConversationType(str, Enum):
    MAIN = "main"
    HIGHLIGHT = "highlight"

class User(Base):
    __tablename__ = 'users'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    google_id = Column(String, unique=True)
    name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    meta_data = Column(JSON, nullable=True)
    user_cost = Column(Float, default=0.0)

    # Add relationship to documents
    documents = relationship("Document", back_populates="owner")

class Document(Base):
    __tablename__ = 'documents'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = Column(String, ForeignKey('users.id'))
    title = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = Column(String)
    meta_data = Column(JSON)
    
    # Relationships
    chunks = relationship("DocumentChunk", back_populates="document")
    main_conversation = relationship("Conversation", back_populates="document", 
                                   primaryjoin="and_(Document.id==Conversation.document_id, "
                                             "Conversation.chunk_id==None)", 
                                   uselist=False)
    owner = relationship("User", back_populates="documents")

class DocumentChunk(Base):
    __tablename__ = 'document_chunks'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, ForeignKey('documents.id'))
    content = Column(Text)
    sequence = Column(Integer)
    meta_data = Column(JSON)
    
    # Relationships
    document = relationship("Document", back_populates="chunks")
    conversations = relationship("Conversation", back_populates="chunk")

    def to_dict(self):
        """Convert chunk to dictionary"""
        return {
            "id": self.id,
            "document_id": self.document_id,
            "content": self.content,
            "sequence": self.sequence,
            "meta_data": self.meta_data
        }

class Conversation(Base):
    __tablename__ = 'conversations'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, ForeignKey('documents.id'))
    chunk_id = Column(String, default=None)
    real_chunk_id = Column(String, ForeignKey('document_chunks.id'), nullable=True)
    type = Column(String, default=ConversationType.MAIN)
    created_at = Column(DateTime, default=datetime.utcnow)
    meta_data = Column(JSON)
    is_demo = Column(Boolean, default=False)  # Flag for demo conversations
    
    # Relationships
    document = relationship("Document", back_populates="main_conversation",
                          foreign_keys=[document_id])
    chunk = relationship("DocumentChunk", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation")
    questions = relationship("Question", back_populates="conversation")
    
    def to_dict(self):
      return {
        "id": self.id,
        "document_id": self.document_id,
        "chunk_id": self.chunk_id,
        "type": self.type,
        "meta_data": self.meta_data
      }


class Message(Base):
    __tablename__ = 'messages'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey('conversations.id'))
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    meta_data = Column(JSON, nullable=True)
    chunk_id = Column(String)

    conversation = relationship("Conversation", back_populates="messages")

    def to_dict(self):
        """Convert message to dictionary"""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "meta_data": self.meta_data,
            "chunk_id": self.chunk_id
        }

class Question(Base):
    __tablename__ = 'questions'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey('conversations.id'))
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    answered = Column(Boolean, default=False)
    meta_data = Column(JSON, nullable=True)

    # Relationship to conversation
    conversation = relationship("Conversation", back_populates="questions")

    def to_dict(self):
        """Convert question to dictionary"""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "answered": self.answered,
            "meta_data": self.meta_data
        }