import pytest
import asyncio
from pathlib import Path
from fastapi import UploadFile
import time

@pytest.mark.asyncio
async def test_pdf_chunking_performance():
    from src.services.pdf import PDFService
    
    print("\n=== PDF Processing Performance Comparison Test ===")
    
    # Initialize services - one for sequential, one for parallel
    pdf_service_sequential = PDFService()
    pdf_service_parallel = PDFService()
    
    # Test parameters
    user_id = "test_user"
    pdf_path = Path("/Users/jaidenreddy/Downloads/levinthal.pdf")

    # Helper function for sequential processing
    async def process_sequentially(text, chunks):
        processed_chunks = []
        conversation_history = None
        start_time = time.time()
        
        for i, chunk in enumerate(chunks):
            processed_chunk, conversation_history = await pdf_service_sequential.process_chunk(
                chunk, i, conversation_history
            )
            processed_chunks.append(processed_chunk)
            
        return time.time() - start_time, processed_chunks

    with open(pdf_path, "rb") as pdf_file:
        upload_file = UploadFile(filename=pdf_path.name, file=pdf_file)
        
        # Extract text first
        text, _ = await pdf_service_sequential.extract_text_from_pdf(upload_file)
        chunks = pdf_service_sequential.chunk_text(text)
        
        print(f"\nNumber of chunks to process: {len(chunks)}")
        print(f"Average chunk size: {sum(len(c) for c in chunks)/len(chunks):.0f} characters")
        
        # Test sequential processing
        print("\n--- Sequential Processing ---")
        sequential_start = time.time()
        sequential_time, sequential_chunks = await process_sequentially(text, chunks)
        print(f"Sequential processing took: {sequential_time:.2f} seconds")
        
        # Reset file pointer
        pdf_file.seek(0)
        
        # Test parallel processing
        print("\n--- Parallel Processing ---")
        parallel_start = time.time()
        result = await pdf_service_parallel.process_pdf(upload_file, user_id)
        parallel_time = time.time() - parallel_start
        print(f"Parallel processing took: {parallel_time:.2f} seconds")
        
        # Calculate speedup
        speedup = sequential_time / parallel_time
        print(f"\nSpeedup from parallel processing: {speedup:.2f}x faster")
        
        # Verify results are equivalent
        assert len(sequential_chunks) == len(result.chunks), "Different number of chunks produced"
        print("\n=== Test completed successfully ===")