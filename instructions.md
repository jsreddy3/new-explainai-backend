# ExplainAI Backend Specification

## Project Overview
ExplainAI is an interactive document analysis system that enables users to have AI-powered conversations about documents. The system processes uploaded documents (initially PDFs), chunks them into manageable sections, and facilitates both global document-level conversations and specific chunk-level discussions.

ExplainAI's backend manages document-based conversations through a structured relationship system. When a document is uploaded, it's stored and mapped to two key conversation structures: (1) a single "main" conversation that spans the entire document, and (2) multiple "chunk" conversations that are mapped to specific ranges within the document's chunks. The document is processed into sequential chunks, with each chunk potentially containing multiple highlight-specific conversations. These chunk-specific conversations are stored in a mapping where the keys are index ranges within the chunk (e.g., {(start_index, end_index): conversation_object}). This allows users to maintain separate conversations about specific highlighted portions of text while preserving one overarching conversation about the document as a whole. Each conversation object, whether main or chunk-specific, maintains its own message history, participant roles, and metadata, while remaining linked to either the full document or its specific text range.
---

## Core Architecture

### Database Design
We use SQLAlchemy as our ORM with the following core models:
- **Document**: Represents uploaded documents with their processed content.
- **DocumentChunk**: Represents segments of documents with sequence tracking.
- **Conversation**: Represents both document-level and chunk-level conversations.
- **Message**: Individual messages within conversations.

---

### Key Components

#### Document Processing System
- Integrated PDF processing using LlamaParse for text extraction.
- Automatic chunking with configurable chunk sizes.
- Document metadata extraction and storage.

**Processing pipeline:**
1. Document upload and validation.
2. Text extraction and cleaning.
3. Intelligent chunking.
4. Database record creation.
5. Initial conversation setup.

#### Conversation Management
- Support for two types of conversations:
  - Document-level (main) conversations about the entire document.
  - Chunk-specific conversations tied to highlighted portions.
- Conversation state management through the database.
- Message history tracking with timestamps.
- Support for both user and AI messages.

---

## Directory Structure
backend/
├── src/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── conversation.py  # moved from conversation_endpoints.py
│   │   │   └── document.py      # new file for document-related endpoints
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py            # configuration management
│   │   └── logging.py           # centralized logging
│   ├── services/
│   │   ├── __init__.py
│   │   ├── conversation.py      # business logic from conversation_manager.py
│   │   ├── document.py          # document processing logic
│   │   └── pdf.py              # refactored from pdf_processing
│   ├── models/
│   │   ├── __init__.py
│   │   └── database.py         # your existing models
│   └── db/
│       ├── __init__.py
│       └── session.py          # database connection management
├── tests/
└── main.py

---

## Key Principles

### Separation of Concerns
- **Routes (`api/routes/`)**:
  - Handle HTTP requests/responses.
  - Input validation.
  - Route to appropriate services.
  - No direct database operations.
- **Services (`services/`)**:
  - Contain core business logic.
  - Handle database operations.
  - Manage document processing.
  - Coordinate between different system components.
- **Models (`models/`)**:
  - Define database schema.
  - Handle data relationships.
  - Provide type hints and validation.

### Configuration Management
- **Environment-based configuration**.
- Configurable parameters:
  - Maximum document size.
  - Chunk size limits.
  - Database connection details.
  - API keys for external services.
  - Processing timeout limits.

### Error Handling
- Consistent error response format.
- Detailed error logging.
- Appropriate HTTP status codes.
- Transaction management for database operations.

---

## API Endpoints

### Document Management
- **POST** `/api/documents`
- **GET** `/api/documents/{document_id}`
- **GET** `/api/documents/{document_id}/chunks`
- **DELETE** `/api/documents/{document_id}`

### Conversation Management
- **POST** `/api/conversations`
- **GET** `/api/conversations/{conversation_id}`
- **POST** `/api/conversations/{conversation_id}/messages`
- **GET** `/api/conversations/{conversation_id}/messages`

### Document Processing
- **POST** `/api/documents/process`
- **GET** `/api/documents/{document_id}/status`

---

## Implementation Tasks (Ordered)

1. **Database Setup**
   - Implement base models.
   - Set up migrations.
   - Configure database connection.
2. **Document Processing**
   - PDF processing integration.
   - Chunking logic.
   - Document storage.
3. **Conversation System**
   - Conversation creation.
   - Message handling.
   - State management.
4. **API Routes**
   - Document endpoints.
   - Conversation endpoints.
   - Error handling middleware.
5. **Testing**
   - Unit tests.
   - Integration tests.
   - Load testing.
6. **Documentation**
   - API documentation.
   - System documentation.
   - Deployment guide.

---

## Configuration Parameters

# config.py constants
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024  # 10MB
DEFAULT_CHUNK_SIZE = 50000
MAX_CHUNKS_PER_DOC = 100
SUPPORTED_MIME_TYPES = ['application/pdf']
DB_CONNECTION_TIMEOUT = 30
PROCESSING_TIMEOUT = 300
Database Schema Details

Document
CREATE TABLE documents (
    id UUID PRIMARY KEY,
    title VARCHAR(255),
    content TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    status VARCHAR(50),
    metadata JSONB
);
DocumentChunk
CREATE TABLE document_chunks (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id),
    content TEXT,
    sequence INTEGER,
    metadata JSONB
);
Conversation
CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id),
    chunk_id UUID REFERENCES document_chunks(id),
    created_at TIMESTAMP,
    metadata JSONB
);
Message
CREATE TABLE messages (
    id UUID PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(id),
    role VARCHAR(50),
    content TEXT,
    created_at TIMESTAMP
);
Error Handling Strategy

All endpoints should return errors in the following format:
{
    "error": {
        "code": "ERROR_CODE",
        "message": "Human readable message",
        "details": {}
    }
}
Common error codes:
DOCUMENT_NOT_FOUND
INVALID_DOCUMENT_FORMAT
PROCESSING_ERROR
CONVERSATION_NOT_FOUND
DATABASE_ERROR
VALIDATION_ERROR