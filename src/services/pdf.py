from fastapi import UploadFile, File, HTTPException
import os
from typing import Optional, List
import litellm
from tempfile import NamedTemporaryFile
from pathlib import Path
from datetime import datetime
from pydantic import BaseModel
from llama_parse import LlamaParse
import PyPDF2
import logging
import nest_asyncio

from src.core.config import Settings
from src.core.logging import setup_logger

# Apply nest_asyncio to allow nested async loops
nest_asyncio.apply()

logger = setup_logger(__name__)
settings = Settings()

# Constants
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
CHUNK_SIZE = 2500  # Reduced from 50000 to 2500 characters
LOG_TRUNCATION_LENGTH = 500  # For truncating long log messages

# System prompt for PDF processing
SYSTEM_PROMPT = """You are an AI assistant specialized in processing and analyzing PDF documents.
Your task is to:
1. Understand and extract key information from the document
2. Maintain the original meaning while improving readability
3. Organize the content in a clear, structured format
4. Preserve important technical details and terminology"""

class PDFResponse(BaseModel):
    success: bool
    topicKey: str
    display: str
    text: str
    chunks: List[str]

class PDFService:
    def __init__(self):
        self.llama_parse = LlamaParse(
            api_key=settings.LLAMA_CLOUD_API_KEY,
            result_type="markdown"  # Use markdown to preserve structure
        )
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

    async def process_chunk(self, chunk: str, chunk_num: int) -> str:
        """Process a single chunk of text using LiteLLM"""
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Process this document chunk (#{chunk_num}): {chunk}"}
            ]
            
            logger.info(f"Processing chunk {chunk_num}")
            logger.debug(f"Chunk content (truncated): {chunk[:LOG_TRUNCATION_LENGTH]}")
            
            response = await litellm.acompletion(
                model="gemini/gemini-1.5-flash",
                messages=messages
            )
            
            processed_text = response.choices[0].message.content
            logger.debug(f"Processed chunk {chunk_num} (truncated): {processed_text[:LOG_TRUNCATION_LENGTH]}")
            
            return self.clean_llm_output(processed_text)
        except Exception as e:
            logger.error(f"Error processing chunk {chunk_num}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error processing document chunk: {str(e)}")

    def chunk_text(self, text: str) -> List[str]:
        """Split text into manageable chunks"""
        logger.info(f"Splitting text of length {len(text)} into chunks of size {CHUNK_SIZE}")
        chunks = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
        logger.info(f"Created {len(chunks)} chunks")
        for i, chunk in enumerate(chunks):
            logger.debug(f"Chunk {i+1} length: {len(chunk)}")
            logger.debug(f"Chunk {i+1} preview: {chunk[:100]}...")
        return chunks

    async def process_pdf_text(self, text: str) -> tuple[str, List[str]]:
        """Process PDF text in chunks"""
        chunks = self.chunk_text(text)
        processed_chunks = []
        
        logger.info(f"Starting to process {len(chunks)} chunks")
        for i, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {i+1}/{len(chunks)} (length: {len(chunk)})")
            logger.debug(f"Raw chunk {i+1}: {chunk[:LOG_TRUNCATION_LENGTH]}...")
            processed_chunk = await self.process_chunk(chunk, i + 1)
            logger.debug(f"Processed chunk {i+1}: {processed_chunk[:LOG_TRUNCATION_LENGTH]}...")
            processed_chunks.append(processed_chunk)
        
        full_text = "\n".join(processed_chunks)
        logger.info(f"Finished processing all chunks. Total processed text length: {len(full_text)}")
        return full_text, processed_chunks

    async def extract_text_from_pdf(self, content: bytes) -> tuple[str, str]:
        """Extract text and title from PDF using llama-parse with PyPDF2 fallback"""
        with NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            temp_file.write(content)
            temp_file.flush()
            
            try:
                logger.info(f"Starting PDF text extraction with LlamaParse")
                # First try with LlamaParse
                result = self.llama_parse.load_data(temp_file.name)
                text = result[0].text if result else ""
                title = result[0].metadata.get('title', '') if result else ""
                
                # Log the extraction results
                logger.info(f"LlamaParse extraction complete. Text length: {len(text)}")
                logger.debug(f"Extracted text preview: {text[:LOG_TRUNCATION_LENGTH]}...")
                
                # Fallback to PyPDF2 if no text extracted
                if not text.strip():
                    logger.info("LlamaParse extraction failed, falling back to PyPDF2")
                    with open(temp_file.name, 'rb') as pdf_file:
                        pdf_reader = PyPDF2.PdfReader(pdf_file)
                        text = '\n'.join(page.extract_text() for page in pdf_reader.pages)
                        title = os.path.basename(temp_file.name)
                    logger.info(f"PyPDF2 extraction complete. Text length: {len(text)}")
                    logger.debug(f"PyPDF2 extracted text preview: {text[:LOG_TRUNCATION_LENGTH]}...")
                
                if not text.strip():
                    raise ValueError("No text could be extracted from the PDF")
                    
                return text, title
            except Exception as e:
                logger.error(f"Error extracting text from PDF: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error processing PDF: {str(e)}")
            finally:
                os.unlink(temp_file.name)

    async def process_pdf(self, file: UploadFile) -> PDFResponse:
        """Process uploaded PDF file"""
        await self.validate_pdf_file(file)
        content = await file.read()
        
        try:
            # Extract text and title
            text, title = await self.extract_text_from_pdf(content)
            logger.info(f"Successfully extracted text from PDF: {title}")
            
            # Process the text and get both full text and chunks
            processed_text, chunks = await self.process_pdf_text(text)
            
            # Generate a unique topic key
            topic_key = f"pdf-{os.path.splitext(file.filename)[0]}-{hash(str(processed_text))%10000:04d}"
            
            return PDFResponse(
                success=True,
                topicKey=topic_key,
                display=os.path.splitext(file.filename)[0],
                text=processed_text,
                chunks=chunks
            )
        except Exception as e:
            logger.error(f"Error processing PDF: {str(e)}")
            return PDFResponse(
                success=False,
                topicKey="error",
                display="Error Processing PDF",
                text=str(e),
                chunks=[]
            )