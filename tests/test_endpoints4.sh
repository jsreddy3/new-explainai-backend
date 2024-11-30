#!/bin/bash

# Function to check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "jq is required but not installed. Please install jq first."
    exit 1
fi

# Function to handle errors
handle_response() {
    if [[ $(echo "$1" | jq -r '.detail' 2>/dev/null) != "null" ]]; then
        echo "Error: $(echo "$1" | jq -r '.detail')"
        return 1
    fi
    return 0
}

# Base URL
BASE_URL="http://localhost:8000/api"

# 1. Setup: Upload document and create conversations
echo "1. Setting up test document and conversations..."
UPLOAD_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
    -H "accept: application/json" \
    -F "file=@tests/pdfs/pale fire presentation.pdf")

if handle_response "$UPLOAD_RESPONSE"; then
    DOCUMENT_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.document_id')
    echo "Document ID: $DOCUMENT_ID"
else
    exit 1
fi

# Create main conversation
MAIN_CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations" \
    -H "Content-Type: application/json" \
    -d '{}')

if handle_response "$MAIN_CONV_RESPONSE"; then
    MAIN_CONV_ID=$(echo "$MAIN_CONV_RESPONSE" | jq -r '.id')
    echo "Main conversation ID: $MAIN_CONV_ID"
else
    exit 1
fi

# Get first chunk for chunk-based tests
DOC_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID" \
    -H "accept: application/json")
CHUNK_ID=$(echo "$DOC_RESPONSE" | jq -r '.chunks[0].id')

# Create chunk conversation
CHUNK_CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations/chunk" \
    -H "Content-Type: application/json" \
    -d "{
        \"chunk_id\": \"$CHUNK_ID\", 
        \"highlight_range\": [0, 100],
        \"highlighted_text\": \"Sample highlighted text for testing\"
    }")

if handle_response "$CHUNK_CONV_RESPONSE"; then
    CHUNK_CONV_ID=$(echo "$CHUNK_CONV_RESPONSE" | jq -r '.id')
    echo "Chunk conversation ID: $CHUNK_CONV_ID"
else
    exit 1
fi

# 2. Test main conversation question generation
echo -e "\n2. Testing main conversation question generation..."

# Generate initial questions
echo "2.1 Generating initial questions..."
INITIAL_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 3,
        "conversation_type": "main",
        "previous_questions": []
    }')
echo "Initial questions response:"
echo "$INITIAL_QUESTIONS" | jq '.' || echo "Failed to parse response: $INITIAL_QUESTIONS"

# Add some conversation context
echo "2.2 Adding conversation context..."
MESSAGE_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/messages" \
    -H "Content-Type: application/json" \
    -d '{
        "content": "What is the main theme of this document?",
        "role": "user"
    }')
echo "$MESSAGE_RESPONSE" | jq '.' || echo "Failed to parse response: $MESSAGE_RESPONSE"

# Generate questions with conversation context
echo "2.3 Generating questions with conversation context..."
CONTEXT_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 3,
        "conversation_type": "main",
        "previous_questions": ["What is the main theme of this document?"]
    }')
echo "Questions with context response:"
echo "$CONTEXT_QUESTIONS" | jq '.' || echo "Failed to parse response: $CONTEXT_QUESTIONS"

# 3. Test chunk conversation question generation
echo -e "\n3. Testing chunk conversation question generation..."

# Generate chunk-specific questions
echo "3.1 Generating chunk-specific questions..."
CHUNK_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 3,
        "conversation_type": "chunk",
        "highlight_text": "Sample highlighted text for testing"
    }')
echo "Chunk questions response:"
echo "$CHUNK_QUESTIONS" | jq '.' || echo "Failed to parse response: $CHUNK_QUESTIONS"

# Add chunk conversation context
echo "3.2 Adding chunk conversation context..."
CHUNK_MESSAGE_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID/messages" \
    -H "Content-Type: application/json" \
    -d '{
        "content": "What does this highlighted section mean?",
        "role": "user"
    }')
echo "$CHUNK_MESSAGE_RESPONSE" | jq '.' || echo "Failed to parse response: $CHUNK_MESSAGE_RESPONSE"

# Generate chunk questions with context
echo "3.3 Generating chunk questions with context..."
CHUNK_CONTEXT_QUESTIONS=$(curl -s -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{
        "count": 3,
        "conversation_type": "chunk",
        "highlight_text": "Sample highlighted text for testing",
        "previous_questions": ["What does this highlighted section mean?"]
    }')
echo "Chunk questions with context response:"
echo "$CHUNK_CONTEXT_QUESTIONS" | jq '.' || echo "Failed to parse response: $CHUNK_CONTEXT_QUESTIONS"

# 4. Test error cases
echo -e "\n4. Testing error cases..."

# Invalid conversation ID
echo "4.1 Testing invalid conversation ID..."
INVALID_CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/invalid-id/questions" \
    -H "Content-Type: application/json" \
    -d '{"count": 3}')
echo "Invalid conversation response:"
echo "$INVALID_CONV_RESPONSE" | jq '.' || echo "Failed to parse response: $INVALID_CONV_RESPONSE"

# Invalid question count
echo "4.2 Testing invalid question count..."
INVALID_COUNT_RESPONSE=$(curl -s -X POST "$BASE_URL/conversations/$MAIN_CONV_ID/questions" \
    -H "Content-Type: application/json" \
    -d '{"count": -1}')
echo "Invalid count response:"
echo "$INVALID_COUNT_RESPONSE" | jq '.' || echo "Failed to parse response: $INVALID_COUNT_RESPONSE"