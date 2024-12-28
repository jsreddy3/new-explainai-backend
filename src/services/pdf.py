from fastapi import UploadFile, HTTPException
import fitz
from typing import List, Dict, Tuple
import litellm
import asyncio
from pydantic import BaseModel
from docx import Document
import io

import time

# Apply nest_asyncio to allow nested async loops
import nest_asyncio
from src.core.config import Settings
from src.core.logging import setup_logger
nest_asyncio.apply()

logger = setup_logger(__name__)
settings = Settings()

# Constants
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
CHUNK_SIZE = 2500  # Characters per chunk
MAX_CHUNKS = 16  # Maximum number of chunks to process

SYSTEM_PROMPT = """You are a precise text formatter. Your task is to clean up text extracted from PDFs while preserving the exact structure and wording of the original document.

Rules:
1. The input text has paragraphs separated by newlines. Preserve these exact paragraph breaks.
2. Fix any OCR or formatting errors:
   - Join hyphenated words that were split across lines
   - Fix misrecognized characters
   - Normalize spacing between words
3. Do not change paragraph organization or structure
4. Do not add or remove content
5. Do not add interpretations or summaries
6. Remove any repeated headers, footers, or page numbers
7. Preserve any intentional formatting like lists or titles

Treat input text that looks like this:

"First para-
graph with some text. More text 
here that continues.

Second paragraph starts here and con-
tinues on next line. M0re text with
OCR errors."

And output clean text like this:

"First paragraph with some text. More text here that continues.

Second paragraph starts here and continues on next line. More text with OCR errors."
"""

