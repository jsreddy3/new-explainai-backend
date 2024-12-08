import pytest
import aiohttp
import os
import logging
from typing import Dict

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def upload_test_document() -> Dict:
    """Upload a test document and return the raw response data"""
    async with aiohttp.ClientSession() as session:
        test_pdf_path = os.path.join(os.path.dirname(__file__), 'pdfs', 'pale fire presentation.pdf')
        
        if not os.path.exists(test_pdf_path):
            raise FileNotFoundError(f"Test PDF not found at {test_pdf_path}")
        
        data = aiohttp.FormData()
        data.add_field('file',
                      open(test_pdf_path, 'rb'),
                      filename='pale fire presentation.pdf',
                      content_type='application/pdf')
        
        async with session.post(
            "http://localhost:8000/api/upload",
            data=data
        ) as response:
            response_data = await response.json()
            logger.info(f"Upload response status: {response.status}")
            logger.info(f"Upload response data: {response_data}")
            return response.status, response_data

@pytest.mark.asyncio
async def test_pdf_upload_basic():
    """Test basic PDF upload functionality"""
    # Upload document
    status, response_data = await upload_test_document()
    
    # Check response status
    assert status == 200, f"Upload failed with status {status}"
    
    # Check response structure
    assert "document_id" in response_data, "Response missing document_id"
    assert isinstance(response_data["document_id"], str), "document_id should be a string"
    
    # Check if we got processed text
    assert "current_chunk" in response_data, "Response missing current_chunk"
    assert "content" in response_data["current_chunk"], "Response missing chunk content"
    assert isinstance(response_data["current_chunk"]["content"], str), "chunk content should be a string"
    assert len(response_data["current_chunk"]["content"]) > 0, "chunk content should not be empty"

@pytest.mark.asyncio
async def test_pdf_processing_details():
    """Test detailed PDF processing results"""
    # Upload document
    status, response_data = await upload_test_document()
    assert status == 200, f"Upload failed with status {status}"
    
    # Get the first chunk's content
    chunk_content = response_data["current_chunk"]["content"]
    
    # Verify chunk structure
    assert "sequence" in response_data["current_chunk"], "Chunk missing sequence number"
    assert isinstance(response_data["current_chunk"]["sequence"], int), "sequence should be an integer"
    
    # Verify navigation structure
    assert "navigation" in response_data["current_chunk"], "Chunk missing navigation"
    nav = response_data["current_chunk"]["navigation"]
    assert "prev" in nav and "next" in nav, "Navigation missing prev/next pointers"
    
    # Log the processed content for manual verification
    logger.info("First chunk content preview:")
    logger.info(chunk_content[:200] + "..." if len(chunk_content) > 200 else chunk_content)
