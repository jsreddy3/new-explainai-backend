from fastapi import UploadFile, HTTPException
import base64
from typing import List, Tuple, Dict, Optional
import google.generativeai as genai
from pydantic import BaseModel
import time
import fitz  # PyMuPDF for page counting
from docx import Document
import io
import asyncio

from newspaper import Article
import validators
from urllib.parse import urlparse

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
PAGES_PER_UNIT = 1  # Process one page at a time

INPUT_TOKEN_RATES = {
    "gemini-1.5-flash": 0.075 / 1_000_000  # $0.075 per million tokens
}

OUTPUT_TOKEN_RATES = {
    "gemini-1.5-flash": 0.30 / 1_000_000  # $0.30 per million tokens
}

GEMINI_PROMPT = """Extract EVERY WORD of the main content, making sure to include ALL text from start to finish. Do not skip or omit any content.

Rules:
1. Include EVERY single paragraph and sentence, from the very first word to the very last word
2. Preserve the original formatting and structure exactly
3. Maintain all paragraph breaks and line formatting exactly as they appear
4. Ignore headers, footers, and metadata
5. Never truncate or skip any portion of the text
6. DO NOT use double dots for periods or ellipses. DO NOT USE "..", use single dot "."
7. Make sure the last paragraph is complete and not cut off
8. Output the entire text without any summarization or omission

Output the complete text, preserving everything from beginning to end."""

# ref: https://ai.google.dev/gemini-api/docs/tokens?lang=python
# GEMINI_PROMPT_TOKEN_COUNT = 160 # (for current prompt), would use genai.GenerativeModel("models/gemini-1.5-flash"); model.count_tokens(GEMINI_PROMPT);
# GEMINI_PDF_PAGE_TOKEN_COUNT = 258 # source: https://ai.google.dev/gemini-api/docs/document-processing?lang=python

