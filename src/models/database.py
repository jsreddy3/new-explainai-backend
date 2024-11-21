from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.sqlite import JSON
from datetime import datetime
import uuid

from src.db.session import Base

class User(Base):
    __tablename__ = 'users'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    meta_data = Column(JSON, nullable=True)

class Document(Base):
    __tablename__ = 'documents'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = Column(String)
    meta_data = Column(JSON)  # Changed from metadata to meta_data
    
    # Relationships
    chunks = relationship("DocumentChunk", back_populates="document")
    main_conversation = relationship("Conversation", back_populates="document", 
                                   primaryjoin="and_(Document.id==Conversation.document_id, "
                                             "Conversation.chunk_id==None)", 
                                   uselist=False)

class DocumentChunk(Base):
    __tablename__ = 'document_chunks'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, ForeignKey('documents.id'))
    content = Column(Text)
    sequence = Column(Integer)
    meta_data = Column(JSON)  # Changed from metadata to meta_data
    
    # Relationships
    document = relationship("Document", back_populates="chunks")
    conversations = relationship("Conversation", back_populates="chunk")

class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, ForeignKey('documents.id'))
    chunk_id = Column(String, ForeignKey('document_chunks.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    meta_data = Column(JSON)  # Changed from metadata to meta_data
    
    # Relationships
    document = relationship("Document", back_populates="main_conversation",
                          foreign_keys=[document_id])
    chunk = relationship("DocumentChunk", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation")

class Message(Base):
    __tablename__ = 'messages'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey('conversations.id'))
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    conversation = relationship("Conversation", back_populates="messages")