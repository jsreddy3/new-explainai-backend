from fastapi import UploadFile, HTTPException
import base64
from typing import List, Tuple, Dict
import google.generativeai as genai
from pydantic import BaseModel
import time
import fitz  # PyMuPDF for page counting
from docx import Document
import io
import asyncio

from src.core.config import Settings
from src.core.logging import setup_logger

logger = setup_logger(__name__)
settings = Settings()

# Constants
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_PAGES = 8  # Limit number of pages
CHUNK_SIZE = 2500  # Characters per chunk
MAX_CHUNKS = 16
MINIMUM_TEXT_LENGTH = 10
PAGES_PER_UNIT = 2  # Process 2 pages at a time

# Prompt optimized based on our testing
GEMINI_PROMPT = """Extract just the main content of this document, preserving its original formatting and structure. Ignore headers, footers, and metadata. Maintain all paragraph breaks and line formatting exactly as they appear in the content."""

class PDFResponse(BaseModel):
    success: bool
    topicKey: str
    display: str
    text: str
    chunks: List[str]

class PDFService:
    def __init__(self):
        self.max_file_size = MAX_FILE_SIZE
        self.upload_progress = {}  # {user_id:filename -> {total: int, processed: int}}
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel("gemini-1.5-flash")

    async def validate_file(self, file: UploadFile) -> None:
        """Validate that the uploaded file is supported and within size limits"""
        allowed_extensions = {'.pdf', '.txt', '.docx', '.md'}
        file_ext = '.' + file.filename.lower().split('.')[-1]
        
        if file_ext not in allowed_extensions:
            raise HTTPException(status_code=400, detail=f"File must be one of: {', '.join(allowed_extensions)}")
        
        content = await file.read()
        await file.seek(0)  # Reset file pointer
        
        if len(content) > self.max_file_size:
            raise HTTPException(status_code=413, detail="File size too large. Maximum size is 10MB")

    def extract_text_from_file(self, content: bytes, file_ext: str) -> str:
        """Extract text from supported file types"""
        if file_ext == '.txt' or file_ext == '.md':
            return content.decode('utf-8')
        
        elif file_ext == '.docx':
            doc = Document(io.BytesIO(content))
            return '\n\n'.join(paragraph.text for paragraph in doc.paragraphs if paragraph.text.strip())
        
        return None  # For PDFs, we'll handle them separately

    def chunk_text(self, text: str, chunk_size: int = CHUNK_SIZE) -> List[str]:
        """Split text into chunks, trying to preserve paragraph boundaries"""
        chunks = []
        paragraphs = text.split('\n\n')
        current_chunk = []
        current_size = 0
        
        for paragraph in paragraphs:
            # If single paragraph exceeds chunk size, split at sentence boundaries
            if len(paragraph) > chunk_size:
                sentences = paragraph.replace('. ', '.|').replace('! ', '!|').replace('? ', '?|').split('|')
                temp = ''
                for sentence in sentences:
                    if len(temp) + len(sentence) > chunk_size:
                        if temp:
                            chunks.append(temp.strip())
                        temp = sentence + '. '
                    else:
                        temp += sentence + '. '
                if temp:
                    chunks.append(temp.strip())
                continue

            # Check if adding paragraph would exceed chunk size
            if current_size + len(paragraph) > chunk_size:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = [paragraph]
                current_size = len(paragraph)
            else:
                current_chunk.append(paragraph)
                current_size += len(paragraph)

        # Add final chunk
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))

        return chunks[:MAX_CHUNKS]  # Limit number of chunks

    def create_page_unit(self, pdf_doc: fitz.Document, start_page: int, num_pages: int) -> bytes:
        """Create a PDF containing a specific range of pages"""
        new_pdf = fitz.open()
        for i in range(num_pages):
            if start_page + i < len(pdf_doc):
                new_pdf.insert_pdf(pdf_doc, from_page=start_page + i, to_page=start_page + i)
        content = new_pdf.write()
        new_pdf.close()
        return content

    async def process_page_unit(self, unit_content: bytes, unit_number: int) -> str:
        """Process a unit of pages with Gemini"""
        try:
            start_time = time.time()
            pdf_data = base64.b64encode(unit_content).decode('utf-8')
            response = await self.model.generate_content_async([
                {
                    'mime_type': 'application/pdf',
                    'data': pdf_data
                },
                GEMINI_PROMPT
            ])
            duration = time.time() - start_time
            logger.info(f"Unit {unit_number} processed in {duration:.3f}s")
            return response.text.strip()
        except Exception as e:
            logger.error(f"Error processing unit {unit_number}: {str(e)}")
            raise

    async def process_pdf_with_gemini(self, content: bytes) -> str:
        """Process PDF content in parallel using page units"""
        start_total = time.time()
        
        logger.info("Starting PDF parsing...")
        pdf_doc = fitz.open(stream=content, filetype="pdf")
        logger.info(f"PDF open time: {time.time() - start_total:.3f} s")
        
        total_pages = min(len(pdf_doc), MAX_PAGES)
        logger.info(f"Total pages (capped): {total_pages}")

        # Calculate number of units needed
        num_units = (total_pages + PAGES_PER_UNIT - 1) // PAGES_PER_UNIT
        logger.info(f"Number of units to process: {num_units}")

        # Create processing tasks for each unit
        start_page_units = time.time()
        tasks = []
        for i in range(num_units):
            start_page = i * PAGES_PER_UNIT
            unit_content = self.create_page_unit(
                pdf_doc, 
                start_page, 
                min(PAGES_PER_UNIT, total_pages - start_page)
            )
            tasks.append(self.process_page_unit(unit_content, i))
        logger.info(f"Time to create all page units: {time.time() - start_page_units:.3f} s")

        # Process all units in parallel
        logger.info("Starting to process all page units in parallel...")
        gather_start = time.time()
        try:
            results = await asyncio.gather(*tasks)
            gather_duration = time.time() - gather_start
            logger.info(f"asyncio.gather() took {gather_duration:.3f} s total")
        except Exception as e:
            pdf_doc.close()
            raise e

        pdf_doc.close()
        
        # Combine results, preserving order
        combine_start = time.time()
        combined_text = ' '.join(result for result in results if result)
        logger.info(f"Combining results took {time.time() - combine_start:.3f} s")

        total_duration = time.time() - start_total
        logger.info(f"Total process_pdf_with_gemini time: {total_duration:.3f} s")

        return combined_text

    async def process_pdf(self, file: UploadFile, user_id: str = None) -> Tuple[PDFResponse, float]:
        """Process document file and return structured response"""
        await self.validate_file(file)
        start_time = time.time()
        file_ext = '.' + file.filename.lower().split('.')[-1]
        
        try:
            # Read file content
            content = await file.read()
            
            # Initialize progress tracking
            if user_id:
                tracking_key = f"{user_id}:{file.filename}"
                self.upload_progress[tracking_key] = {
                    "total": 1,
                    "processed": 0
                }
            
            # Process based on file type
            if file_ext == '.pdf':
                processed_text = await self.process_pdf_with_gemini(content)
            else:
                processed_text = self.extract_text_from_file(content, file_ext)
            
            assert len(processed_text) > MINIMUM_TEXT_LENGTH, "Extracted text is too short"
            
            # Create chunks from processed text
            chunks = self.chunk_text(processed_text)
            
            # Update progress
            if user_id:
                tracking_key = f"{user_id}:{file.filename}"
                self.upload_progress[tracking_key]["processed"] = 1
            
            filename = file.filename.rsplit('.', 1)[0]
            cost = 0  # TODO: Calculate cost based on Gemini's pricing
            
            return PDFResponse(
                success=True,
                topicKey=f"pdf-{filename}-{hash(processed_text)%10000:04d}",
                display=filename,
                text=processed_text,
                chunks=chunks
            ), cost
            
        except Exception as e:
            logger.error(f"Error processing file: {str(e)}")
            if user_id:
                tracking_key = f"{user_id}:{file.filename}"
                if tracking_key in self.upload_progress:
                    del self.upload_progress[tracking_key]
            raise e
        finally:
            if user_id:
                tracking_key = f"{user_id}:{file.filename}"
                if tracking_key in self.upload_progress:
                    del self.upload_progress[tracking_key]