GEMINI_MODEL = "gemini-1.5-flash"

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
        self.model = genai.GenerativeModel(GEMINI_MODEL)

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

    async def extract_text_from_file(self, content: bytes, file_ext: str) -> Tuple[str, List[str]]:
        """Extract text + chunks for .txt, .md, .docx. (PDF handled elsewhere)"""
        if file_ext in ('.txt', '.md'):
            text = content.decode('utf-8')
            chunks = self.chunk_text(text)
            return text, chunks
        
        elif file_ext == '.docx':
            doc = Document(io.BytesIO(content))
            text = '\n\n'.join(paragraph.text for paragraph in doc.paragraphs if paragraph.text.strip())
            chunks = self.chunk_text(text)
            return text, chunks
        
        return None, []  # For PDFs, we'll handle them separately

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

    async def process_page_unit(self, unit_content: bytes, unit_number: int) -> Tuple[str, int, int]:
        """Process a unit of pages with Gemini and return text and token counts"""
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
            
            # Get token counts from response metadata
            input_tokens = response.usage_metadata.prompt_token_count
            output_tokens = response.usage_metadata.candidates_token_count
            
            return response.text.strip(), input_tokens, output_tokens
        except Exception as e:
            logger.error(f"Error processing unit {unit_number}: {str(e)}")
            raise

    def calculate_gemini_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate total cost based on token counts."""
        input_cost = input_tokens * INPUT_TOKEN_RATES['gemini-1.5-flash']
        output_cost = output_tokens * OUTPUT_TOKEN_RATES['gemini-1.5-flash']
        return input_cost + output_cost

    def parse_page_range(self, page_range: Optional[str], total_pages: int) -> Tuple[int, int, str]:
        """Parse a page range string in format 'start-end' and return normalized page indices.
        
        Args:
            page_range: String in format 'start-end' (e.g., '1-10')
            total_pages: Total number of pages in the document
            
        Returns:
            Tuple of (start_page, end_page, actual_range) where actual_range reflects any adjustments made
        """
        actual_range = page_range
        
        if not page_range:
            # Default to first 8 pages if no range specified
            return 0, min(MAX_PAGES, total_pages), "1-8"
        
        try:
            start, end = map(int, page_range.split('-'))
            # Convert to 0-based indexing and handle bounds
            start = max(0, start - 1)  # Convert from 1-based to 0-based
            end = min(total_pages, end)  # Cap at total pages
            
            if start >= end:
                start = 0
                end = min(MAX_PAGES, total_pages)
                actual_range = "1-8"
            
            # Enforce maximum of 16 pages by limiting the range
            if end - start > 16:
                end = start + 16
                actual_range = f"{start + 1}-{end}"  # Convert back to 1-based for display
                logger.info(f"Range exceeded 16 pages, limiting to: {actual_range}")
            
            return start, end, actual_range
        except ValueError:
            # If format is invalid, default to first 8 pages
            return 0, min(MAX_PAGES, total_pages), "1-8"

    async def process_pdf_with_gemini(self, content: bytes, page_range: Optional[str] = None) -> Tuple[str, List[str], int, int, str]:
        """Process PDF content in parallel using page units."""
        pdf_doc = fitz.open(stream=content, filetype="pdf")
        total_pages = len(pdf_doc)
        
        # Get page range to process
        start_page, end_page, actual_range = self.parse_page_range(page_range, total_pages)
        pages_to_process = range(start_page, end_page)
        
        logger.info(f"Processing pages {start_page + 1} to {end_page} of {total_pages}")
        
        # Process selected pages
        tasks = []
        for page_num in pages_to_process:
            unit_content = self.create_page_unit(pdf_doc, page_num, 1)
            tasks.append(self.process_page_unit(unit_content, page_num))
        
        try:
            results = await asyncio.gather(*tasks)
            page_texts, input_tokens, output_tokens = zip(*results)
            
            # Filter out empty pages
            page_texts = [text for text in page_texts if text]
            
            # Combine text
            combined_text = '\n\n'.join(page_texts)
            total_input_tokens = sum(input_tokens)
            total_output_tokens = sum(output_tokens)
            
            return combined_text, list(page_texts), total_input_tokens, total_output_tokens, actual_range
        except Exception as e:
            pdf_doc.close()
            raise e
        finally:
            pdf_doc.close()

    async def process_pdf(self, file: UploadFile, user_id: str = None, page_range: Optional[str] = None) -> Tuple[PDFResponse, float, str]:
        """Process document file and return structured response"""
        try:
            await self.validate_file(file)
            file_ext = '.' + file.filename.lower().split('.')[-1]
            
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
                processed_text, chunks, input_tokens, output_tokens, actual_range = await self.process_pdf_with_gemini(
                    content,
                    page_range=page_range
                )
                cost = self.calculate_gemini_cost(input_tokens, output_tokens)
                logger.info(f"PDF processing cost: {cost}")
            else:
                processed_text, chunks = await self.extract_text_from_file(content, file_ext)
                cost = 0
                actual_range = None
            
            if not processed_text:
                raise HTTPException(status_code=400, detail=f"Could not extract text from {file_ext} file")

            assert len(processed_text) > MINIMUM_TEXT_LENGTH, "Extracted text is too short"
            
            # Update progress
            if user_id:
                tracking_key = f"{user_id}:{file.filename}"
                self.upload_progress[tracking_key]["processed"] = 1
            
            filename = file.filename.rsplit('.', 1)[0]
            
            return PDFResponse(
                success=True,
                topicKey=f"pdf-{filename}-{hash(processed_text)%10000:04d}",
                display=filename,
                text=processed_text,
                chunks=chunks
            ), cost, actual_range
        except Exception as e:
            logger.error(f"Error processing file: {str(e)}")
            if user_id:
                tracking_key = f"{user_id}:{file.filename}"
                if tracking_key in self.upload_progress:
                    del self.upload_progress[tracking_key]
            raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")
        finally:
            if user_id:
                tracking_key = f"{user_id}:{file.filename}"
                if tracking_key in self.upload_progress:
                    del self.upload_progress[tracking_key]

    def is_valid_url(self, url: str) -> bool:
        """Validate URL format and supported domains"""
        if not validators.url(url):
            return False
            
        parsed = urlparse(url)
        logger.info("URL Parse info: ", parsed)
        # Add any domain-specific validation if needed
        return True
    
    async def extract_web_content(self, url: str) -> str:
        """Extract content from a web URL using newspaper3k"""
        try:
            article = Article(url)
            article.download()
            article.parse()
            
            # Combine title and text with proper formatting
            content_parts = []
            title = None
            if article.title:
                title = article.title.strip()
                content_parts.append(title)
            if article.text:
                content_parts.append(article.text.strip())
                
            content = "\n\n".join(content_parts)
            
            if not content.strip():
                raise ValueError("No content could be extracted from URL")
                
            return content, title
            
        except Exception as e:
            logger.error(f"Error extracting content from URL {url}: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to extract content from URL: {str(e)}"
            )
    
    async def process_url(self, url: str) -> Tuple[PDFResponse, float]:
        """Process a URL and return structured response"""
        if not self.is_valid_url(url):
            raise HTTPException(status_code=400, detail="Invalid URL format")
            
        try:
            # Extract content from URL
            content, title = await self.extract_web_content(url)
            
            # Ensure minimum content length
            assert len(content) > MINIMUM_TEXT_LENGTH, "Extracted text is too short"
            
            # Create chunks from content
            chunks = self.chunk_text(content)

            if title:
                display = title
            else:
                parsed_url = urlparse(url)
                display = parsed_url.netloc + parsed_url.path
                if display.endswith('/'):
                    display = display[:-1]
                display = display.split('/')[-1] or parsed_url.netloc
            
            return PDFResponse(
                success=True,
                topicKey=f"url-{hash(url)%10000:04d}",
                display=display,
                text=content,
                chunks=chunks
            ), 0  # Cost is 0 for web extraction
            
        except Exception as e:
            logger.error(f"Error processing URL {url}: {str(e)}")
            raise