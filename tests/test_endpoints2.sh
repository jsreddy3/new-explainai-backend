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

# 1. Upload test document (needed for chunk tests)
echo "1. Uploading test document..."
UPLOAD_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/upload" \
  -H "accept: application/json" \
  -F "file=@tests/pdfs/pale fire presentation.pdf")

echo "Upload response: $UPLOAD_RESPONSE"
if handle_response "$UPLOAD_RESPONSE"; then
    DOCUMENT_ID=$(echo "$UPLOAD_RESPONSE" | jq -r '.document_id')
    echo "Document ID: $DOCUMENT_ID"
else
    exit 1
fi

# 2. Get document chunks
echo -e "\n2. Getting document chunks..."
DOC_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID" \
  -H "accept: application/json")

# Get first chunk ID
CHUNK_ID=$(echo "$DOC_RESPONSE" | jq -r '.chunks[0].id')
echo "First chunk ID: $CHUNK_ID"

# 3. Create chunk conversation
echo -e "\n3. Creating chunk conversation..."
CHUNK_CONV_RESPONSE=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations/chunk" \
  -H "Content-Type: application/json" \
  -d "{\"chunk_id\": \"$CHUNK_ID\", \"highlight_range\": [0, 100]}")

echo "Chunk conversation response: $CHUNK_CONV_RESPONSE"
if handle_response "$CHUNK_CONV_RESPONSE"; then
    CHUNK_CONV_ID=$(echo "$CHUNK_CONV_RESPONSE" | jq -r '.id')
    echo "Chunk conversation ID: $CHUNK_CONV_ID"
else
    exit 1
fi

# 4. Chat about specific chunk
echo -e "\n4. Starting chunk-specific chat..."
echo "First chunk message:"
curl -N -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "What does this section discuss?",
    "role": "user"
  }' | while read -r line; do
    if [[ $line == data:* ]]; then
        echo "${line#data: }"
    fi
done

# 5. Create another chunk conversation with different range
echo -e "\n5. Creating another chunk conversation with different range..."
CHUNK_CONV_RESPONSE2=$(curl -s -X POST "$BASE_URL/documents/$DOCUMENT_ID/conversations/chunk" \
  -H "Content-Type: application/json" \
  -d "{\"chunk_id\": \"$CHUNK_ID\", \"highlight_range\": [100, 200]}")

echo "Second chunk conversation response: $CHUNK_CONV_RESPONSE2"
if handle_response "$CHUNK_CONV_RESPONSE2"; then
    CHUNK_CONV_ID2=$(echo "$CHUNK_CONV_RESPONSE2" | jq -r '.id')
    echo "Second chunk conversation ID: $CHUNK_CONV_ID2"
else
    exit 1
fi

# 6. Chat about second range
echo -e "\n6. Starting second chunk chat..."
echo "Second chunk message:"
curl -N -X POST "$BASE_URL/conversations/$CHUNK_CONV_ID2/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "How does this part connect to the previous section?",
    "role": "user"
  }' | while read -r line; do
    if [[ $line == data:* ]]; then
        echo "${line#data: }"
    fi
done

# 7. List all conversations for document
echo -e "\n7. Getting all document conversations..."
CONVERSATIONS_RESPONSE=$(curl -s -X GET "$BASE_URL/documents/$DOCUMENT_ID/conversations" \
  -H "accept: application/json")

echo "All conversations:"
echo "$CONVERSATIONS_RESPONSE" | jq '.'