VLM_PROMPT = """You are a precise text extractor and formatter. Your task is to extract and output the text in the provided image, preserving the exact structure and wording of the original document.

Rules:
1. The input text has paragraphs separated by newlines. Preserve these exact paragraph breaks.
2. Fix any OCR or formatting errors:
   - Join hyphenated words that were split across lines
   - Fix misrecognized characters
   - Normalize spacing between words
3. Do not change paragraph organization or structure
4. Do not add or remove content
5. Do not add interpretations or summaries
6. Remove any repeated headers, footers, or page numbers
7. Preserve any intentional formatting like lists or titles

Then you must output clean text like this:

"First paragraph with some text. More text here that continues.

Second paragraph starts here and continues on next line. More text with OCR errors."
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
        self.upload_progress = {}  # {user_id:filename -> {total: int, processed: int}}

    async def validate_pdf_file(self, file: UploadFile) -> None:
        """Validate that the uploaded file is supported and within size limits"""
        allowed_extensions = {'.pdf', '.txt', '.docx', '.md'}
        file_ext = '.' + file.filename.lower().split('.')[-1]
        
        if file_ext not in allowed_extensions:
            raise HTTPException(status_code=400, detail=f"File must be one of: {', '.join(allowed_extensions)}")
        
        content = await file.read()
        await file.seek(0)  # Reset file pointer
        
        if len(content) > self.max_file_size:
            raise HTTPException(status_code=413, detail="File size too large. Maximum size is 10MB")

    def clean_llm_output(self, text: str) -> str:
        """Clean and standardize LLM output"""
        return text.strip()

    def _extract_paragraphs_with_mupdf(self, pdf_reader) -> List[str]:
        """Extract paragraphs using PyMuPDF layout analysis"""
        paragraphs = []
        current_para = []
        prev_y1 = None
        
        for page_num in range(len(pdf_reader)):
            page = pdf_reader[page_num]
            blocks = page.get_text("dict")["blocks"]
            
            for block in blocks:
                if "lines" not in block:
                    continue
                    
                for line in block["lines"]:
                    # Get text from spans
                    line_text = " ".join(span["text"] for span in line.get("spans", []))
                    y0 = line["bbox"][1]  # top of current line
                    
                    # Check if this is likely a new paragraph
                    if prev_y1 is not None:
                        gap = y0 - prev_y1
                        line_height = line["bbox"][3] - line["bbox"][1]

                        logger.info(f"Page {page_num}, Line Text: {line_text}, Gap: {gap}, Line height: {line_height}")
                        
                        # If gap is significantly larger than line height, it's likely a new paragraph
                        if gap > line_height * 0.7:
                            if current_para:
                                paragraphs.append(" ".join(current_para))
                                current_para = []
                    
                    current_para.append(line_text)
                    prev_y1 = line["bbox"][3]  # bottom of current line
            
            # Page break - force new paragraph
            if current_para:
                paragraphs.append(" ".join(current_para))
                current_para = []
                prev_y1 = None
        
        # Add final paragraph
        if current_para:
            paragraphs.append(" ".join(current_para))
        
        return paragraphs

    def chunk_paragraphs(self, paragraphs: List[str], max_size: int = CHUNK_SIZE) -> List[str]:
        """
        Create chunks that respect paragraph boundaries while maintaining size limits.
        If a single paragraph exceeds max_size, it will be split at sentence boundaries.
        """
        chunks = []
        current_chunk = []
        current_size = 0
        
        for para in paragraphs:
            para_size = len(para) + 2  # +2 for newlines

            logger.info(f"Para size: {para_size}, Current size: {current_size}, Potential: {current_size + para_size}, Max: {max_size}")
            logger.info(f"Para text: {para}...")
            
            # If this paragraph alone exceeds max size, split it
            if para_size > max_size:
                # Split into sentences and try to keep sentences together
                sentences = para.replace('. ', '.|').replace('! ', '!|').replace('? ', '?|').split('|')
                
                temp_chunk = []
                temp_size = 0
                
                for sentence in sentences:
                    sentence_size = len(sentence) + 1  # +1 for space
                    
                    if temp_size + sentence_size > max_size:
                        if temp_chunk:
                            chunks.append(' '.join(temp_chunk))
                        temp_chunk = [sentence]
                        temp_size = sentence_size
                    else:
                        temp_chunk.append(sentence)
                        temp_size += sentence_size
                
                if temp_chunk:
                    chunks.append(' '.join(temp_chunk))
                continue
            
            # If adding this paragraph exceeds max size, start new chunk
            if current_size + para_size > max_size and current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                logger.info(f"Creating chunk of size {current_size}")
                current_chunk = []
                current_size = 0
            
            current_chunk.append(para)
            current_size += para_size
        
        # Add final chunk
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))
        
        return chunks

    async def process_chunk(self, chunk: str, chunk_num: int, 
                          previous_messages: List[Dict[str, str]] = None) -> Tuple[str, List[Dict[str, str]], float]:
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
            
            return self.clean_llm_output(processed_text), messages, litellm.completion_cost(response)
        except Exception as e:
            logger.error(f"Error processing chunk {chunk_num}", extra={
                "error_type": type(e).__name__,
                "error_message": str(e),
                "chunk_number": chunk_num
            })
            return chunk, messages, 0  # Fallback to original text if processing fails

    async def process_pdf_text(self, chunks: List[str], user_id: str = None, filename: str = None) -> Tuple[str, List[str], float]:
        """Process PDF text chunks in parallel"""
        conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        # Initialize progress tracking
        if user_id and filename:
            tracking_key = f"{user_id}:{filename}"
            self.upload_progress[tracking_key] = {
                "total": len(chunks),
                "processed": 0
            }
        
        # Create tasks for all chunks to process them in parallel
        tasks = []
        for i, chunk in enumerate(chunks):
            tasks.append(self.process_chunk(chunk, i, conversation_history.copy()))
        
        try:
            # Process all chunks concurrently
            results = await asyncio.gather(*tasks)
            total_cost = 0
            
            # Unpack results and update progress
            processed_chunks = []
            for i, (processed_chunk, _, cost) in enumerate(results):
                processed_chunks.append(processed_chunk)
                if user_id and filename:
                    tracking_key = f"{user_id}:{filename}"
                    self.upload_progress[tracking_key]["processed"] = i + 1
                    total_cost += cost
            
        finally:
            # Cleanup tracking
            if user_id and filename:
                tracking_key = f"{user_id}:{filename}"
                if tracking_key in self.upload_progress:
                    del self.upload_progress[tracking_key]
        
        # Combine all processed chunks
        full_text = "\n\n".join(processed_chunks)
        return full_text, processed_chunks, total_cost

    def _extract_text_from_file(self, file_content: bytes, file_ext: str) -> List[str]:
        """Extract paragraphs from supported file types"""
        if file_ext in ['.txt', '.md']:
            text = file_content.decode('utf-8')
            # Split on double newlines to preserve paragraph structure
            return [p.strip() for p in text.split('\n\n') if p.strip()]
        
        elif file_ext == '.docx':
            doc = Document(io.BytesIO(file_content))
            # Extract paragraphs from docx
            return [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        
        elif file_ext == '.pdf':
            pdf_doc = fitz.open(stream=file_content, filetype="pdf")
            return self._extract_paragraphs_with_mupdf(pdf_doc)

    async def process_pdf(self, file: UploadFile, user_id: str = None) -> Tuple[PDFResponse, float]:
        """Process document file and return structured response with cost"""
        await self.validate_pdf_file(file)
        
        try:
            # Read file content
            file_content = await file.read()
            file_ext = '.' + file.filename.lower().split('.')[-1]
            
            # Extract paragraphs based on file type
            paragraphs = self._extract_text_from_file(file_content, file_ext)
            chunks = self.chunk_paragraphs(paragraphs)
            
            # Limit number of chunks
            if len(chunks) > MAX_CHUNKS:
                logger.info(f"Truncating document from {len(chunks)} chunks to {MAX_CHUNKS} chunks")
                chunks = chunks[:MAX_CHUNKS]
            
            # For text-based files, skip LLM processing
            if file_ext in ['.txt', '.docx', '.md']:
                processed_text = '\n\n'.join(chunks)
                cost = 0
            else:
                # Process PDF chunks through LLM
                processed_text, processed_chunks, cost = await self.process_pdf_text(chunks, user_id, file.filename)
                chunks = processed_chunks
            
            filename = file.filename.rsplit('.', 1)[0]
            
            return PDFResponse(
                success=True,
                topicKey=f"pdf-{filename}-{hash(processed_text)%10000:04d}",
                display=filename,
                text=processed_text,
                chunks=chunks
            ), cost
            
        except Exception as e:
            logger.error(f"Error processing document: {str(e)}")
            if user_id and file.filename:
                tracking_key = f"{user_id}:{file.filename}"
                if tracking_key in self.upload_progress:
                    del self.upload_progress[tracking_key]
            raise e