from fastapi import UploadFile, File, HTTPException
import os
from typing import Optional, List, Dict, Tuple
import litellm
from tempfile import NamedTemporaryFile
from pathlib import Path
from datetime import datetime
import time
from pydantic import BaseModel
import PyPDF2
import logging
import nest_asyncio
import yaml

from src.core.config import Settings
from src.core.logging import setup_logger, log_with_context

# Apply nest_asyncio to allow nested async loops
nest_asyncio.apply()

logger = setup_logger(__name__)
settings = Settings()

# Constants
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
CHUNK_SIZE = 5000  # Characters per chunk
LOG_TRUNCATION_LENGTH = 500  # For truncating long log messages

# System prompt for PDF processing
SYSTEM_PROMPT = """You are a precise text formatter. Your task is to clean up OCR-extracted text from PDFs while preserving the exact structure and meaning of the original document.

Rules:
1. Maintain all paragraph breaks exactly as they appear in the original
2. Fix OCR errors (like misrecognized characters, unwanted hyphens)
3. Preserve all meaningful whitespace
4. Do not add or remove content
5. Do not add interpretations or summaries
6. Keep all original line breaks that indicate structural elements (like lists or titles)
7. The OCR may have split every word into a separate line. If so, join them back together properly
8. Remove artifacts like page numbers or header/footer repetitions

Output only the cleaned text, with no explanations or metadata.

Example input:
"L0rem ips-
um d0lor sit amet, consectetur adipiscing elit.

Se d d0 eius- 
mod tempor incididunt ut lab0re.
Page 2

Ut enim ad min-
im veniam, quis."

Example output:
"Lorem ipsum dolor sit amet, consectetur adipiscing elit.

Sed do eiusmod tempor incididunt ut labore.

Ut enim ad minim veniam, quis."
"""

class PDFResponse(BaseModel):
    success: bool
    topicKey: str
    display: str
    text: str
    chunks: List[str]

class PDFService:
    def __init__(self):
        self.max_file_size = MAX_FILE_SIZE
        
    async def validate_pdf_file(self, file: UploadFile) -> None:
        """Validate that the uploaded file is a PDF and within size limits"""
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="File must be a PDF")
        
        content = await file.read()
        await file.seek(0)  # Reset file pointer
        
        if len(content) > self.max_file_size:
            raise HTTPException(status_code=413, detail="File size too large. Maximum size is 10MB")

    def clean_llm_output(self, text: str) -> str:
        """Clean and standardize LLM output"""
        return text.strip()

    def chunk_text(self, text: str, chunk_size: int = CHUNK_SIZE) -> List[str]:
        """Split text into manageable chunks"""
        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    async def process_chunk(self, chunk: str, chunk_num: int, previous_messages: List[Dict[str, str]] = None) -> Tuple[str, List[Dict[str, str]]]:
        """Process a single chunk of text using LiteLLM"""
        try:
            start_time = time.time()
            messages = previous_messages or [{"role": "system", "content": SYSTEM_PROMPT}]
            messages.append({"role": "user", "content": chunk})
            
            logger.info(f"Processing chunk {chunk_num}", extra={
                "chunk_number": chunk_num,
                "chunk_length": len(chunk)
            })
            
            response = await litellm.acompletion(
                model="gemini/gemini-1.5-flash",
                messages=messages
            )
            
            process_time = time.time() - start_time
            processed_text = response.choices[0].message.content
            
            # Add assistant's response to conversation history
            messages.append({"role": "assistant", "content": processed_text})
            
            return self.clean_llm_output(processed_text), messages
        except Exception as e:
            logger.error(f"Error processing chunk {chunk_num}", extra={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "chunk_number": chunk_num
            })
            return chunk, messages  # Fallback to original text if processing fails

    async def extract_text_from_pdf(self, file: UploadFile, max_pages: int = 50) -> Tuple[str, str]:
        """Extract text and title from PDF file"""
        try:
            # Get the original filename from UploadFile
            filename = file.filename.replace('.pdf', '')
            
            # Read PDF using the file object
            pdf_reader = PyPDF2.PdfReader(file.file)
            
            # Try to get title from metadata, fallback to filename
            title = (
                pdf_reader.metadata.get('/Title', '') if pdf_reader.metadata
                else filename
            )
            
            # If title is empty or an IndirectObject, use filename
            if not title or 'IndirectObject' in str(title):
                title = filename
                
            # Extract text from first max_pages pages only
            if len(pdf_reader.pages) > max_pages:
                logger.info(f"PDF has {len(pdf_reader.pages)} pages, only processing first {max_pages} pages")

            text = ""
            for page in pdf_reader.pages[:max_pages]:
                text += page.extract_text() + "\n"
                
            logger.info(f"Extracted title: {title}")
            return text.strip(), title.strip()

        except Exception as e:
            logger.error(f"Error extracting text from PDF: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error processing PDF: {str(e)}"
            )

    async def process_pdf_text(self, text: str) -> Tuple[str, List[str]]:
        """Process PDF text in chunks and return both full text and individual chunks"""
        chunks = self.chunk_text(text)
        processed_chunks = []
        conversation_history = None
        
        for i, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {i+1}/{len(chunks)}")
            try:
                processed_chunk, conversation_history = await self.process_chunk(chunk, i, conversation_history)
                processed_chunks.append(processed_chunk)
            except Exception as e:
                logger.error(f"Error processing chunk {i+1}: {str(e)}")
                processed_chunks.append(chunk)  # Fall back to original chunk if processing fails
        
        # Combine all processed chunks
        full_text = "\n".join(processed_chunks)
        return full_text, processed_chunks

    async def process_pdf(self, file: UploadFile, max_pages: int = 50) -> PDFResponse:
        """Process PDF file and return structured response"""
        await self.validate_pdf_file(file)
        
        try:
            # Extract text and title
            text, title = await self.extract_text_from_pdf(file, max_pages)
            
            # Process the text
            processed_text, chunks = await self.process_pdf_text(text)
            
            return PDFResponse(
                success=True,
                topicKey=f"pdf-{title}-{hash(str(processed_text))%10000:04d}",
                display=title,
                text=processed_text,
                chunks=chunks
            )
            
        except Exception as e:
            logger.error(f"Error processing PDF: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    async def process_pdf_stream(self, file: UploadFile, max_pages: int = 50):
        """Process PDF file and yield chunks as they're processed"""
        try:
            await self.validate_pdf_file(file)
            
            # Extract text and title
            text, title = await self.extract_text_from_pdf(file, max_pages)
            
            # Split into chunks
            chunks = self.chunk_text(text)
            conversation_history = None
            
            for i, chunk in enumerate(chunks):
                try:
                    # Process chunk
                    processed_chunk, conversation_history = await self.process_chunk(chunk, i, conversation_history)
                    
                    # Yield chunk response
                    yield {
                        "success": True,
                        "topicKey": f"pdf-{title}-{hash(str(processed_chunk))%10000:04d}",
                        "display": title,
                        "text": processed_chunk,
                        "chunk_number": i,
                        "total_chunks": len(chunks)
                    }
                    
                except Exception as e:
                    logger.error(f"Error processing chunk {i}: {str(e)}")
                    yield {
                        "success": False,
                        "error": f"Error processing chunk {i}: {str(e)}",
                        "chunk_number": i,
                        "total_chunks": len(chunks)
                    }
                    
        except Exception as e:
            logger.error(f"Error in PDF stream processing: {str(e)}")
            yield {
                "success": False,
                "error": f"Error in PDF stream processing: {str(e)}",
                "chunk_number": 0,
                "total_chunks": 0
            }