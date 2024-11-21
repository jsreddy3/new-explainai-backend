from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, List

from src.db.session import get_db
from src.services.pdf import PDFService
from src.services.conversation import ConversationService
from src.models.database import Document, DocumentChunk
from src.core.logging import setup_logger

logger = setup_logger(__name__)
router = APIRouter()
pdf_service = PDFService()

@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
) -> Dict:
    """Upload and process a document"""
    try:
        # Process the PDF
        result = await pdf_service.process_pdf(file)
        if not result.success:
            raise HTTPException(status_code=400, detail=result.text)

        # Create document record
        document = Document(
            title=result.display,
            content=result.text,
            status="processed",
            meta_data={
                "topic_key": result.topicKey,
                "chunks_count": len(result.chunks)
            }
        )
        db.add(document)
        await db.flush()
        
        logger.info(f"Created document with ID: {document.id}")

        # Create chunks
        for idx, chunk_content in enumerate(result.chunks):
            chunk = DocumentChunk(
                document_id=document.id,
                content=chunk_content,
                sequence=idx,
                meta_data={
                    "length": len(chunk_content),
                    "index": idx
                }
            )
            db.add(chunk)
        
        # Create main conversation
        conversation_service = ConversationService(db)
        await conversation_service.create_main_conversation(document.id)

        await db.commit()
        await db.refresh(document)
        
        logger.info(f"Successfully processed document with {len(result.chunks)} chunks")
        return {
            "success": True,
            "document_id": str(document.id),
            "title": result.display,
            "topic_key": result.topicKey,
            "chunks_count": len(result.chunks)
        }

    except Exception as e:
        await db.rollback()
        logger.exception(f"Error in upload_document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict:
    """Get document details and its chunks"""
    try:
        logger.info(f"Fetching document with ID: {document_id}")
        
        result = await db.execute(
            select(Document).where(Document.id == document_id)
        )
        document = result.scalar_one_or_none()
        
        if not document:
            logger.warning(f"Document not found: {document_id}")
            raise HTTPException(status_code=404, detail="Document not found")

        logger.info(f"Found document: {document.title}")
        logger.debug(f"Document meta_data: {document.meta_data}")

        result = await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.sequence)
        )
        chunks = result.scalars().all()
        
        logger.info(f"Found {len(chunks)} chunks for document")

        return {
            "id": str(document.id),
            "title": document.title,
            "content": document.content,
            "created_at": str(document.created_at),
            "status": document.status,
            "meta_data": document.meta_data,
            "chunks": [
                {
                    "id": str(chunk.id),
                    "sequence": chunk.sequence,
                    "content": chunk.content,
                    "meta_data": chunk.meta_data
                } for chunk in chunks
            ]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error in get_document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: str,
    db: AsyncSession = Depends(get_db)
) -> List[Dict]:
    """Get chunks for a document"""
    try:
        logger.info(f"Fetching chunks for document ID: {document_id}")
        
        # First verify document exists
        result = await db.execute(
            select(Document).where(Document.id == document_id)
        )
        document = result.scalar_one_or_none()
        
        if not document:
            logger.warning(f"Document not found: {document_id}")
            raise HTTPException(status_code=404, detail="Document not found")
            
        # Get chunks
        result = await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.sequence)
        )
        chunks = result.scalars().all()
        
        logger.info(f"Found {len(chunks)} chunks")
        
        return [
            {
                "id": str(chunk.id),
                "sequence": chunk.sequence,
                "content": chunk.content,
                "meta_data": chunk.meta_data
            } for chunk in chunks
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting document chunks: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/documents")
async def list_documents(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 10
) -> List[Dict]:
    """List all documents with pagination"""
    result = await db.execute(
        select(Document)
        .order_by(Document.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    documents = result.scalars().all()

    return [
        {
            "id": str(doc.id),
            "title": doc.title,
            "created_at": str(doc.created_at),
            "status": doc.status
        }
        for doc in documents
    ]