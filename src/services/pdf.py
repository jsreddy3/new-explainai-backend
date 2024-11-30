from fastapi import UploadFile, File, HTTPException
import os
from typing import Optional, List
import litellm
from tempfile import NamedTemporaryFile
from pathlib import Path
from datetime import datetime
import time
from pydantic import BaseModel
from llama_parse import LlamaParse
import PyPDF2
import logging
import nest_asyncio

from src.core.config import Settings
from src.core.logging import setup_logger, log_with_context

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
            start_time = time.time()
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Process this document chunk (#{chunk_num}): {chunk}"}
            ]
            
            # logger.info(f"Processing chunk {chunk_num}", extra={
            #     "chunk_number": chunk_num,
            #     "chunk_length": len(chunk)
            # })
            # logger.debug(f"Chunk content (truncated): {chunk[:LOG_TRUNCATION_LENGTH]}")
            
            response = await litellm.acompletion(
                model="gemini/gemini-1.5-flash",
                messages=messages
            )
            
            process_time = time.time() - start_time
            processed_text = response.choices[0].message.content
            # logger.debug(f"Processed chunk {chunk_num} (truncated): {processed_text[:LOG_TRUNCATION_LENGTH]}", extra={
            #     "processing_time_seconds": process_time
            # })
            
            return self.clean_llm_output(processed_text)
        except Exception as e:
            logger.error(f"Error processing chunk {chunk_num}", extra={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "chunk_number": chunk_num
            })
            raise HTTPException(status_code=500, detail=f"Error processing document chunk: {str(e)}")

    def chunk_text(self, text: str) -> List[str]:
        """Split text into manageable chunks"""
        try:
            start_time = time.time()
            # logger.info(f"Splitting text of length {len(text)} into chunks of size {CHUNK_SIZE}")
            chunks = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
            split_time = time.time() - start_time
            # logger.info(f"Created {len(chunks)} chunks", extra={
            #     "chunk_count": len(chunks),
            #     "splitting_time_seconds": split_time
            # })
            # for i, chunk in enumerate(chunks):
            #     logger.debug(f"Chunk {i+1} length: {len(chunk)}", extra={
            #         "chunk_number": i+1,
            #         "chunk_length": len(chunk)
            #     })
            #     logger.debug(f"Chunk {i+1} preview: {chunk[:100]}...", extra={
            #         "chunk_number": i+1,
            #         "preview": chunk[:100] + "..."
            #     })
            return chunks
        except Exception as e:
            logger.error("Error splitting text", extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            raise

    async def process_pdf_text(self, text: str) -> tuple[str, List[str]]:
        """Process PDF text in chunks"""
        try:
            start_time = time.time()
            chunks = self.chunk_text(text)
            processed_chunks = []
            
            # logger.info(f"Starting to process {len(chunks)} chunks")
            for i, chunk in enumerate(chunks):
                # logger.info(f"Processing chunk {i+1}/{len(chunks)} (length: {len(chunk)})", extra={
                #     "chunk_number": i+1,
                #     "chunk_length": len(chunk)
                # })
                # logger.debug(f"Raw chunk {i+1}: {chunk[:LOG_TRUNCATION_LENGTH]}...", extra={
                #     "chunk_number": i+1,
                #     "raw_chunk": chunk[:LOG_TRUNCATION_LENGTH] + "..."
                # })
                processed_chunk = await self.process_chunk(chunk, i + 1)
                # logger.debug(f"Processed chunk {i+1}: {processed_chunk[:LOG_TRUNCATION_LENGTH]}", extra={
                #     "chunk_number": i+1,
                #     "processed_chunk": processed_chunk[:LOG_TRUNCATION_LENGTH] + "..."
                # })
                processed_chunks.append(processed_chunk)
            
            full_text = "\n".join(processed_chunks)
            total_time = time.time() - start_time
            # logger.info(f"Finished processing all chunks. Total processed text length: {len(full_text)}", extra={
            #     "total_chunks": len(chunks),
            #     "total_text_length": len(full_text),
            #     "total_processing_time_seconds": total_time,
            #     "average_chunk_time_seconds": total_time / len(chunks)
            # })
            return full_text, processed_chunks
        except Exception as e:
            logger.error("Error processing PDF text", extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            raise

    async def extract_text_from_pdf(self, content: bytes) -> tuple[str, str]:
        """Extract text and title from PDF using llama-parse with PyPDF2 fallback"""
        try:
            start_time = time.time()
            with NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                temp_file.write(content)
                temp_file.flush()
                
                try:
                    # logger.info(f"Starting PDF text extraction with LlamaParse", extra={
                    #     "file_path": temp_file.name
                    # })
                    
                    # First try with LlamaParse
                    extraction_start = time.time()
                    result = self.llama_parse.load_data(temp_file.name)
                    extraction_time = time.time() - extraction_start
                    
                    if not result or not result[0].text:
                        raise ValueError("Failed to extract text from PDF")
                    
                    text = result[0].text
                    title = result[0].metadata.get('title', '') if result else ""
                    
                    # Log the extraction results
                    # logger.info("LlamaParse extraction complete", extra={
                    #     "text_length": len(text),
                    #     "extraction_time_seconds": extraction_time
                    # })
                    
                    # Log text preview for debugging
                    # logger.debug("Extracted text preview", extra={
                    #     "preview": text[:200] + "..."
                    # })
                    
                    # logger.info("Successfully extracted text from PDF", extra={
                    #     "file_path": temp_file.name
                    # })
                    
                    return text, title
                except Exception as e:
                    logger.warning(f"LlamaParse extraction failed, falling back to PyPDF2", extra={
                        "error": str(e)
                    })
                    with open(temp_file.name, 'rb') as pdf_file:
                        pdf_reader = PyPDF2.PdfReader(pdf_file)
                        text = '\n'.join(page.extract_text() for page in pdf_reader.pages)
                        title = Path(temp_file.name).stem
                        
                        if not text.strip():
                            raise ValueError("No text could be extracted from the PDF")
                        
                        logger.info("PyPDF2 extraction complete", extra={
                            "text_length": len(text)
                        })
                        return text, title
                finally:
                    os.unlink(temp_file.name)
        except Exception as e:
            logger.error("Error extracting text from PDF", extra={
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            raise

    async def process_pdf(self, file: UploadFile) -> PDFResponse:
        """Process uploaded PDF file"""
        try:
            start_time = time.time()
            logger.info(f"Starting PDF processing for file: {file.filename}")
            
            await self.validate_pdf_file(file)
            # logger.info("PDF validation passed")
            
            content = await file.read()
            # logger.info(f"Read PDF content, size: {len(content)} bytes")
            
            # Extract text and title
            # logger.info("Extracting text from PDF...")
            text, title = await self.extract_text_from_pdf(content)
            # logger.info(f"Successfully extracted text from PDF: {title}", extra={
            #     "file_name": file.filename,
            #     "text_length": len(text),
            #     "text_preview": text[:200] if text else "No text"
            # })
            
            # Process the text and get both full text and chunks
            # logger.info("Processing extracted text...")
            processed_text, chunks = await self.process_pdf_text(text)
            # logger.info(f"Text processing complete. Generated {len(chunks)} chunks")
            
            # Generate a unique topic key
            topic_key = f"pdf-{os.path.splitext(file.filename)[0]}-{hash(str(processed_text))%10000:04d}"
            
            total_time = time.time() - start_time
            logger.info("Finished processing PDF", extra={
                "total_time_seconds": total_time,
                "topic_key": topic_key,
                "chunks_count": len(chunks)
            })
            
            return PDFResponse(
                success=True,
                topicKey=topic_key,
                display=os.path.splitext(file.filename)[0],
                text=processed_text,
                chunks=chunks
            )
        except Exception as e:
            logger.error("Error processing PDF", extra={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": str(e.__traceback__)
            })
            return PDFResponse(
                success=False,
                topicKey="error",
                display="Error Processing PDF",
                text=str(e),
                chunks=[]
            )