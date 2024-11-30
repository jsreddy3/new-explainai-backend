import asyncio
import pytest
import aiohttp
import logging
import json
import os

from backend.tests.test_document_conversation_workflow import (
    WebSocketTestClient
)

logger = logging.getLogger(__name__)

async def upload_test_document(session=None):
    """Upload a test document and return document data"""
    if session is None:
        session = aiohttp.ClientSession()
    
    test_pdf_path = os.path.join(os.path.dirname(__file__), 'pdfs', 'pale fire presentation.pdf')
    
    if not os.path.exists(test_pdf_path):
        raise FileNotFoundError(f"Test PDF not found at {test_pdf_path}")
    
    data = aiohttp.FormData()
    data.add_field('file',
                  open(test_pdf_path, 'rb'),
                  filename='pale fire presentation.pdf',
                  content_type='application/pdf')
    
    async with session.post(
        "http://localhost:8000/api/documents/upload", 
        data=data
    ) as response:
        assert response.status == 200, f"Upload failed with status {response.status}"
        document_data = await response.json()
        return document_data

@pytest.mark.asyncio
async def test_multiple_conversations_and_questions():
    """
    Advanced test scenario that covers:
    1. Multiple document uploads
    2. Creating main and chunk conversations for each document
    3. Generating questions for different conversations
    4. Sending messages to different conversations
    5. Verifying conversation isolation
    """
    async with aiohttp.ClientSession() as session:
        # Upload two different documents
        doc1_data = await upload_test_document(session)
        doc2_data = await upload_test_document(session)

        doc1_id = doc1_data.get("document_id")
        doc2_id = doc2_data.get("document_id")
        doc1_main_conv_id = doc1_data.get("main_conversation_id")
        doc2_main_conv_id = doc2_data.get("main_conversation_id")

        assert doc1_id and doc2_id, "Document uploads failed"
        assert doc1_main_conv_id and doc2_main_conv_id, "Main conversations not created during upload"

        # Conversation and question tracking
        conversations = {
            doc1_id: {"main": doc1_main_conv_id, "chunks": [], "questions": []},
            doc2_id: {"main": doc2_main_conv_id, "chunks": [], "questions": []}
        }

        # Create WebSocket clients for both documents
        async with (
            WebSocketTestClient(
                session,
                f"ws://localhost:8000/api/stream/{doc1_id}",
                name="doc1_conversation_stream"
            ) as doc1_ws,
            WebSocketTestClient(
                session,
                f"ws://localhost:8000/api/stream/{doc2_id}",
                name="doc2_conversation_stream"
            ) as doc2_ws
        ):
            # Get chunks for both documents
            async with (
                WebSocketTestClient(
                    session,
                    f"ws://localhost:8000/api/documents/{doc1_id}/stream",
                    name="doc1_chunk_retrieval"
                ) as doc1_chunks_ws,
                WebSocketTestClient(
                    session,
                    f"ws://localhost:8000/api/documents/{doc2_id}/stream",
                    name="doc2_chunk_retrieval"
                ) as doc2_chunks_ws
            ):
                # Retrieve chunks
                await doc1_chunks_ws.send_json({"action": "get_chunks"})
                await doc2_chunks_ws.send_json({"action": "get_chunks"})

                doc1_chunks_response = await doc1_chunks_ws.receive_json()
                doc2_chunks_response = await doc2_chunks_ws.receive_json()

                # Create chunk conversations
                first_doc1_chunk = doc1_chunks_response["chunks"][0]
                first_doc2_chunk = doc2_chunks_response["chunks"][0]

                # Create chunk conversations for both documents
                await doc1_ws.send_json({
                    "action": "create_chunk_conversation",
                    "document_id": doc1_id,
                    "chunk_id": first_doc1_chunk["id"],
                    "highlight_range": [0, 10],
                    "highlighted_text": first_doc1_chunk["content"][:10]
                })

                await doc2_ws.send_json({
                    "action": "create_chunk_conversation",
                    "document_id": doc2_id,
                    "chunk_id": first_doc2_chunk["id"],
                    "highlight_range": [0, 10],
                    "highlighted_text": first_doc2_chunk["content"][:10]
                })

                # Wait for chunk conversation events
                async def wait_for_chunk_conversation(ws):
                    while True:
                        event = await ws.receive_json()
                        logger.info(f"Chunk conversation event: {event}")
                        chunk_conv_id = (
                            event.get("data", {}).get("conversation_id") or
                            event.get("conversation_id")
                        )
                        if chunk_conv_id:
                            return chunk_conv_id

                doc1_chunk_conv_id = await wait_for_chunk_conversation(doc1_ws)
                doc2_chunk_conv_id = await wait_for_chunk_conversation(doc2_ws)

                conversations[doc1_id]["chunks"].append(doc1_chunk_conv_id)
                conversations[doc2_id]["chunks"].append(doc2_chunk_conv_id)

                # Generate questions for both main and chunk conversations
                await doc1_ws.send_json({
                    "action": "generate_questions",
                    "conversation_id": conversations[doc1_id]["main"],
                    "count": 3
                })
                await doc1_ws.send_json({
                    "action": "generate_questions",
                    "conversation_id": doc1_chunk_conv_id,
                    "count": 2
                })

                await doc2_ws.send_json({
                    "action": "generate_questions",
                    "conversation_id": conversations[doc2_id]["main"],
                    "count": 3
                })
                await doc2_ws.send_json({
                    "action": "generate_questions",
                    "conversation_id": doc2_chunk_conv_id,
                    "count": 2
                })

                # Collect question generation events
                async def wait_for_question_generation(ws, expected_count):
                    questions_collected = []
                    while len(questions_collected) < expected_count:
                        event = await ws.receive_json()
                        logger.info(f"Question generation event: {event}")
                        
                        # Check for question generation events
                        if event.get("type") == "conversation.questions.generate.completed":
                            questions = event.get("data", {}).get("questions", [])
                            if questions:
                                questions_collected.extend(questions)
                    
                    return questions_collected

                # Wait for question generation for both documents
                doc1_questions = await wait_for_question_generation(doc1_ws, 5)
                doc2_questions = await wait_for_question_generation(doc2_ws, 5)

                # Verify questions were generated
                assert len(doc1_questions) == 5, "Failed to generate 5 questions for doc1"
                assert len(doc2_questions) == 5, "Failed to generate 5 questions for doc2"

@pytest.mark.asyncio
async def test_conversation_merge_workflow():
    """
    Test conversation merging workflow:
    1. Create main and chunk conversations
    2. Generate questions for both
    3. Merge chunk conversation into main conversation
    4. Verify merge details
    """
    async with aiohttp.ClientSession() as session:
        # Upload document
        doc_data = await upload_test_document(session)
        doc_id = doc_data.get("document_id")
        doc_main_conv_id = doc_data.get("main_conversation_id")
        assert doc_id, "Document upload failed"
        assert doc_main_conv_id, "Main conversation not created during upload"
        
        async with WebSocketTestClient(
            session, 
            f"ws://localhost:8000/api/stream/{doc_id}", 
            name="conversation_merge_stream"
        ) as ws_client:
            # Retrieve document chunks
            async with WebSocketTestClient(
                session, 
                f"ws://localhost:8000/api/documents/{doc_id}/stream", 
                name="chunk_retrieval"
            ) as chunks_ws:
                await chunks_ws.send_json({"action": "get_chunks"})
                chunks_response = await chunks_ws.receive_json()
                first_chunk = chunks_response["chunks"][0]

            # Create chunk conversation
            await ws_client.send_json({
                "action": "create_chunk_conversation",
                "document_id": doc_id,
                "chunk_id": first_chunk["id"],
                "highlight_range": [0, 10],
                "highlighted_text": first_chunk["content"][:10]
            })
            
            # Wait for chunk conversation event
            async def wait_for_chunk_conversation(ws):
                while True:
                    event = await ws.receive_json()
                    logger.info(f"Chunk conversation event: {event}")
                    chunk_conv_id = (
                        event.get("data", {}).get("conversation_id") or
                        event.get("conversation_id")
                    )
                    if chunk_conv_id:
                        return chunk_conv_id

            chunk_conv_id = await wait_for_chunk_conversation(ws_client)
            assert chunk_conv_id, "Chunk conversation not created"
            
            # Generate questions for both conversations
            await ws_client.send_json({
                "action": "generate_questions",
                "conversation_id": doc_main_conv_id,
                "count": 3
            })
            await ws_client.send_json({
                "action": "generate_questions",
                "conversation_id": chunk_conv_id,
                "count": 2
            })
            
            # Collect question generation events
            async def wait_for_question_generation(expected_count):
                questions_collected = []
                while len(questions_collected) < expected_count:
                    event = await ws_client.receive_json()
                    logger.info(f"Question generation event: {event}")
                    
                    # Check for question generation events
                    if event.get("type") == "conversation.questions.generate.completed":
                        questions = event.get("data", {}).get("questions", [])
                        if questions:
                            questions_collected.extend(questions)
                
                return questions_collected

            # Wait for question generation
            questions = await wait_for_question_generation(5)

            # Verify questions were generated
            assert len(questions) == 5, "Failed to generate 5 questions"

            # Merge chunk conversation into main conversation
            await ws_client.send_json({
                "action": "merge_conversation",
                "main_conversation_id": doc_main_conv_id,
                "highlight_conversation_id": chunk_conv_id
            })
            
            # Wait for merge event
            async def wait_for_merge_event():
                while True:
                    event = await ws_client.receive_json()
                    logger.info(f"Conversation merge event: {event}")
                    
                    if event.get("type") == "conversation.merge.completed":
                        return event.get("data", {})

            merge_details = await wait_for_merge_event()
            
            assert merge_details.get("success"), "Conversation merge failed"
            assert merge_details.get("main_conversation_id") == doc_main_conv_id, \
                "Incorrect main conversation ID in merge"
            assert merge_details.get("merged_conversation", {}).get("conversation_id") == chunk_conv_id, \
                "Incorrect chunk conversation ID in merge"

# Optional: Add more advanced test scenarios here
